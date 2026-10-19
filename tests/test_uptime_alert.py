import importlib.util
import io
import json
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import yaml
from fastapi.testclient import TestClient

from lecturesift.app import app


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("uptime_alert", ROOT / "scripts/uptime_alert.py")
uptime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(uptime)

NOW = datetime(2026, 10, 19, 9, 5, tzinfo=timezone.utc)
RUN_URL = "https://github.com/example/lecturesift/actions/runs/1"
HOME_BODY = b'<html><body class="home-page"><h1 id="pageTitle">LectureSift</h1></body></html>'
HEALTH_BODY = json.dumps({"ok": True, "version": "4.1", "revision": "unknown"}).encode()


class Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status


def http_error(url, code):
    return HTTPError(url, code, "error", Message(), io.BytesIO(b""))


def site(responses):
    """Opener that serves queued outcomes per URL and records each request."""
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        outcome = responses[request.full_url].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    opener.calls = calls
    return opener


def healthy_site(repeat=1):
    return site({
        uptime.HOME_URL: [Response(HOME_BODY) for _ in range(repeat)],
        uptime.HEALTH_URL: [Response(HEALTH_BODY) for _ in range(repeat)],
    })


def test_workflow_runs_every_ten_minutes_with_minimal_token_and_no_overlap():
    workflow = yaml.safe_load((ROOT / ".github/workflows/uptime.yml").read_text(encoding="utf-8"))
    triggers = workflow[True]  # PyYAML reads the bare "on" key as a boolean.

    assert set(triggers) == {"schedule", "workflow_dispatch"}
    [schedule] = triggers["schedule"]
    minutes, *rest = schedule["cron"].split()
    assert rest == ["*", "*", "*", "*"]
    assert [int(minute) for minute in minutes.split(",")] == list(range(4, 60, 10))
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert workflow["concurrency"] == {"group": "uptime-alert", "cancel-in-progress": False}

    [job] = workflow["jobs"].values()
    assert job["timeout-minutes"] <= 6
    uses = [step["uses"] for step in job["steps"] if "uses" in step]
    assert uses == ["actions/checkout@v4"]
    assert job["steps"][0]["with"]["persist-credentials"] is False
    [run] = [step for step in job["steps"] if "run" in step]
    assert run["run"] == "python3 scripts/uptime_alert.py"
    assert run["env"] == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "REPOSITORY_OWNER": "${{ github.repository_owner }}",
    }
    assert "secrets." not in (ROOT / ".github/workflows/uptime.yml").read_text(encoding="utf-8")


def test_markers_match_the_real_homepage_and_health_response():
    homepage = (ROOT / "frontend/index.html").read_text(encoding="utf-8").encode()
    assert uptime.check_home(homepage) is None

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert uptime.check_health(response.content) is None


def test_health_rejects_wrong_shapes():
    assert uptime.check_health(b"<html>Bad gateway</html>") == "sağlık yanıtı JSON değil"
    assert uptime.check_health(b"[]") == "sağlık yanıtında ok=true yok"
    assert uptime.check_health(b'{"ok": "true", "version": "4.1"}') == "sağlık yanıtında ok=true yok"
    assert uptime.check_health(b'{"ok": true}') == "sağlık yanıtında sürüm bilgisi yok"
    assert uptime.check_home(b"<html>Netlify placeholder</html>") is not None


def test_healthy_targets_need_one_request_each_and_no_wait():
    opener, waits = healthy_site(), []

    results = uptime.check_targets(opener=opener, sleep=waits.append)

    assert [r["error"] for r in results] == [None, None]
    assert [r["attempts"] for r in results] == [1, 1]
    assert waits == []
    assert {timeout for _, timeout in opener.calls} == {uptime.TIMEOUT_SECONDS}


def test_single_blip_is_retried_and_does_not_count_as_down():
    opener = site({
        uptime.HOME_URL: [Response(HOME_BODY)],
        uptime.HEALTH_URL: [http_error(uptime.HEALTH_URL, 502), Response(HEALTH_BODY)],
    })
    waits = []

    results = uptime.check_targets(opener=opener, sleep=waits.append)

    assert uptime.state_of(results) == ""
    assert waits == [30]
    # Only the failing target is asked again.
    assert [url for url, _ in opener.calls] == [uptime.HOME_URL, uptime.HEALTH_URL, uptime.HEALTH_URL]


def test_confirmed_failure_reports_last_status_after_three_spaced_attempts():
    timeout = URLError(TimeoutError("timed out"))
    opener = site({
        uptime.HOME_URL: [Response(b"<html>maintenance</html>"), Response(b"", 503), http_error(uptime.HOME_URL, 503)],
        uptime.HEALTH_URL: [timeout, timeout, timeout],
    })
    waits = []

    results = uptime.check_targets(opener=opener, sleep=waits.append)

    assert waits == [30, 30]
    assert uptime.state_of(results) == "site,api"
    home, health = results
    assert (home["status"], home["attempts"]) == (503, 3)
    assert uptime.describe(home) == "Başarısız: HTTP 503 (HTTP 200 bekleniyordu)"
    assert uptime.describe(health) == "Başarısız: yanıt yok (zaman aşımı, 15 sn)"


def test_connection_errors_stay_on_one_table_cell():
    error = URLError(OSError("DNS | failure\nnext line"))
    status, reason = uptime.probe(uptime.TARGETS[1], site({uptime.HEALTH_URL: [error]}))
    assert status is None
    assert reason == "bağlantı hatası: DNS / failure next line"


class FakeGitHub:
    def __init__(self, issue=None):
        self.issue = issue
        self.calls = []

    def open_alert_issue(self):
        return self.issue

    def create_issue(self, title, body):
        self.calls.append(("create", title, body))

    def comment(self, number, body):
        self.calls.append(("comment", number, body))

    def update_issue(self, number, **fields):
        self.calls.append(("update", number, fields))


