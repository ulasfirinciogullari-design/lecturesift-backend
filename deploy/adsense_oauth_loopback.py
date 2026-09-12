#!/usr/bin/env python3
"""Create a least-privilege AdSense refresh token through desktop OAuth.

The helper binds only to IPv4 loopback, requests the single AdSense read-only
scope, and writes credentials only to an explicitly selected private file.
It never prints OAuth codes, client secrets, access tokens, or refresh tokens.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import ssl
import stat
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)
import webbrowser


AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
ADSENSE_READONLY_SCOPE = "https://www.googleapis.com/auth/adsense.readonly"
DEFAULT_ACCOUNT_NAME = "accounts/pub-7608481350058806"
MAX_CLIENT_CONFIG_BYTES = 64 * 1024
MAX_TOKEN_RESPONSE_BYTES = 64 * 1024
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLIENT_ID = re.compile(r"[A-Za-z0-9._-]+\.apps\.googleusercontent\.com")
ACCOUNT_NAME = re.compile(r"accounts/pub-[0-9]+")
SITE_DOMAIN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}"
)


class OAuthSetupError(RuntimeError):
    """Raised for a safe, operator-actionable OAuth setup failure."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class _CallbackServer(HTTPServer):
    expected_state: str
    oauth_result: tuple[str, str] | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def log_message(self, format: str, *args: Any) -> None:
        # The default handler logs the request path, including its short-lived
        # authorization code. OAuth callback material must never reach logs.
        return

    def _reply(self, status: int, message: str) -> None:
        payload = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path != "/":
            self._reply(404, "This local OAuth callback path is not available.")
            return
        values = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
        supplied_state = values.get("state", [])
        if (
            len(supplied_state) != 1
            or not secrets.compare_digest(supplied_state[0], self.server.expected_state)
        ):
            self._reply(400, "The OAuth request could not be verified. Return to the helper.")
            return
        errors = values.get("error", [])
        codes = values.get("code", [])
        if errors:
            self.server.oauth_result = ("error", "authorization_denied")
            self._reply(400, "Authorization was not completed. You may close this tab.")
            return
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
            self.server.oauth_result = ("error", "invalid_callback")
            self._reply(400, "The authorization response was invalid. You may close this tab.")
            return
        self.server.oauth_result = ("code", codes[0])
        self._reply(200, "AdSense read-only authorization received. You may close this tab.")


def _private_input(path: Path, *, label: str) -> bytes:
    if not path.is_absolute():
        raise OAuthSetupError(f"{label} path must be absolute")
    try:
        details = path.lstat()
    except OSError as exc:
        raise OAuthSetupError(f"cannot read {label}") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise OAuthSetupError(f"{label} must be a regular non-symlink file")
    try:
        parent_details = path.parent.lstat()
        resolved_parent = path.parent.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        if resolved_path.is_relative_to(REPOSITORY_ROOT):
            raise OAuthSetupError(f"{label} must stay outside the source repository")
    except OSError as exc:
        raise OAuthSetupError(f"cannot resolve {label}") from exc
    if (
        stat.S_ISLNK(parent_details.st_mode)
        or not stat.S_ISDIR(parent_details.st_mode)
        or resolved_parent != path.parent.absolute()
        or parent_details.st_uid != os.geteuid()
        or stat.S_IMODE(parent_details.st_mode) & 0o022
    ):
        raise OAuthSetupError(
            f"{label} directory must be real, owned by the current user, and not writable by group or others"
        )
    if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) not in {0o400, 0o600}:
        raise OAuthSetupError(f"{label} must be owned by the current user with mode 0400 or 0600")
    if details.st_size <= 0 or details.st_size > MAX_CLIENT_CONFIG_BYTES:
        raise OAuthSetupError(f"{label} has an invalid size")
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_CLIENT_CONFIG_BYTES + 1)
    except OSError as exc:
        raise OAuthSetupError(f"cannot read {label}") from exc
    if len(payload) > MAX_CLIENT_CONFIG_BYTES:
        raise OAuthSetupError(f"{label} is too large")
    return payload


