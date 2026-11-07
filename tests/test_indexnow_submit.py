import importlib.util
import io
import json
import re
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
WORKFLOW = ROOT / ".github" / "workflows" / "indexnow.yml"
SPEC = importlib.util.spec_from_file_location(
    "indexnow_submit", ROOT / "scripts" / "indexnow_submit.py")
indexnow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(indexnow)

KEY = "0123456789abcdef0123456789abcdef"
KEY_URL = f"https://lecturesift.com/{KEY}.txt"
SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url><loc>https://lecturesift.com/</loc>
    <xhtml:link rel="alternate" hreflang="en" href="https://lecturesift.com/en/"/></url>
  <url><loc>https://lecturesift.com/en/</loc></url>
  <url><loc> https://lecturesift.com/plans </loc></url>
  <url><loc>https://lecturesift.com/</loc></url>
  <url><loc>http://lecturesift.com/insecure</loc></url>
  <url><loc>https://clever-horse-22b1a8.netlify.app/preview</loc></url>
</urlset>
"""


class Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"nope"):
    return HTTPError("https://example.test", code, "error", Message(), io.BytesIO(body))


def opener_with(outcomes):
    """Opener that serves the queued outcomes in order and records each request."""
    calls = []

    def opener(request, timeout):
        calls.append((request.get_method(), request.full_url, request.data, dict(request.header_items()), timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return opener, calls


def committed_key_file():
    matches = [path for path in FRONTEND.glob("*.txt") if re.fullmatch(r"[0-9a-f]{32}", path.stem)]
    assert len(matches) == 1, "exactly one IndexNow key file lives in frontend/"
    return matches[0]


def test_committed_key_file_is_public_hex_named_after_its_key():
    path = committed_key_file()
    assert path.read_text(encoding="utf-8").strip() == path.stem
    assert indexnow.read_key(path) == path.stem


def test_read_key_rejects_a_bad_key_or_a_misnamed_file(tmp_path):
    short = tmp_path / "abc.txt"
    short.write_text("abc\n", encoding="utf-8")
    with pytest.raises(ValueError, match="valid IndexNow key"):
        indexnow.read_key(short)
    misnamed = tmp_path / "key.txt"
    misnamed.write_text(f"{KEY}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=f"{KEY}.txt"):
        indexnow.read_key(misnamed)


def test_workflow_submits_the_committed_key_and_sitemap_after_frontend_pushes():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["push"]["branches"] == ["main"]
    assert "frontend/**" in triggers["push"]["paths"]
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read"}
    job, = workflow["jobs"].values()
    checkout, run_step = job["steps"]
    key_file = committed_key_file()
    for needed in ("scripts/indexnow_submit.py", "frontend/sitemap.xml", f"frontend/{key_file.name}"):
        assert needed in checkout["with"]["sparse-checkout"].split()
    command = " ".join(run_step["run"].split())
    assert command.startswith("python3 scripts/indexnow_submit.py ")
    assert "--sitemap frontend/sitemap.xml" in command
    assert f"--key-file frontend/{key_file.name}" in command
    assert '--revision "$GITHUB_SHA"' in command
    assert "--wait 600" in command
    assert "--dry-run" not in command


def test_sitemap_urls_keep_only_production_https_urls_once(tmp_path):
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(SITEMAP, encoding="utf-8")
    assert indexnow.sitemap_urls(sitemap, "lecturesift.com") == [
        "https://lecturesift.com/",
        "https://lecturesift.com/en/",
        "https://lecturesift.com/plans",
    ]
    with pytest.raises(ValueError, match="lists no https://other.test/ URLs"):
        indexnow.sitemap_urls(sitemap, "other.test")


def test_checked_in_sitemap_becomes_one_indexnow_payload():
    urls = indexnow.sitemap_urls(FRONTEND / "sitemap.xml", "lecturesift.com")
    assert "https://lecturesift.com/" in urls
    assert "https://lecturesift.com/en/" in urls
    assert len(urls) == len(set(urls)) > 100
    key = committed_key_file().stem
    payload = indexnow.build_payload("lecturesift.com", key, urls)
    assert payload == {
        "host": "lecturesift.com",
        "key": key,
        "keyLocation": f"https://lecturesift.com/{key}.txt",
        "urlList": urls,
    }
    with pytest.raises(ValueError, match="at most 10000 URLs"):
        indexnow.build_payload("lecturesift.com", key, ["https://lecturesift.com/"] * 10_001)


def test_wait_until_retries_through_errors_and_wrong_answers():
    opener, calls = opener_with([http_error(404), URLError("dns"), Response(b"stale-key\n"), Response(f"{KEY}\n".encode())])
    sleeps = []
    assert indexnow.wait_until(lambda: indexnow.key_is_live(KEY_URL, KEY, opener), 600, sleep=sleeps.append, clock=lambda: 0.0)
    assert sleeps == [indexnow.POLL_SECONDS] * 3
    assert [call[0] for call in calls] == ["GET"] * 4
    assert {call[1] for call in calls} == {KEY_URL}


def test_wait_until_gives_up_at_the_deadline():
    opener, calls = opener_with([http_error(404), http_error(404)])
    ticks = iter([100.0, 700.0])
    assert not indexnow.wait_until(lambda: indexnow.key_is_live(KEY_URL, KEY, opener), 600, sleep=lambda seconds: None, clock=lambda: next(ticks))
    assert len(calls) == 2
    opener, calls = opener_with([http_error(404)])
    assert not indexnow.wait_until(lambda: indexnow.key_is_live(KEY_URL, KEY, opener), 0, sleep=lambda seconds: None, clock=lambda: 0.0)
    assert len(calls) == 1


def deploys(*items):
    return Response(json.dumps(list(items)).encode())


def test_deploy_is_live_only_for_a_ready_published_production_deploy_of_the_revision():
    revision = "b" * 40
    live = {"context": "production", "state": "ready", "published_at": "2026-11-07T07:30:00Z", "commit_ref": revision}
    opener, calls = opener_with([deploys(
        {"context": "deploy-preview", "state": "ready", "published_at": None, "commit_ref": revision},
        {"context": "production", "state": "building", "published_at": None, "commit_ref": revision},
        {"context": "production", "state": "ready", "published_at": "2026-11-06T21:05:00Z", "commit_ref": "a" * 40},
    )])
    assert not indexnow.deploy_is_live(indexnow.DEPLOYS_URL, revision, opener)
    assert calls[0][0] == "GET" and calls[0][1] == indexnow.DEPLOYS_URL
    opener, _ = opener_with([deploys({"context": "production", "state": "ready", "published_at": "2026-11-06T21:05:00Z", "commit_ref": "a" * 40}, live)])
    assert indexnow.deploy_is_live(indexnow.DEPLOYS_URL, revision, opener)
    opener, _ = opener_with([http_error(503)])
    assert not indexnow.deploy_is_live(indexnow.DEPLOYS_URL, revision, opener)
    opener, _ = opener_with([Response(b'{"error": "not a list"}')])
    with pytest.raises(ValueError):
        indexnow.deploy_is_live(indexnow.DEPLOYS_URL, revision, opener)
    opener, _ = opener_with([Response(b"<html>")])
    with pytest.raises(ValueError):
        indexnow.deploy_is_live(indexnow.DEPLOYS_URL, revision, opener)


def test_workflow_deploy_url_is_the_delivery_checks_public_metadata():
    delivery = (ROOT / "scripts" / "check_frontend_delivery.py").read_text(encoding="utf-8")
    assert "3af04a22-7a31-44c2-8918-a79843f7ac82/deploys" in delivery
    assert indexnow.DEPLOYS_URL.startswith("https://api.netlify.com/api/v1/sites/3af04a22-7a31-44c2-8918-a79843f7ac82/deploys")


def test_submit_posts_the_json_payload_and_returns_the_status():
    payload = indexnow.build_payload("lecturesift.com", KEY, ["https://lecturesift.com/"])
    for code in (200, 202):
        opener, calls = opener_with([Response(b"", code)])
        assert indexnow.submit(payload, "https://api.indexnow.org/IndexNow", opener=opener) == (code, "")
        method, url, data, headers, timeout = calls[0]
        assert (method, url) == ("POST", "https://api.indexnow.org/IndexNow")
        assert json.loads(data) == payload
        assert headers["Content-type"] == "application/json; charset=utf-8"
    opener, _ = opener_with([http_error(403, b"Invalid key")])
    assert indexnow.submit(payload, "https://api.indexnow.org/IndexNow", opener=opener) == (403, "Invalid key")


def test_main_dry_run_prints_the_payload_and_touches_no_network(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(indexnow, "urlopen", lambda *args, **kwargs: pytest.fail("network used in dry run"))
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(SITEMAP, encoding="utf-8")
    key_file = tmp_path / f"{KEY}.txt"
    key_file.write_text(f"{KEY}\n", encoding="utf-8")
    assert indexnow.main(["--sitemap", str(sitemap), "--key-file", str(key_file), "--dry-run"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["keyLocation"] == KEY_URL
    assert printed["urlList"] == ["https://lecturesift.com/", "https://lecturesift.com/en/", "https://lecturesift.com/plans"]


def test_main_submits_only_after_the_revision_is_live_with_the_key(tmp_path, capsys, monkeypatch):
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(SITEMAP, encoding="utf-8")
    key_file = tmp_path / f"{KEY}.txt"
    key_file.write_text(f"{KEY}\n", encoding="utf-8")
    revision = "c" * 40
    live = {"context": "production", "state": "ready", "published_at": "2026-11-07T07:30:00Z", "commit_ref": revision}
    arguments = ["--sitemap", str(sitemap), "--key-file", str(key_file), "--endpoint", "https://indexnow.test/submit",
                 "--revision", revision, "--deploys-url", "https://deploys.test/list"]
    monkeypatch.setattr(indexnow, "_sleep", lambda seconds: None)

    opener, calls = opener_with([deploys()])
    monkeypatch.setattr(indexnow, "urlopen", opener)
    assert indexnow.main(arguments) == 1
    assert "is not the published production deploy yet" in capsys.readouterr().err
    assert [(call[0], call[1]) for call in calls] == [("GET", "https://deploys.test/list")]

    opener, calls = opener_with([deploys(live), http_error(404)])
    monkeypatch.setattr(indexnow, "urlopen", opener)
    assert indexnow.main(arguments) == 1
    assert "does not serve the committed key yet" in capsys.readouterr().err
    assert [call[0] for call in calls] == ["GET", "GET"]

    opener, calls = opener_with([deploys(live), Response(f"{KEY}\n".encode()), Response(b"", 202)])
    monkeypatch.setattr(indexnow, "urlopen", opener)
    assert indexnow.main(arguments) == 0
    assert f"IndexNow answered 202 for 3 URLs on lecturesift.com at {revision[:7]}" in capsys.readouterr().out
    assert [(call[0], call[1]) for call in calls] == [
        ("GET", "https://deploys.test/list"), ("GET", KEY_URL), ("POST", "https://indexnow.test/submit")]

    opener, calls = opener_with([deploys(live), Response(f"{KEY}\n".encode()), http_error(429, b"Too Many Requests")])
    monkeypatch.setattr(indexnow, "urlopen", opener)
    assert indexnow.main(arguments) == 1
    assert "429" in capsys.readouterr().out


def test_main_waits_for_the_deploy_within_one_shared_deadline(tmp_path, capsys, monkeypatch):
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(SITEMAP, encoding="utf-8")
    key_file = tmp_path / f"{KEY}.txt"
    key_file.write_text(f"{KEY}\n", encoding="utf-8")
    revision = "d" * 40
    live = {"context": "production", "state": "ready", "published_at": "2026-11-07T07:30:00Z", "commit_ref": revision}
    sleeps = []
    monkeypatch.setattr(indexnow, "_sleep", sleeps.append)
    ticks = iter([0.0, 10.0, 20.0, 30.0, 40.0])
    monkeypatch.setattr(indexnow, "_clock", lambda: next(ticks))
    opener, calls = opener_with([deploys(), http_error(503), deploys(live), Response(f"{KEY}\n".encode()), Response(b"", 200)])
    monkeypatch.setattr(indexnow, "urlopen", opener)
    assert indexnow.main(["--sitemap", str(sitemap), "--key-file", str(key_file), "--endpoint", "https://indexnow.test/submit",
                          "--revision", revision, "--deploys-url", "https://deploys.test/list", "--wait", "600"]) == 0
    assert sleeps == [indexnow.POLL_SECONDS] * 2
    assert [call[0] for call in calls] == ["GET", "GET", "GET", "GET", "POST"]
    assert "IndexNow answered 200" in capsys.readouterr().out