def results(**errors):
    return [
        {"key": t["key"], "name": t["name"], "url": t["url"], "attempts": 3 if t["key"] in errors else 1,
         "status": 503 if t["key"] in errors else 200, "error": errors.get(t["key"])}
        for t in uptime.TARGETS
    ]


def open_issue(state):
    return {"number": 7, "body": f"Original report\n\n<!-- lecturesift-uptime-state: {state} -->"}


def test_first_confirmed_failure_opens_one_turkish_issue_that_mentions_the_owner():
    github = FakeGitHub()

    action = uptime.reconcile(results(api="HTTP 200 bekleniyordu"), github, NOW, "owner-name", RUN_URL)

    assert action == "opened"
    [(kind, title, body)] = github.calls
    assert kind == "create"
    assert title == "Site erişilemiyor: https://api.lecturesift.com/health"
    assert body.startswith("@owner-name ")
    assert "2026-10-19 09:05:00 UTC (19.10.2026 12:05:00 Türkiye saati)" in body
    assert "| API sağlık kontrolü | https://api.lecturesift.com/health | Başarısız: HTTP 503 (HTTP 200 bekleniyordu) |" in body
    assert "| Ana sayfa | https://lecturesift.com/ | Başarılı (HTTP 200) |" in body
    assert RUN_URL in body
    assert body.endswith("<!-- lecturesift-uptime-state: api -->")


def test_same_failure_on_later_runs_adds_nothing():
    github = FakeGitHub(open_issue("api"))

    assert uptime.reconcile(results(api="x"), github, NOW, "owner-name", RUN_URL) == "unchanged"
    assert github.calls == []


def test_changed_failure_adds_one_comment_and_records_the_new_state():
    github = FakeGitHub(open_issue("api"))

    action = uptime.reconcile(results(site="x", api="x"), github, NOW, "owner-name", RUN_URL)

    assert action == "updated"
    [comment, update] = github.calls
    assert comment[:2] == ("comment", 7)
    assert comment[2].startswith("@owner-name Erişim durumu değişti.")
    assert "19.10.2026 12:05:00 Türkiye saati" in comment[2]
    assert update == ("update", 7, {
        "title": "Site erişilemiyor: https://lecturesift.com/, https://api.lecturesift.com/health",
        "body": "Original report\n\n<!-- lecturesift-uptime-state: site,api -->",
    })


def test_recovery_comments_with_time_and_closes_the_issue():
    github = FakeGitHub(open_issue("site,api"))

    assert uptime.reconcile(results(), github, NOW, "owner-name", RUN_URL) == "closed"

    [comment, update] = github.calls
    assert comment[:2] == ("comment", 7)
    assert comment[2].startswith(
        "Site yeniden erişilebilir. Zaman: 2026-10-19 09:05:00 UTC (19.10.2026 12:05:00 Türkiye saati)"
    )
    assert update == ("update", 7, {"state": "closed", "state_reason": "completed"})


def test_healthy_run_without_an_open_issue_does_nothing():
    github = FakeGitHub()
    assert uptime.reconcile(results(), github, NOW, "owner-name", RUN_URL) == "healthy"
    assert github.calls == []


def github_api(routes):
    """Opener for the REST client that answers by (method, path)."""
    calls = []

    def opener(request, timeout):
        path = request.full_url.removeprefix("https://api.github.com/repos/example/lecturesift")
        payload = json.loads(request.data) if request.data else None
        calls.append((request.get_method(), path, payload, request.get_header("Authorization")))
        outcome = routes[(request.get_method(), path)]
        if isinstance(outcome, Exception):
            raise outcome
        return Response(json.dumps(outcome).encode())

    opener.calls = calls
    return opener


def test_client_finds_only_its_own_open_outage_issue():
    marked = {"number": 9, "body": "x <!-- lecturesift-uptime-state: api -->"}
    opener = github_api({("GET", "/issues?labels=outage&state=open&per_page=100"): [
        {"number": 11, "body": "x <!-- lecturesift-uptime-state: api -->", "pull_request": {}},
        {"number": 10, "body": "Manually filed outage note", "labels": [{"name": "outage"}]},
        {"number": 8, "body": None},
        marked,
    ]})

    assert uptime.GitHub("example/lecturesift", "token-value", opener).open_alert_issue() == marked
    assert opener.calls[0][3] == "Bearer token-value"


def test_client_creates_missing_label_and_reapplies_a_dropped_label():
    opener = github_api({
        ("POST", "/labels"): http_error("labels", 422),
        ("POST", "/issues"): {"number": 12, "labels": []},
        ("POST", "/issues/12/labels"): [{"name": "outage"}],
    })

    uptime.GitHub("example/lecturesift", "token-value", opener).create_issue("Title", "Body")

    assert [(method, path) for method, path, *_ in opener.calls] == [
        ("POST", "/labels"), ("POST", "/issues"), ("POST", "/issues/12/labels"),
    ]
    assert opener.calls[1][2] == {"title": "Title", "body": "Body", "labels": ["outage"]}


def test_client_does_not_hide_other_label_errors():
    opener = github_api({("POST", "/labels"): http_error("labels", 403)})
    try:
        uptime.GitHub("example/lecturesift", "token-value", opener).create_issue("Title", "Body")
    except HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("A forbidden label request must fail the run")


def test_readme_explains_the_alert():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Uptime alert", 1)[1].split("\n## ", 1)[0]
    assert ".github/workflows/uptime.yml" in section
    assert "every 10 minutes" in section
    assert "60 days" in section
    assert "outage" in section