def load_desktop_client(path: Path) -> tuple[str, str]:
    try:
        document = json.loads(_private_input(path, label="OAuth client configuration"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthSetupError("OAuth client configuration is not valid JSON") from exc
    if not isinstance(document, dict) or set(document).intersection({"installed", "web"}) != {
        "installed"
    }:
        raise OAuthSetupError("use a Google OAuth client whose application type is Desktop app")
    installed = document["installed"]
    if not isinstance(installed, dict):
        raise OAuthSetupError("OAuth desktop client configuration is invalid")
    client_id = installed.get("client_id")
    client_secret = installed.get("client_secret")
    if (
        not isinstance(client_id, str)
        or CLIENT_ID.fullmatch(client_id) is None
        or len(client_id) > 512
        or any(character.isspace() for character in client_id)
    ):
        raise OAuthSetupError("OAuth desktop client ID is invalid")
    if (
        not isinstance(client_secret, str)
        or not 8 <= len(client_secret) <= 1024
        or any(ord(character) < 33 for character in client_secret)
    ):
        raise OAuthSetupError("OAuth desktop client secret is invalid")
    if installed.get("auth_uri", AUTHORIZATION_ENDPOINT) != AUTHORIZATION_ENDPOINT:
        raise OAuthSetupError("OAuth authorization endpoint is not the fixed Google endpoint")
    if installed.get("token_uri", TOKEN_ENDPOINT) != TOKEN_ENDPOINT:
        raise OAuthSetupError("OAuth token endpoint is not the fixed Google endpoint")
    return client_id, client_secret


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorization_url(
    client_id: str, redirect_uri: str, state_value: str, challenge: str
) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ADSENSE_READONLY_SCOPE,
            "state": state_value,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        }
    )
    return f"{AUTHORIZATION_ENDPOINT}?{query}"


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    timeout_seconds: int,
) -> str:
    body = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("ascii")
    request = Request(
        TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "LectureSift-AdSense-OAuth/1",
        },
    )
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=ssl.create_default_context()),
        _RejectRedirects(),
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_TOKEN_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise OAuthSetupError(
            f"Google rejected the token exchange with HTTP {exc.code}; no credential file was written"
        ) from None
    except (URLError, TimeoutError, OSError):
        raise OAuthSetupError("the fixed Google token endpoint could not be reached") from None
    if len(payload) > MAX_TOKEN_RESPONSE_BYTES:
        raise OAuthSetupError("Google token response exceeded the safety limit")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthSetupError("Google token response was invalid") from exc
    if not isinstance(document, dict):
        raise OAuthSetupError("Google token response was invalid")
    returned_scope = document.get("scope")
    if not isinstance(returned_scope, str) or returned_scope.split() != [
        ADSENSE_READONLY_SCOPE
    ]:
        raise OAuthSetupError("Google returned a grant outside the requested read-only scope")
    refresh_token = document.get("refresh_token")
    if (
        not isinstance(refresh_token, str)
        or not 16 <= len(refresh_token) <= 4096
        or any(ord(character) < 33 for character in refresh_token)
    ):
        raise OAuthSetupError(
            "Google did not return a usable refresh token; revoke the old grant and retry"
        )
    return refresh_token


def _validate_account_name(value: str) -> str:
    if len(value) > 128 or ACCOUNT_NAME.fullmatch(value) is None:
        raise OAuthSetupError("AdSense account name must match accounts/pub-<digits>")
    return value


