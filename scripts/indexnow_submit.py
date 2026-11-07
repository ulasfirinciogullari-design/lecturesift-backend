#!/usr/bin/env python3
"""Submit the public sitemap URLs to IndexNow.

IndexNow is the free, account-less way to tell Bing, Yandex, Seznam and Naver
which public pages exist or changed; Google ignores it and keeps reading the
sitemap. The engines verify the site by fetching a key file from the site
root, so the key is public by design and lives in frontend/ as <key>.txt.

A submission is only made once Netlify reports the triggering revision as
the published production deploy (public deploy metadata, no credentials)
and the live site serves the committed key, so engines are never pointed
at pages that are still building.

Plain stdlib, public GET and POST only, no credentials.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

ENDPOINT = "https://api.indexnow.org/IndexNow"
HOST = "lecturesift.com"
# Public deploy metadata of the Netlify site, the same source the frontend
# delivery check reads; it needs no credentials.
DEPLOYS_URL = (
    "https://api.netlify.com/api/v1/sites/"
    "3af04a22-7a31-44c2-8918-a79843f7ac82/deploys?per_page=10"
)
KEY_PATTERN = re.compile(r"[A-Za-z0-9-]{8,128}")
SITEMAP_NAMESPACE = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
ACCEPTED_STATUSES = {200, 202}
MAX_URLS_PER_REQUEST = 10_000
POLL_SECONDS = 15


def read_key(key_file):
    """The key held by frontend/<key>.txt, which must be named after it."""
    path = Path(key_file)
    key = path.read_text(encoding="utf-8").strip()
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError(f"{path} does not hold a valid IndexNow key")
    if path.name != f"{key}.txt":
        raise ValueError(f"{path.name} must be named after its key: {key}.txt")
    return key


def sitemap_urls(sitemap, host):
    """The sitemap's https URLs on the host, in order, without duplicates."""
    urls = []
    for loc in ElementTree.parse(sitemap).iterfind(
            f"./{SITEMAP_NAMESPACE}url/{SITEMAP_NAMESPACE}loc"):
        url = (loc.text or "").strip()
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.netloc == host and url not in urls:
            urls.append(url)
    if not urls:
        raise ValueError(f"{sitemap} lists no https://{host}/ URLs")
    return urls


def build_payload(host, key, urls):
    if len(urls) > MAX_URLS_PER_REQUEST:
        raise ValueError(f"IndexNow takes at most {MAX_URLS_PER_REQUEST} URLs per request")
    return {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": list(urls),
    }


MAX_BODY_BYTES = 1_000_000


def fetch(url, opener=None, timeout=10, data=None, headers=None):
    """Status and the body (bounded); HTTP errors are statuses too."""
    request = Request(url, data=data, headers=headers or {},
                      method="POST" if data is not None else "GET")
    try:
        with (opener or urlopen)(request, timeout=timeout) as response:
            return response.status, response.read(MAX_BODY_BYTES)
    except HTTPError as error:
        return error.code, error.read(MAX_BODY_BYTES)


def key_is_live(key_url, key, opener=None):
    status, body = fetch(key_url, opener)
    return status == 200 and body.decode("utf-8", "replace").strip() == key


def deploy_is_live(deploys_url, revision, opener=None):
    """True when the revision is a ready, published production deploy."""
    status, body = fetch(deploys_url, opener, data=None)
    if status != 200:
        return False
    deploys = json.loads(body.decode("utf-8", "replace"))
    if not isinstance(deploys, list):
        raise ValueError("Deploy metadata is not a list")
    return any(
        isinstance(item, dict)
        and item.get("context") == "production"
        and item.get("state") == "ready"
        and bool(item.get("published_at"))
        and item.get("commit_ref") == revision
        for item in deploys
    )


def _sleep(seconds):
    time.sleep(seconds)


def _clock():
    return time.monotonic()


def wait_until(condition, deadline, sleep=None, clock=None):
    """True once the condition holds, False when the deadline passes first."""
    sleep = _sleep if sleep is None else sleep
    clock = _clock if clock is None else clock
    while True:
        try:
            if condition():
                return True
        except (URLError, ValueError):
            pass
        if clock() >= deadline:
            return False
        sleep(POLL_SECONDS)


def submit(payload, endpoint=ENDPOINT, opener=None):
    body = json.dumps(payload).encode("utf-8")
    status, response = fetch(endpoint, opener, timeout=30, data=body,
                             headers={"Content-Type": "application/json; charset=utf-8"})
    return status, response.decode("utf-8", "replace").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sitemap", default="frontend/sitemap.xml")
    parser.add_argument("--key-file", required=True, help="frontend/<key>.txt")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--revision", metavar="SHA",
                        help="submit only once this commit is the published production deploy")
    parser.add_argument("--deploys-url", default=DEPLOYS_URL,
                        help="public Netlify deploy metadata of the site")
    parser.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                        help="how long to wait for the deploy and the key file before giving up")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the payload and submit nothing")
    args = parser.parse_args(argv)

    key = read_key(args.key_file)
    urls = sitemap_urls(args.sitemap, args.host)
    payload = build_payload(args.host, key, urls)
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0
    deadline = _clock() + args.wait
    if args.revision and not wait_until(
            lambda: deploy_is_live(args.deploys_url, args.revision), deadline):
        print(f"{args.revision} is not the published production deploy yet; "
              "nothing submitted", file=sys.stderr)
        return 1
    if not wait_until(lambda: key_is_live(payload["keyLocation"], key), deadline):
        print(f"{payload['keyLocation']} does not serve the committed key yet; "
              "nothing submitted", file=sys.stderr)
        return 1
    status, response = submit(payload, args.endpoint)
    print(f"IndexNow answered {status} for {len(urls)} URLs on {args.host}"
          + (f" at {args.revision[:7]}" if args.revision else "")
          + (f": {response}" if response else ""))
    return 0 if status in ACCEPTED_STATUSES else 1


if __name__ == "__main__":
    sys.exit(main())
