#!/usr/bin/env python3
"""Prove that dedicated rehearsal R2 credentials cannot read production.

The check is deliberately read-only.  A signed ListObjectsV2 request and a
signed GET for a cryptographically random missing object first prove that the
credentials work against the rehearsal bucket.  The same operations must then
be denied by the production bucket.  Any success, ambiguous error, transport
failure or malformed response fails closed.  Secret values never enter the
proof document or diagnostic messages.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import ssl
import stat
import tempfile
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)
import xml.etree.ElementTree as ElementTree


ASSIGNMENT = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[0-9]{14}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DENIAL_CODES = frozenset({"AccessDenied", "InvalidAccessKeyId"})
MAX_RESPONSE_BYTES = 64 * 1024
PROOF_FORMAT = "lecturesift-rehearsal-r2-negative-capability-v1"


class R2IsolationError(RuntimeError):
    """Raised when storage isolation cannot be proved unambiguously."""


@dataclass(frozen=True)
class ProbeResult:
    status: int
    code: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def parse_dotenv_text(text: str, *, label: str) -> dict[str, str]:
    """Parse one root dotenv as literals, never as executable shell input."""

    if "\x00" in text:
        raise R2IsolationError(f"{label} contains a NUL byte")
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(raw)
        if not match:
            raise R2IsolationError(f"unsupported {label} syntax at line {number}")
        key, rhs = match.groups()
        if key in values:
            raise R2IsolationError(f"duplicate {label} key: {key}")
        if not rhs.strip():
            value = ""
        else:
            lexer = shlex.shlex(rhs, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            try:
                words = list(lexer)
            except ValueError as exc:
                raise R2IsolationError(
                    f"invalid {label} quoting at line {number}"
                ) from exc
            if len(words) != 1:
                raise R2IsolationError(
                    f"ambiguous {label} value at line {number}"
                )
            value = words[0]
        if any(character in value for character in "\r\n"):
            raise R2IsolationError(f"multiline {label} value: {key}")
        values[key] = value
    return values


def _private_dotenv(path: Path, *, label: str) -> dict[str, str]:
    try:
        details = path.lstat()
    except OSError as exc:
        raise R2IsolationError(f"missing {label}") from exc
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != 0
        or stat.S_IMODE(details.st_mode) not in {0o400, 0o600}
        or path.resolve(strict=True) != path
    ):
        raise R2IsolationError(f"{label} must be root-owned mode 0400/0600")
    try:
        return parse_dotenv_text(path.read_text(encoding="utf-8"), label=label)
    except (OSError, UnicodeError) as exc:
        raise R2IsolationError(f"cannot read {label}") from exc


def _endpoint(value: str, *, label: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not (
        parsed.scheme == "https"
        and host.endswith(".r2.cloudflarestorage.com")
        and parsed.port in {None, 443}
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        raise R2IsolationError(f"{label} must be an explicit Cloudflare R2 HTTPS endpoint")
    return f"https://{host}", host


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _error_code(payload: bytes) -> str:
    if not payload or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        return ""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return ""
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] == "Code" and item.text:
            code = item.text.strip()
            return code if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", code) else ""
    return ""


def _read_bounded(stream) -> bytes:  # noqa: ANN001
    payload = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise R2IsolationError("R2 returned an oversized capability response")
    return payload


def signed_request(
    endpoint: str,
    region: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    operation: str,
    probe_key: str,
) -> ProbeResult:
    """Make one bounded, no-redirect, direct HTTPS SigV4 request."""

    if operation == "list":
        raw_path = f"/{bucket}"
        query = (
            "list-type=2&max-keys=1&prefix="
            + quote(probe_key, safe="-_.~")
        )
    elif operation == "get-missing":
        raw_path = f"/{bucket}/{probe_key}"
        query = ""
    else:
        raise R2IsolationError("unsupported R2 capability operation")

    canonical_path = quote(raw_path, safe="/-_.~")
    url = endpoint + canonical_path + (f"?{query}" if query else "")
    host = urlsplit(endpoint).hostname or ""
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    empty_sha256 = hashlib.sha256(b"").hexdigest()
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{empty_sha256}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [
            "GET",
            canonical_path,
            query,
            canonical_headers,
            signed_headers,
            empty_sha256,
        ]
    )
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    date_key = _hmac(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    region_key = _hmac(date_key, region)
    service_key = _hmac(region_key, "s3")
    signing_key = _hmac(service_key, "aws4_request")
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    request = Request(
        url,
        method="GET",
        headers={
            "Authorization": authorization,
            "Host": host,
            "User-Agent": "LectureSift-R2-Isolation-Probe/1",
            "X-Amz-Content-Sha256": empty_sha256,
            "X-Amz-Date": amz_date,
        },
    )
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )
    try:
        with opener.open(request, timeout=15) as response:
            payload = _read_bounded(response)
            return ProbeResult(status=int(response.status), code=_error_code(payload))
    except HTTPError as exc:
        payload = _read_bounded(exc)
        return ProbeResult(status=int(exc.code), code=_error_code(payload))
    except (OSError, TimeoutError, URLError) as exc:
        raise R2IsolationError("R2 capability request could not be completed") from exc


Transport = Callable[[str, str, str, str, str, str, str], ProbeResult]


def prove_isolation(
    runtime: Mapping[str, str],
    production_api: Mapping[str, str],
    rehearsal: Mapping[str, str],
    *,
    revision: str,
    run_id: str,
    probe_key: str | None = None,
    transport: Transport = signed_request,
) -> dict[str, object]:
    """Return a secret-free proof or fail closed before admission."""

    if not REVISION.fullmatch(revision) or not RUN_ID.fullmatch(run_id):
        raise R2IsolationError("invalid rehearsal proof identity")
    production_endpoint_value = runtime.get("S3_ENDPOINT_URL") or production_api.get(
        "S3_ENDPOINT_URL", ""
    )
    production_bucket = runtime.get("S3_BUCKET") or production_api.get("S3_BUCKET", "")
    production_region = runtime.get("S3_REGION") or production_api.get("S3_REGION") or "auto"
    rehearsal_endpoint_value = rehearsal.get("LECTURESIFT_REHEARSAL_S3_ENDPOINT_URL", "")
    rehearsal_bucket = rehearsal.get("LECTURESIFT_REHEARSAL_S3_BUCKET", "")
    rehearsal_region = rehearsal.get("LECTURESIFT_REHEARSAL_S3_REGION") or "auto"
    rehearsal_access = rehearsal.get("LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID", "")
    rehearsal_secret = rehearsal.get("LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY", "")
    if not all(
        (
            production_endpoint_value,
            production_bucket,
            rehearsal_endpoint_value,
            rehearsal_bucket,
            rehearsal_access,
            rehearsal_secret,
        )
    ):
        raise R2IsolationError("R2 capability inputs are incomplete")
    if (
        not BUCKET.fullmatch(production_bucket)
        or not BUCKET.fullmatch(rehearsal_bucket)
        or production_bucket == rehearsal_bucket
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", production_region)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", rehearsal_region)
    ):
        raise R2IsolationError("R2 capability identities are invalid")
    production_access = runtime.get("S3_ACCESS_KEY_ID") or production_api.get(
        "S3_ACCESS_KEY_ID", ""
    )
    production_secret = runtime.get("S3_SECRET_ACCESS_KEY") or production_api.get(
        "S3_SECRET_ACCESS_KEY", ""
    )
    if rehearsal_access in {production_access, production_secret} or rehearsal_secret in {
        production_access,
        production_secret,
    }:
        raise R2IsolationError("rehearsal and production storage credentials overlap")

    production_endpoint, production_host = _endpoint(
        production_endpoint_value, label="production storage endpoint"
    )
    rehearsal_endpoint, rehearsal_host = _endpoint(
        rehearsal_endpoint_value, label="rehearsal storage endpoint"
    )
    key = probe_key or f".lecturesift-capability-{secrets.token_hex(24)}"
    if not re.fullmatch(r"\.lecturesift-capability-[0-9a-f]{48}", key):
        raise R2IsolationError("invalid R2 capability probe identity")

    rehearsal_list = transport(
        rehearsal_endpoint,
        rehearsal_region,
        rehearsal_access,
        rehearsal_secret,
        rehearsal_bucket,
        "list",
        key,
    )
    if rehearsal_list.status != 200:
        raise R2IsolationError("rehearsal R2 positive list control did not succeed")
    rehearsal_missing = transport(
        rehearsal_endpoint,
        rehearsal_region,
        rehearsal_access,
        rehearsal_secret,
        rehearsal_bucket,
        "get-missing",
        key,
    )
    if rehearsal_missing != ProbeResult(status=404, code="NoSuchKey"):
        raise R2IsolationError("rehearsal R2 positive object control was ambiguous")

    production_list = transport(
        production_endpoint,
        production_region,
        rehearsal_access,
        rehearsal_secret,
        production_bucket,
        "list",
        key,
    )
    production_get = transport(
        production_endpoint,
        production_region,
        rehearsal_access,
        rehearsal_secret,
        production_bucket,
        "get-missing",
        key,
    )
    for label, result in (
        ("production bucket listing", production_list),
        ("production object read", production_get),
    ):
        if result.status != 403 or result.code not in DENIAL_CODES:
            raise R2IsolationError(f"{label} was not unambiguously denied")

    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    proof: dict[str, object] = {
        "format": PROOF_FORMAT,
        "revision": revision,
        "run_id": run_id,
        "rehearsal_list_control": "allowed",
        "rehearsal_missing_object_control": "confirmed",
        "production_list_access": "denied",
        "production_object_read": "denied",
        "production_list_denial_code": production_list.code,
        "production_object_denial_code": production_get.code,
        "production_endpoint_host_sha256": digest(production_host),
        "production_bucket_sha256": digest(production_bucket),
        "rehearsal_endpoint_host_sha256": digest(rehearsal_host),
        "rehearsal_bucket_sha256": digest(rehearsal_bucket),
        "credentials_in_proof": False,
        "probe_wrote_objects": False,
    }
    if any(
        secret and secret in json.dumps(proof, sort_keys=True)
        for secret in (rehearsal_access, rehearsal_secret, production_access, production_secret)
    ):
        raise R2IsolationError("secret material entered the R2 capability proof")
    return proof


def _atomic_write_private(path: Path, payload: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_uid != 0:
            raise R2IsolationError("R2 capability proof metadata is unsafe")
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--api", type=Path, required=True)
    parser.add_argument("--rehearsal", type=Path, required=True)
    parser.add_argument("--environment-proof", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise R2IsolationError("R2 capability proof requires root")
        run_dir = args.output.parent.resolve(strict=True)
        details = run_dir.lstat()
        if (
            run_dir.parent != Path("/var/backups/lecturesift/rehearsal")
            or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", run_dir.name)
            or not stat.S_ISDIR(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
            or details.st_uid != 0
            or stat.S_IMODE(details.st_mode) != 0o700
            or args.output != run_dir / "rehearsal-r2-negative-capability.json"
        ):
            raise R2IsolationError("unsafe R2 capability proof output")
        proof_details = args.environment_proof.lstat()
        if (
            args.environment_proof
            != run_dir / "rehearsal-environment-proof.json"
            or not stat.S_ISREG(proof_details.st_mode)
            or stat.S_ISLNK(proof_details.st_mode)
            or proof_details.st_uid != 0
            or stat.S_IMODE(proof_details.st_mode) not in {0o400, 0o600}
            or args.environment_proof.resolve(strict=True) != args.environment_proof
        ):
            raise R2IsolationError("unsafe rehearsal environment proof")
        environment = json.loads(
            args.environment_proof.read_text(encoding="utf-8")
        )
        revision = environment.get("revision") if isinstance(environment, dict) else None
        run_id = environment.get("run_id") if isinstance(environment, dict) else None
        if (
            environment.get("format") != "lecturesift-rehearsal-environment-proof-v1"
            or not isinstance(revision, str)
            or not isinstance(run_id, str)
            or run_id != run_dir.name.replace("T", "").removesuffix("Z")
        ):
            raise R2IsolationError("invalid rehearsal environment proof identity")
        proof = prove_isolation(
            _private_dotenv(args.runtime, label="production runtime environment"),
            _private_dotenv(args.api, label="production API environment"),
            _private_dotenv(args.rehearsal, label="rehearsal environment"),
            revision=revision,
            run_id=run_id,
        )
        payload = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
        _atomic_write_private(args.output, payload)
    except (OSError, UnicodeError, json.JSONDecodeError, R2IsolationError) as exc:
        print(f"R2 rehearsal isolation proof failed: {exc}", file=os.sys.stderr)
        return 1
    print("R2_REHEARSAL_NEGATIVE_CAPABILITY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
