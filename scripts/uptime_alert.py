"""Outside-in availability alert for the public site and API.

Runs from the scheduled "Uptime alert" GitHub Actions workflow. A target only
counts as down after every spaced attempt failed, so one network blip does not
alert. The outcome is kept in one open GitHub issue labelled "outage": it is
opened on the first confirmed failure, commented on only when the set of
failing targets changes, and closed once every target answers again.

Standard library only. Uses the workflow's GITHUB_TOKEN and nothing else.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HOME_URL = "https://lecturesift.com/"
HEALTH_URL = "https://api.lecturesift.com/health"
# Rendered by the homepage hero; the browser smoke test relies on it as well.
HOME_MARKER = b'id="pageTitle"'
ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30
TIMEOUT_SECONDS = 15
MAX_BYTES = 2_000_000
LABEL = "outage"
USER_AGENT = "LectureSift-Uptime-Alert/1.0"
GITHUB_API = "https://api.github.com"
STATE_MARKER = re.compile(r"<!-- lecturesift-uptime-state: ([a-z,]*) -->")
# Turkey has kept UTC+3 all year since 2016, so no tz database is needed.
TURKEY = timezone(timedelta(hours=3))


def check_home(body):
    if HOME_MARKER not in body:
        return "ana sayfa içeriği beklenen işareti içermiyor"
    return None


def check_health(body):
    try:
        payload = json.loads(body)
    except ValueError:
        return "sağlık yanıtı JSON değil"
    # Shape of lecturesift.app.health(); maintenance modes still answer it.
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return "sağlık yanıtında ok=true yok"
    if not isinstance(payload.get("version"), str) or not payload["version"]:
        return "sağlık yanıtında sürüm bilgisi yok"
    return None


TARGETS = (
    {"key": "site", "name": "Ana sayfa", "url": HOME_URL, "check": check_home},
    {"key": "api", "name": "API sağlık kontrolü", "url": HEALTH_URL, "check": check_health},
)


def probe(target, opener=urlopen, timeout=TIMEOUT_SECONDS):
    """Return (HTTP status or None, Turkish failure reason or None)."""
    request = Request(target["url"], headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    })
    try:
        with opener(request, timeout=timeout) as response:
            status = response.status
            body = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        error.close()
        return error.code, "HTTP 200 bekleniyordu"
    except (URLError, OSError, HTTPException) as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, TimeoutError):
            return None, f"zaman aşımı, {timeout} sn"
        return None, clean(f"bağlantı hatası: {str(reason) or type(reason).__name__}")
    if status != 200:
        return status, "HTTP 200 bekleniyordu"
    if len(body) > MAX_BYTES:
        return status, "yanıt beklenenden büyük"
    return status, target["check"](body)


def check_targets(targets=TARGETS, opener=urlopen, sleep=time.sleep,
                  attempts=ATTEMPTS, delay=RETRY_DELAY_SECONDS):
    """Retry only the failing targets, spaced by `delay` seconds."""
    outcomes = {}
    pending = list(targets)
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            sleep(delay)
        failing = []
        for target in pending:
            status, error = probe(target, opener)
            outcomes[target["key"]] = {"status": status, "error": error, "attempts": attempt}
            if error:
                failing.append(target)
        pending = failing
        if not pending:
            break
    return [
        {"key": t["key"], "name": t["name"], "url": t["url"], **outcomes[t["key"]]}
        for t in targets
    ]


def clean(text, limit=200):
    """Keep provider error text on one Markdown table cell."""
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe(result):
    status = f"HTTP {result['status']}" if result["status"] is not None else "yanıt yok"
    if not result["error"]:
        return f"Başarılı ({status})"
    return f"Başarısız: {status} ({result['error']})"


def format_time(now):
    utc = now.astimezone(timezone.utc)
    turkey = now.astimezone(TURKEY)
    return f"{utc:%Y-%m-%d %H:%M:%S} UTC ({turkey:%d.%m.%Y %H:%M:%S} Türkiye saati)"


def state_of(results):
    return ",".join(r["key"] for r in results if r["error"])


def state_marker(state):
    return f"<!-- lecturesift-uptime-state: {state} -->"


def issue_title(results):
    return "Site erişilemiyor: " + ", ".join(r["url"] for r in results if r["error"])


def result_table(results):
    rows = [f"| {r['name']} | {r['url']} | {describe(r)} |" for r in results]
    return "\n".join(["| Kontrol | Adres | Sonuç |", "| --- | --- | --- |", *rows])


def details(now, run_url):
    return (
        f"- Zaman: {format_time(now)}\n"
        f"- Başarısız adresler yaklaşık {RETRY_DELAY_SECONDS} sn arayla "
        f"{ATTEMPTS} kez denendi.\n"
        f"- Çalıştırma kaydı: {run_url}"
    )


def issue_body(results, now, owner, run_url):
    return (
        f"@{owner} LectureSift dışarıdan yapılan erişim kontrolünde yanıt vermedi.\n\n"
        f"{result_table(results)}\n\n{details(now, run_url)}\n\n"
        "Arıza durumu değişirse bu kayda yorum eklenir. Her şey yeniden erişilebilir "
        "olduğunda kayıt otomatik olarak kapanır.\n\n"
        f"{state_marker(state_of(results))}"
    )


def change_comment(results, now, owner, run_url):
    return (
        f"@{owner} Erişim durumu değişti.\n\n"
        f"{result_table(results)}\n\n{details(now, run_url)}"
    )


def recovery_comment(results, now, run_url):
    return (
        f"Site yeniden erişilebilir. Zaman: {format_time(now)}\n\n"
        f"{result_table(results)}\n\n- Çalıştırma kaydı: {run_url}"
    )


class GitHub:
    def __init__(self, repository, token, opener=urlopen, api=GITHUB_API):
        self.base = f"{api}/repos/{repository}"
        self.token = token
        self.opener = opener

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        })
        with self.opener(request, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def open_alert_issue(self):
        issues = self.request("GET", f"/issues?labels={LABEL}&state=open&per_page=100")
        for issue in issues:
            if "pull_request" not in issue and STATE_MARKER.search(issue.get("body") or ""):
                return issue
        return None

    def create_issue(self, title, body):
        try:
            self.request("POST", "/labels", {
                "name": LABEL, "color": "b60205",
                "description": "Otomatik erişim alarmı (Uptime alert iş akışı)",
            })
        except HTTPError as error:
            error.close()
            if error.code != 422:  # 422: the label already exists.
                raise
        issue = self.request("POST", "/issues", {"title": title, "body": body, "labels": [LABEL]})
        # GitHub silently drops labels it will not set. Without the label the
        # next run could not find this issue and would open another one.
        if LABEL not in {label.get("name") for label in issue.get("labels", [])}:
            self.request("POST", f"/issues/{issue['number']}/labels", {"labels": [LABEL]})
        return issue

    def comment(self, number, body):
        self.request("POST", f"/issues/{number}/comments", {"body": body})

    def update_issue(self, number, **fields):
        self.request("PATCH", f"/issues/{number}", fields)


def reconcile(results, github, now, owner, run_url):
    """Bring the single open outage issue in line with `results`."""
    state = state_of(results)
    issue = github.open_alert_issue()
    if state:
        if issue is None:
            github.create_issue(issue_title(results), issue_body(results, now, owner, run_url))
            return "opened"
        if STATE_MARKER.search(issue["body"]).group(1) == state:
            return "unchanged"
        github.comment(issue["number"], change_comment(results, now, owner, run_url))
        body = STATE_MARKER.sub(state_marker(state), issue["body"], count=1)
        github.update_issue(issue["number"], title=issue_title(results), body=body)
        return "updated"
    if issue is None:
        return "healthy"
    github.comment(issue["number"], recovery_comment(results, now, run_url))
    github.update_issue(issue["number"], state="closed", state_reason="completed")
    return "closed"


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    owner = os.environ.get("REPOSITORY_OWNER") or repository.split("/", 1)[0]
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{repository}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    results = check_targets()
    now = datetime.now(timezone.utc)
    for result in results:
        print(f"{result['url']}: {describe(result)} after {result['attempts']} attempt(s)")
    action = reconcile(results, GitHub(repository, os.environ["GITHUB_TOKEN"]), now, owner, run_url)
    print(f"Outage issue: {action}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"{result_table(results)}\n\nOutage issue: {action}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
