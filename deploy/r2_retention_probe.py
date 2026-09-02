#!/usr/bin/env python3
"""Fail-closed proof for the dedicated R2 backup bucket's data lock.

This tool deliberately creates exactly one uniquely named object below the
reserved ``restic/data/.lecturesift-retention-probes/v1/`` prefix.  It never
lists the bucket and never addresses a Restic pack, snapshot, index, key, or
lock object.  Success means all of the following were observed:

* the object was written and read back byte-for-byte with its purpose metadata;
* DELETE was rejected specifically as a retention/bucket-lock operation; and
* the same bytes and metadata were still readable after the rejected DELETE.

The surviving object is intentional immutable evidence and is not cleaned up.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


EXPECTED_BUCKET = "lecturesift-production-backups"
EXPECTED_REPOSITORY_PREFIX = "restic"
PROBE_PREFIX = "restic/data/.lecturesift-retention-probes/v1/"
PROBE_VERSION = "1"
EXPECTED_RETENTION_DAYS = "90"
CONFIRMATION = "CREATE-ONE-IMMUTABLE-R2-PROBE"
EVIDENCE_ROOT = Path("/var/lib/lecturesift/recovery-drills")
EVIDENCE_NAME = "r2-retention-lock.ok"
_ACCOUNT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PROBE_KEY_RE = re.compile(
    r"^restic/data/[.]lecturesift-retention-probes/v1/"
    r"probe-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}[.]json$"
)
_LOCK_MARKERS = ("bucket lock", "object lock", "retention", "immutable", "locked")


class ProbeError(RuntimeError):
    """A safe, non-secret diagnostic suitable for operator output."""


class S3HttpError(ProbeError):
    def __init__(self, *, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"R2 request failed (HTTP {status}, code={code or 'unknown'}).")


@dataclass(frozen=True)
class RepositoryTarget:
    account_id: str
    endpoint: str
    bucket: str = EXPECTED_BUCKET
    repository_prefix: str = EXPECTED_REPOSITORY_PREFIX


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward a signed credential-bearing request to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def parse_repository(repository: str) -> RepositoryTarget:
    """Accept only the one EU R2 bucket and Restic repository used in prod."""

    prefix = "s3:"
    if not repository.startswith(prefix):
        raise ProbeError("RESTIC_REPOSITORY must be the dedicated R2 S3 repository.")
    raw_url = repository[len(prefix) :]
    parsed = urllib.parse.urlsplit(raw_url)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProbeError("RESTIC_REPOSITORY is not the exact HTTPS backup target.")

    host = (parsed.hostname or "").lower()
    suffix = ".eu.r2.cloudflarestorage.com"
    if not host.endswith(suffix):
        raise ProbeError("RESTIC_REPOSITORY must use the Cloudflare R2 EU endpoint.")
    account_id = host[: -len(suffix)]
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        raise ProbeError("RESTIC_REPOSITORY contains an invalid R2 account endpoint.")

    expected_path = f"/{EXPECTED_BUCKET}/{EXPECTED_REPOSITORY_PREFIX}"
    if parsed.path != expected_path:
        raise ProbeError(
            "RESTIC_REPOSITORY must target lecturesift-production-backups/restic exactly."
        )
    endpoint = f"https://{account_id}{suffix}"
    return RepositoryTarget(account_id=account_id, endpoint=endpoint)


def validate_probe_key(key: str) -> None:
    if not _PROBE_KEY_RE.fullmatch(key):
        raise ProbeError("Refusing an object key outside the reserved retention-probe prefix.")


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _canonical_header_value(value: str) -> str:
    return " ".join(value.strip().split())


def _parse_s3_error(raw: bytes) -> tuple[str, str]:
    """Extract only the public S3 error code/message, never headers or secrets."""

    try:
        root = ET.fromstring(raw[:65536])
    except ET.ParseError:
        return "", ""
    code = root.findtext("Code") or ""
    message = root.findtext("Message") or ""
    return code[:128], message[:512]


class CloudflareR2Client:
    """Minimal SigV4 client so the host needs only the Python standard library."""

    def __init__(
        self,
        *,
        target: RepositoryTarget,
        access_key_id: str,
        secret_access_key: str,
        timeout_seconds: int = 30,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        if not access_key_id or not secret_access_key:
            raise ProbeError("Dedicated host-only R2 backup credentials are required.")
        self._target = target
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._timeout_seconds = timeout_seconds
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._opener = urllib.request.build_opener(_RejectRedirects())

    def request(
        self,
        method: str,
        key: str,
        *,
        body: bytes = b"",
        metadata: Mapping[str, str] | None = None,
        if_none_match: bool = False,
    ) -> HttpResponse:
        validate_probe_key(key)
        if method not in {"PUT", "GET", "DELETE"}:
            raise ProbeError("The retention probe permits only PUT, GET, and DELETE.")
        if method != "PUT" and (body or metadata or if_none_match):
            raise ProbeError("Unexpected request data for a read/delete probe operation.")

        payload_hash = _sha256_hex(body)
        timestamp = self._now().astimezone(dt.timezone.utc)
        amz_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = timestamp.strftime("%Y%m%d")
        host = urllib.parse.urlsplit(self._target.endpoint).hostname or ""
        headers: dict[str, str] = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if metadata:
            for name, value in metadata.items():
                if not re.fullmatch(r"[a-z0-9-]+", name) or not value:
                    raise ProbeError("Invalid retention-probe metadata.")
                headers[f"x-amz-meta-{name}"] = value
        if if_none_match:
            headers["if-none-match"] = "*"

        sorted_names = sorted(headers)
        canonical_headers = "".join(
            f"{name}:{_canonical_header_value(headers[name])}\n" for name in sorted_names
        )
        signed_headers = ";".join(sorted_names)
        object_path = f"/{self._target.bucket}/{key}"
        canonical_uri = urllib.parse.quote(object_path, safe="/-_.~")
        canonical_request = "\n".join(
            (
                method,
                canonical_uri,
                "",
                canonical_headers,
                signed_headers,
                payload_hash,
            )
        )
        scope = f"{date_stamp}/auto/s3/aws4_request"
        string_to_sign = "\n".join(
            ("AWS4-HMAC-SHA256", amz_date, scope, _sha256_hex(canonical_request.encode()))
        )
        date_key = _hmac_sha256(("AWS4" + self._secret_access_key).encode(), date_stamp)
        region_key = _hmac_sha256(date_key, "auto")
        service_key = _hmac_sha256(region_key, "s3")
        signing_key = _hmac_sha256(service_key, "aws4_request")
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers["authorization"] = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self._access_key_id}/{scope},"
            f"SignedHeaders={signed_headers},Signature={signature}"
        )
        headers["user-agent"] = "LectureSift-R2-Retention-Probe/1"

        request = urllib.request.Request(
            f"{self._target.endpoint}{canonical_uri}",
            data=body if method == "PUT" else None,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                response_body = response.read(2 * 1024 * 1024)
                if response.read(1):
                    raise ProbeError("Retention-probe response exceeded the safety limit.")
                return HttpResponse(
                    status=response.status,
                    headers={name.lower(): value for name, value in response.headers.items()},
                    body=response_body,
                )
        except urllib.error.HTTPError as exc:
            error_body = exc.read(65536)
            code, message = _parse_s3_error(error_body)
            raise S3HttpError(status=exc.code, code=code, message=message) from None
        except urllib.error.URLError:
            raise ProbeError("R2 could not be reached over verified HTTPS.") from None


def _expect_success(response: HttpResponse, action: str) -> None:
    if response.status < 200 or response.status >= 300:
        raise ProbeError(f"R2 {action} did not return a successful status.")


def _verify_readback(
    response: HttpResponse, *, payload: bytes, metadata: Mapping[str, str]
) -> None:
    _expect_success(response, "readback")
    expected_hash = _sha256_hex(payload)
    if not hmac.compare_digest(_sha256_hex(response.body), expected_hash):
        raise ProbeError("Retention-probe readback hash did not match the uploaded bytes.")
    for name, expected in metadata.items():
        actual = response.headers.get(f"x-amz-meta-{name}", "")
        if not hmac.compare_digest(actual, expected):
            raise ProbeError("Retention-probe purpose metadata was missing or changed.")


def _is_specific_lock_denial(error: S3HttpError) -> bool:
    if error.status not in {403, 409, 423}:
        return False
    diagnostic = f"{error.code} {error.message}".lower()
    return any(marker in diagnostic for marker in _LOCK_MARKERS)


def run_probe(
    client: CloudflareR2Client,
    *,
    created_at: dt.datetime | None = None,
    probe_uuid: uuid.UUID | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    created_at = (created_at or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    probe_uuid = probe_uuid or uuid.uuid4()
    nonce = nonce or secrets.token_hex(32)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    probe_id = probe_uuid.hex
    key = f"{PROBE_PREFIX}probe-{stamp}-{probe_id}.json"
    validate_probe_key(key)

    payload = json.dumps(
        {
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "expected_retention_days": int(EXPECTED_RETENTION_DAYS),
            "nonce": nonce,
            "probe_id": probe_id,
            "purpose": "lecturesift-r2-immutable-retention-proof",
            "version": int(PROBE_VERSION),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload_hash = _sha256_hex(payload)
    metadata = {
        "lecturesift-purpose": "immutable-retention-probe",
        "lecturesift-probe-id": probe_id,
        "lecturesift-probe-version": PROBE_VERSION,
        "lecturesift-retention-days": EXPECTED_RETENTION_DAYS,
        "lecturesift-sha256": payload_hash,
    }

    put_response = client.request(
        "PUT", key, body=payload, metadata=metadata, if_none_match=True
    )
    _expect_success(put_response, "upload")
    _verify_readback(client.request("GET", key), payload=payload, metadata=metadata)

    try:
        delete_response = client.request("DELETE", key)
    except S3HttpError as exc:
        if not _is_specific_lock_denial(exc):
            raise ProbeError(
                "DELETE failed, but R2 did not identify the failure as a bucket/retention lock."
            ) from None
        delete_code = exc.code or f"HTTP-{exc.status}"
    else:
        _expect_success(delete_response, "delete")
        raise ProbeError(
            "DELETE succeeded; the expected R2 retention lock is not protecting the probe."
        )

    _verify_readback(client.request("GET", key), payload=payload, metadata=metadata)
    return {
        "bucket": EXPECTED_BUCKET,
        "delete_result": delete_code,
        "key": key,
        "payload_sha256": payload_hash,
        "repository_prefix": EXPECTED_REPOSITORY_PREFIX,
        "status": "immutable-retention-verified",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must equal {CONFIRMATION!r}; the probe intentionally cannot be removed early.",
    )
    return parser


def _current_repository_id_sha256() -> str:
    restic = shutil.which("restic")
    if not restic:
        raise ProbeError("restic is required to bind retention proof to the repository.")
    environment = os.environ.copy()
    environment["AWS_ACCESS_KEY_ID"] = environment.get("RESTIC_AWS_ACCESS_KEY_ID", "")
    environment["AWS_SECRET_ACCESS_KEY"] = environment.get(
        "RESTIC_AWS_SECRET_ACCESS_KEY", ""
    )
    try:
        completed = subprocess.run(
            [restic, "cat", "config"],
            check=True,
            capture_output=True,
            env=environment,
            timeout=60,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        raise ProbeError("The exact Restic repository identity could not be verified.") from None
    repository_id = str(payload.get("id") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", repository_id):
        raise ProbeError("The Restic repository returned an invalid identity.")
    return _sha256_hex(repository_id.encode("ascii"))


def _write_evidence(
    result: Mapping[str, str],
    *,
    target: RepositoryTarget,
    repository_id_sha256: str,
    root: Path = EVIDENCE_ROOT,
) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", repository_id_sha256):
        raise ProbeError("The Restic repository identity hash is invalid.")
    if not root.parent.is_dir() or root.parent.is_symlink():
        raise ProbeError("The retention evidence parent is missing or unsafe.")
    root.mkdir(mode=0o700, exist_ok=True)
    if root.resolve(strict=True) != root or root.is_symlink():
        raise ProbeError("The retention evidence directory escaped its fixed path.")
    details = root.stat()
    if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
        raise ProbeError("The retention evidence directory must be root-owned and not writable by others.")

    repository = (
        f"s3:{target.endpoint}/{EXPECTED_BUCKET}/{EXPECTED_REPOSITORY_PREFIX}"
    )
    fields = {
        "bucket": EXPECTED_BUCKET,
        "delete_result": result["delete_result"],
        "probe_key": result["key"],
        "probe_payload_sha256": result["payload_sha256"],
        "repository_id_sha256": repository_id_sha256,
        "repository_target_sha256": _sha256_hex(repository.encode("ascii")),
        "retention_days": EXPECTED_RETENTION_DAYS,
        "status": "immutable-retention-verified",
        "verified_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": PROBE_VERSION,
    }
    for key, value in fields.items():
        if "\n" in value or "\r" in value or "=" in key:
            raise ProbeError("Retention evidence contains an unsafe field.")

    destination = root / EVIDENCE_NAME
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{EVIDENCE_NAME}-", dir=root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n", closefd=True) as stream:
            descriptor = -1
            for key in sorted(fields):
                stream.write(f"{key}={fields[key]}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION:
            raise ProbeError("Explicit immutable-probe confirmation did not match.")
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise ProbeError("The R2 retention probe must run as root on the backup host.")
        repository = os.environ.get("RESTIC_REPOSITORY", "")
        target = parse_repository(repository)
        client = CloudflareR2Client(
            target=target,
            access_key_id=os.environ.get("RESTIC_AWS_ACCESS_KEY_ID", ""),
            secret_access_key=os.environ.get("RESTIC_AWS_SECRET_ACCESS_KEY", ""),
        )
        result = run_probe(client)
        repository_id_sha256 = _current_repository_id_sha256()
        evidence = _write_evidence(
            result, target=target, repository_id_sha256=repository_id_sha256
        )
        result["evidence"] = str(evidence)
    except ProbeError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
