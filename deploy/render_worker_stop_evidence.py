#!/usr/bin/env python3
"""Produce a stable, GET-only proof that the Render worker is suspended.

The Render API token is read from one fixed root-only control file.  The token
is never included in output, diagnostics, or the resulting digest.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
from typing import Callable, Final


CONTROL_FILE: Final = Path("/root/.lecturesift-render-cutover-control.env")
CONTROL_OWNER_UID: Final = 0
CONTROL_OWNER_GID: Final = 0
API_HOST: Final = "api.render.com"
API_PORT: Final = 443
MAX_CONTROL_BYTES: Final = 16 * 1024
MAX_RESPONSE_BYTES: Final = 1024 * 1024
SCHEMA: Final = "lecturesift-render-worker-stop-v1"

_TOKEN = re.compile(r"[A-Za-z0-9._~-]{20,512}")
_SERVICE_ID = re.compile(r"srv-[a-z0-9]{16,40}")
_SERVICE_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._ -]{0,126}[A-Za-z0-9])?")
_OWNER_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,127}")
_ALLOWED_SUSPENDERS: Final = {
    "admin",
    "billing",
    "hipaa_enablement",
    "parent_service",
    "stuck_crashlooping",
    "unknown",
    "user",
}
_EXPECTED_KEYS: Final = {
    "RENDER_API_TOKEN",
    "RENDER_WORKER_SERVICE_ID",
    "RENDER_WORKER_SERVICE_NAME",
}


class StopEvidenceError(RuntimeError):
    """A fail-closed Render worker proof error."""


def _same_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return all(
        getattr(before, field) == getattr(after, field)
        for field in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


def parse_control(data: bytes) -> dict[str, str]:
    if not data or len(data) > MAX_CONTROL_BYTES or b"\0" in data:
        raise StopEvidenceError("the Render control file has an invalid size or encoding")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise StopEvidenceError("the Render control file is not strict UTF-8") from exc
    if not text.endswith("\n") or "\r" in text:
        raise StopEvidenceError("the Render control file must use newline-terminated LF records")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in _EXPECTED_KEYS or key in fields:
            raise StopEvidenceError("the Render control file has missing, duplicate, or unknown fields")
        if not value or value != value.strip():
            raise StopEvidenceError("the Render control file contains an invalid value")
        fields[key] = value
    if set(fields) != _EXPECTED_KEYS:
        raise StopEvidenceError("the Render control file does not contain the exact required fields")
    if _TOKEN.fullmatch(fields["RENDER_API_TOKEN"]) is None:
        raise StopEvidenceError("the Render API token has an invalid format")
    if _SERVICE_ID.fullmatch(fields["RENDER_WORKER_SERVICE_ID"]) is None:
        raise StopEvidenceError("the Render worker service ID has an invalid format")
    if _SERVICE_NAME.fullmatch(fields["RENDER_WORKER_SERVICE_NAME"]) is None:
        raise StopEvidenceError("the Render worker service name has an invalid format")
    return fields


def load_control(path: Path = CONTROL_FILE) -> dict[str, str]:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise StopEvidenceError("the Render control path must be absolute and canonical")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise StopEvidenceError("the fixed Render control file is missing") from exc
    mode = stat.S_IMODE(before.st_mode)
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != CONTROL_OWNER_UID
        or before.st_gid != CONTROL_OWNER_GID
        or before.st_nlink != 1
        or mode not in {0o400, 0o600}
    ):
        raise StopEvidenceError("the Render control file is not a private root-owned regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StopEvidenceError("the Render control file could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_identity(before, opened):
            raise StopEvidenceError("the Render control file changed while it was opened")
        data = bytearray()
        while len(data) <= MAX_CONTROL_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_CONTROL_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if not _same_identity(opened, after):
            raise StopEvidenceError("the Render control file changed while it was read")
    finally:
        os.close(descriptor)
    return parse_control(bytes(data))


def _strict_json(data: bytes) -> object:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise StopEvidenceError("the Render API response is not strict UTF-8") from exc

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise StopEvidenceError("the Render API response contains duplicate JSON fields")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=no_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                StopEvidenceError("the Render API response contains a non-finite number")
            ),
        )
    except json.JSONDecodeError as exc:
        raise StopEvidenceError("the Render API response is not valid JSON") from exc


def _get_json(connection: http.client.HTTPSConnection, path: str, token: str) -> object:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "LectureSift-Cutover/1",
    }
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        data = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        raise StopEvidenceError("the Render API GET request failed") from exc
    if response.status != 200:
        raise StopEvidenceError(f"the Render API returned HTTP {response.status}")
    if content_type != "application/json":
        raise StopEvidenceError("the Render API response is not JSON")
    if len(data) > MAX_RESPONSE_BYTES:
        raise StopEvidenceError("the Render API response exceeds the safety limit")
    return _strict_json(data)


def worker_stop_digest(
    control: dict[str, str],
    *,
    connection_factory: Callable[..., http.client.HTTPSConnection] = http.client.HTTPSConnection,
) -> str:
    service_id = control["RENDER_WORKER_SERVICE_ID"]
    service_name = control["RENDER_WORKER_SERVICE_NAME"]
    token = control["RENDER_API_TOKEN"]
    context = ssl.create_default_context()
    connection = connection_factory(API_HOST, API_PORT, context=context, timeout=15)
    try:
        service = _get_json(connection, f"/v1/services/{service_id}", token)
        instances = _get_json(connection, f"/v1/services/{service_id}/instances", token)
    finally:
        try:
            connection.close()
        except Exception:
            pass
    if not isinstance(service, dict):
        raise StopEvidenceError("the Render service response is not an object")
    owner_id = service.get("ownerId")
    suspenders = service.get("suspenders")
    if (
        service.get("id") != service_id
        or service.get("name") != service_name
        or service.get("type") != "background_worker"
        or service.get("suspended") != "suspended"
        or not isinstance(owner_id, str)
        or _OWNER_ID.fullmatch(owner_id) is None
        or not isinstance(suspenders, list)
        or any(not isinstance(value, str) or value not in _ALLOWED_SUSPENDERS for value in suspenders)
        or len(set(suspenders)) != len(suspenders)
    ):
        raise StopEvidenceError("the Render worker identity or suspended state is not exact")
    if not isinstance(instances, list) or instances:
        raise StopEvidenceError("the suspended Render worker still has a listed instance")
    canonical = {
        "instances": [],
        "schema": SCHEMA,
        "service": {
            "id": service_id,
            "name": service_name,
            "ownerId": owner_id,
            "suspended": "suspended",
            "suspenders": sorted(suspenders),
            "type": "background_worker",
        },
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    try:
        control = load_control()
        print(worker_stop_digest(control))
        return 0
    except StopEvidenceError as exc:
        print(f"Render worker stop proof failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("Render worker stop proof failed unexpectedly", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
