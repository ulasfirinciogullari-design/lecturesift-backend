"""Bounded, read-only checks of the currently published public frontend.

No credentials, browser, analytics execution, uploads, or automatic retries.
Each observation is retained, including a failure followed by a success.
"""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ORIGINS = (
    "https://lecturesift.com",
    "https://clever-horse-22b1a8.netlify.app",
)
PATHS = (
    "/", "/about", "/document-summary", "/cornell-notes", "/en/cornell-notes",
    "/favicon.svg", "/robots.txt", "/sitemap.xml", "/analytics.js",
    "/assets/study/cornell-notes-tr.txt",
)
DEPLOYS_URL = (
    "https://api.netlify.com/api/v1/sites/"
    "3af04a22-7a31-44c2-8918-a79843f7ac82/deploys?per_page=10"
)
MAX_BYTES = 2_000_000


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (f"{target.scheme}://{target.netloc}" not in ORIGINS
                or target.username or target.password):
            raise URLError("Redirect outside the two public frontend origins")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RuntimeAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        source = dict(attrs).get("src", "")
        parsed = urlsplit(source)
        if (tag == "script" and not parsed.scheme and not parsed.netloc
                and parsed.path.startswith("/assets/i18n/")
                and parsed.path.endswith(".js") and not parsed.query
                and not parsed.fragment and ".." not in parsed.path):
            self.paths.add(parsed.path)


def probe(url, opener, timeout=10):
    started = time.monotonic()
    row = {"url": url, "observed_at": datetime.now(timezone.utc).isoformat()}
    body = b""
    try:
        request = Request(url, headers={
            "User-Agent": "LectureSift-Delivery-Check/1.0",
            "Accept-Encoding": "identity",
        })
        try:
            response = opener(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(MAX_BYTES + 1)
            row.update(status=response.code, final_url=response.geturl(), bytes=len(body))
            for name in ("server", "x-nf-request-id", "cache-status", "content-type"):
                row[name] = response.headers.get(name, "")[:300]
            row["body_sha256"] = hashlib.sha256(body).hexdigest()
            row["ok"] = response.code == 200 and 0 < len(body) <= MAX_BYTES
            if len(body) > MAX_BYTES:
                row["error"] = "Response exceeded the inspection limit"
    except (OSError, URLError, ValueError) as error:
        row.update(status=None, ok=False, error=type(error).__name__)
    row["duration_ms"] = round((time.monotonic() - started) * 1000)
    return row, body


def check_delivery(opener, rounds=2):
    observations = []
    metadata, body = probe(DEPLOYS_URL, opener)
    deployment = None
    if metadata["ok"]:
        try:
            deploys = json.loads(body)
            published = [item for item in deploys if item.get("context") == "production"
                         and item.get("state") == "ready" and item.get("published_at")]
            if published:
                current = max(published, key=lambda item: item["published_at"])
                deployment = {key: current.get(key) for key in (
                    "id", "commit_ref", "state", "published_at",
                )}
        except (ValueError, TypeError, AttributeError):
            metadata["ok"] = False
            metadata["error"] = "Invalid public deployment metadata"

    assets = {origin: set() for origin in ORIGINS}
    for round_number in range(1, rounds + 1):
        for origin in ORIGINS:
            for path in PATHS:
                row, body = probe(origin + path, opener)
                row["round"] = round_number
                observations.append(row)
                if path == "/document-summary" and row["ok"]:
                    parser = RuntimeAssets()
                    parser.feed(body.decode("utf-8", errors="replace"))
                    # This page has exactly one public language runtime bundle.
                    if len(parser.paths) != 1:
                        row.update(ok=False, error="Expected one language runtime asset")
                    else:
                        assets[origin].update(parser.paths)
            for path in sorted(assets[origin])[:2]:
                row, _ = probe(urljoin(origin, path), opener)
                row["round"] = round_number
                observations.append(row)

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Public HTTP delivery sample; not browser, revenue, or availability SLA proof",
        "deployment": deployment,
        "deployment_metadata": metadata,
        "delivery_ok": all(row["ok"] for row in observations),
        "observations": observations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check_delivery(build_opener(PublicRedirects()).open)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Published deployment:", json.dumps(report["deployment"]))
    for row in report["observations"]:
        print(json.dumps(row))
    failed = sum(not row["ok"] for row in report["observations"])
    print(f"{len(report['observations'])} observations; {failed} failed. No retries hidden.")
    return 0 if report["delivery_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