def _validate_site_domain(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    if SITE_DOMAIN.fullmatch(normalized) is None:
        raise OAuthSetupError("AdSense site domain must be a plain DNS hostname")
    return normalized


def validate_private_output(output: Path, *, replace: bool) -> Path:
    if os.name != "posix":
        raise OAuthSetupError("run this helper in WSL, Linux, or macOS so mode 0600 can be enforced")
    if not output.is_absolute():
        raise OAuthSetupError("credential output path must be absolute")
    parent = output.parent
    try:
        parent_details = parent.lstat()
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise OAuthSetupError("credential output directory is unavailable") from exc
    if (
        stat.S_ISLNK(parent_details.st_mode)
        or not stat.S_ISDIR(parent_details.st_mode)
        or resolved_parent != parent.absolute()
        or parent_details.st_uid != os.geteuid()
        or stat.S_IMODE(parent_details.st_mode) & 0o022
    ):
        raise OAuthSetupError(
            "credential output directory must be real, owned by the current user, and not writable by group or others"
        )
    resolved_output = resolved_parent / output.name
    if resolved_output.is_relative_to(REPOSITORY_ROOT):
        raise OAuthSetupError("credential output must stay outside the source repository")
    if output.exists() or output.is_symlink():
        if not replace:
            raise OAuthSetupError("credential output already exists; pass --replace to rotate it")
        details = output.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise OAuthSetupError("existing credential output is not a private mode-0600 file")
    return parent


def write_private_environment(
    output: Path,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    account_name: str,
    site_domain: str,
    replace: bool,
) -> None:
    # Recheck immediately before the atomic write as well as before OAuth, so
    # an invalid destination never consumes a newly issued refresh token and a
    # path swap during consent still fails closed.
    parent = validate_private_output(output, replace=replace)
    values = {
        "LECTURESIFT_ADSENSE_API_ENABLED": "false",
        "LECTURESIFT_ADSENSE_API_CLIENT_ID": client_id,
        "LECTURESIFT_ADSENSE_API_CLIENT_SECRET": client_secret,
        "LECTURESIFT_ADSENSE_API_REFRESH_TOKEN": refresh_token,
        "LECTURESIFT_ADSENSE_API_ACCOUNT_NAME": account_name,
        "LECTURESIFT_ADSENSE_API_SITE_DOMAIN": site_domain,
    }
    content = (
        "# Generated by deploy/adsense_oauth_loopback.py. Keep private; do not commit.\n"
        + "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
        + "\n"
    )
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=parent)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
        os.chmod(output, 0o600, follow_symlinks=False)
        details = output.lstat()
        if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
            raise OAuthSetupError("credential output permissions could not be enforced")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account-name", default=DEFAULT_ACCOUNT_NAME)
    parser.add_argument("--site-domain", default="lecturesift.com")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--authorization-timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    if not 60 <= args.authorization_timeout_seconds <= 600:
        parser.error("--authorization-timeout-seconds must be between 60 and 600")
    try:
        account_name = _validate_account_name(args.account_name.strip())
        site_domain = _validate_site_domain(args.site_domain)
        validate_private_output(args.output, replace=args.replace)
        client_id, client_secret = load_desktop_client(args.client_config)
        # Bind the callback before constructing the URL, so the exact dynamic
        # redirect URI used for the exchange remains available here.
        verifier, challenge = pkce_pair()
        state_value = secrets.token_urlsafe(32)
        with _CallbackServer(("127.0.0.1", 0), _CallbackHandler) as server:
            server.expected_state = state_value
            redirect_uri = f"http://127.0.0.1:{int(server.server_address[1])}/"
            url = authorization_url(client_id, redirect_uri, state_value, challenge)
            if webbrowser.open(url, new=1, autoraise=True):
                print("Complete the AdSense read-only consent in the opened browser.")
            else:
                print("Open this short-lived Google authorization URL in your local browser:")
                print(url)
            deadline = time.monotonic() + args.authorization_timeout_seconds
            while server.oauth_result is None and time.monotonic() < deadline:
                server.timeout = min(1.0, max(0.05, deadline - time.monotonic()))
                server.handle_request()
            result = server.oauth_result
        if result is None or result[0] != "code":
            raise OAuthSetupError("OAuth authorization was not completed; no credential file was written")
        refresh_token = exchange_code(
            client_id=client_id,
            client_secret=client_secret,
            code=result[1],
            verifier=verifier,
            redirect_uri=redirect_uri,
            timeout_seconds=20,
        )
        write_private_environment(
            args.output,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            account_name=account_name,
            site_domain=site_domain,
            replace=args.replace,
        )
    except OAuthSetupError as exc:
        parser.exit(1, f"AdSense OAuth setup failed: {exc}\n")
    print(f"Private credential fragment written with mode 0600: {args.output}")
    print("The AdSense API remains disabled until the deployment flag is explicitly enabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
