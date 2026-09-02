#!/usr/bin/env python3
"""Create a confidential, read-only digest of the target Redis keyspace."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
from typing import Final, Protocol


SALT_FILE: Final = Path("/var/lib/lecturesift/provider-cutover/redis-manifest.salt")
SALT_OWNER_UID: Final = 0
SALT_OWNER_GID: Final = 0
TARGET_REDIS_PORT: Final = 6379
MIGRATION_LOCK_KEY: Final = b"lecturesift:jobs:v2:write-lock"
JOB_STATE_KEY: Final = b"lecturesift:jobs:v2"
SCHEMA: Final = "lecturesift-redis-logical-manifest-v1"
TTL_POLICY: Final = "absolute-pexpiretime-unix-ms-v1"
MAX_SECRET_BYTES: Final = 4096
MAX_KEYS: Final = 1_000_000
MAX_BULK_BYTES: Final = 256 * 1024 * 1024
MAX_LINE_BYTES: Final = 64 * 1024
_SALT = re.compile(rb"[0-9a-f]{64}\n")
_LOCK_TOKEN = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\n")


class ManifestError(RuntimeError):
    """A fail-closed Redis manifest error."""


class RedisReader(Protocol):
    def ping(self) -> object: ...
    def scan(self, cursor: int, *, count: int) -> tuple[int, list[bytes]]: ...
    def type(self, key: bytes) -> bytes: ...
    def dump(self, key: bytes) -> bytes | None: ...
    def get(self, key: bytes) -> bytes | None: ...
    def pttl(self, key: bytes) -> int: ...
    def execute_command(self, command: str, key: bytes) -> int: ...


class RESPRedisReader:
    """Minimal RESP2 reader exposing only the commands used by this proof."""

    def __init__(self, host: str, *, timeout: float = 30.0):
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ManifestError("the target Redis address is invalid") from exc
        if address.version != 4 or not address.is_private or address.is_loopback:
            raise ManifestError("the target Redis address must be a private non-loopback IPv4 address")
        try:
            self._socket = socket.create_connection((host, TARGET_REDIS_PORT), timeout=8)
            self._socket.settimeout(timeout)
            self._reader = self._socket.makefile("rb", buffering=0)
        except OSError as exc:
            raise ManifestError("the target Redis connection failed") from exc

    def close(self) -> None:
        try:
            self._reader.close()
        finally:
            self._socket.close()

    @staticmethod
    def _encode_part(value: bytes | str | int) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, int):
            return str(value).encode("ascii")
        try:
            return value.encode("ascii", errors="strict")
        except UnicodeEncodeError as exc:
            raise ManifestError("a Redis command contains non-ASCII text") from exc

    def _read_exact(self, size: int) -> bytes:
        if size < 0 or size > MAX_BULK_BYTES:
            raise ManifestError("a Redis bulk response exceeds the safety limit")
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self._reader.read(size - len(chunks))
            if not chunk:
                raise ManifestError("the Redis response ended unexpectedly")
            chunks.extend(chunk)
        return bytes(chunks)

    def _read_line(self) -> bytes:
        line = self._reader.readline(MAX_LINE_BYTES + 1)
        if not line or len(line) > MAX_LINE_BYTES or not line.endswith(b"\r\n"):
            raise ManifestError("Redis returned an invalid response line")
        return line[:-2]

    def _read_response(self, depth: int = 0) -> object:
        if depth > 8:
            raise ManifestError("the Redis response nesting exceeds the safety limit")
        prefix = self._read_exact(1)
        if prefix == b"+":
            return self._read_line()
        if prefix == b"-":
            self._read_line()
            raise ManifestError("Redis returned an error response")
        if prefix == b":":
            try:
                return int(self._read_line())
            except ValueError as exc:
                raise ManifestError("Redis returned an invalid integer") from exc
        if prefix == b"$":
            try:
                length = int(self._read_line())
            except ValueError as exc:
                raise ManifestError("Redis returned an invalid bulk length") from exc
            if length == -1:
                return None
            payload = self._read_exact(length)
            if self._read_exact(2) != b"\r\n":
                raise ManifestError("Redis returned an invalid bulk terminator")
            return payload
        if prefix == b"*":
            try:
                length = int(self._read_line())
            except ValueError as exc:
                raise ManifestError("Redis returned an invalid array length") from exc
            if length < 0 or length > MAX_KEYS + 16:
                raise ManifestError("the Redis array response exceeds the safety limit")
            return [self._read_response(depth + 1) for _ in range(length)]
        raise ManifestError("Redis returned an unsupported RESP type")

    def _command(self, *parts: bytes | str | int) -> object:
        encoded = [self._encode_part(part) for part in parts]
        request = bytearray(f"*{len(encoded)}\r\n".encode("ascii"))
        for part in encoded:
            request.extend(f"${len(part)}\r\n".encode("ascii"))
            request.extend(part)
            request.extend(b"\r\n")
        try:
            self._socket.sendall(request)
            return self._read_response()
        except ManifestError:
            raise
        except OSError as exc:
            raise ManifestError("the Redis read-only request failed") from exc

    def ping(self) -> object:
        return self._command("PING") == b"PONG"

    def scan(self, cursor: int, *, count: int) -> tuple[int, list[bytes]]:
        response = self._command("SCAN", cursor, "COUNT", count)
        if (
            not isinstance(response, list)
            or len(response) != 2
            or not isinstance(response[0], bytes)
            or not isinstance(response[1], list)
            or any(not isinstance(key, bytes) for key in response[1])
        ):
            raise ManifestError("Redis returned an invalid SCAN response")
        try:
            next_cursor = int(response[0])
        except ValueError as exc:
            raise ManifestError("Redis returned an invalid SCAN cursor") from exc
        return next_cursor, response[1]

    def type(self, key: bytes) -> bytes:
        response = self._command("TYPE", key)
        if not isinstance(response, bytes):
            raise ManifestError("Redis returned an invalid TYPE response")
        return response

    def dump(self, key: bytes) -> bytes | None:
        response = self._command("DUMP", key)
        if response is not None and not isinstance(response, bytes):
            raise ManifestError("Redis returned an invalid DUMP response")
        return response

    def get(self, key: bytes) -> bytes | None:
        response = self._command("GET", key)
        if response is not None and not isinstance(response, bytes):
            raise ManifestError("Redis returned an invalid GET response")
        return response

    def pttl(self, key: bytes) -> int:
        response = self._command("PTTL", key)
        if not isinstance(response, int):
            raise ManifestError("Redis returned an invalid PTTL response")
        return response

    def execute_command(self, command: str, key: bytes) -> int:
        if command != "PEXPIRETIME":
            raise ManifestError("the Redis reader forbids this command")
        response = self._command("PEXPIRETIME", key)
        if not isinstance(response, int):
            raise ManifestError("Redis returned an invalid PEXPIRETIME response")
        return response


@dataclass(frozen=True)
class Manifest:
    digest: str
    key_count: int


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


def _read_private_file(path: Path, pattern: re.Pattern[bytes], label: str) -> bytes:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise ManifestError(f"the {label} path must be absolute and canonical")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ManifestError(f"the {label} is missing") from exc
    mode = stat.S_IMODE(before.st_mode)
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != SALT_OWNER_UID
        or before.st_gid != SALT_OWNER_GID
        or before.st_nlink != 1
        or mode not in {0o400, 0o600}
    ):
        raise ManifestError(f"the {label} must be a private root-owned regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestError(f"the {label} could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_identity(before, opened):
            raise ManifestError(f"the {label} changed while it was opened")
        data = os.read(descriptor, MAX_SECRET_BYTES + 1)
        if os.read(descriptor, 1):
            raise ManifestError(f"the {label} exceeds the safety limit")
        after = os.fstat(descriptor)
        if not _same_identity(opened, after):
            raise ManifestError(f"the {label} changed while it was read")
    finally:
        os.close(descriptor)
    if pattern.fullmatch(data) is None:
        raise ManifestError(f"the {label} has an invalid format")
    return data[:-1]


def load_salt(path: Path = SALT_FILE) -> bytes:
    return bytes.fromhex(_read_private_file(path, _SALT, "Redis manifest salt").decode("ascii"))


def initialize_salt(path: Path = SALT_FILE) -> None:
    parent = path.parent
    if not path.is_absolute() or path != SALT_FILE:
        raise ManifestError("the Redis manifest salt path is fixed")
    try:
        details = os.lstat(parent)
    except OSError as exc:
        raise ManifestError("the provider-cutover evidence root is missing") from exc
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != SALT_OWNER_UID
        or details.st_gid != SALT_OWNER_GID
        or stat.S_IMODE(details.st_mode) != 0o700
        or Path(os.path.realpath(parent)) != parent
    ):
        raise ManifestError("the provider-cutover evidence root is not canonical root:root mode 0700")
    if path.exists() or path.is_symlink():
        load_salt(path)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        os.write(descriptor, secrets.token_hex(32).encode("ascii") + b"\n")
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, SALT_OWNER_UID, SALT_OWNER_GID)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    load_salt(path)


def load_lock_token(path: Path) -> bytes:
    return _read_private_file(path, _LOCK_TOKEN, "Redis migration lock token")


def _hmac_hex(salt: bytes, *parts: bytes) -> str:
    digest = hmac.new(salt, digestmod=hashlib.sha256)
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _lock_is_valid(client: RedisReader, policy: str, expected_lock_token: bytes | None) -> None:
    kind = client.type(MIGRATION_LOCK_KEY)
    if kind == b"none":
        if policy != "steady":
            raise ManifestError("the expected Redis migration lock is absent")
        return
    if policy != "migration" or kind != b"string" or expected_lock_token is None:
        raise ManifestError("an unapproved Redis migration lock is present")
    value = client.get(MIGRATION_LOCK_KEY)
    ttl = client.pttl(MIGRATION_LOCK_KEY)
    if value is None or not hmac.compare_digest(value, expected_lock_token) or ttl <= 0:
        raise ManifestError("the Redis migration lock token or expiry is invalid")


def _scan_keys(client: RedisReader) -> list[bytes]:
    cursor = 0
    keys: set[bytes] = set()
    while True:
        cursor, batch = client.scan(cursor, count=500)
        if not isinstance(cursor, int) or not isinstance(batch, list):
            raise ManifestError("Redis returned an invalid SCAN response")
        for key in batch:
            if not isinstance(key, bytes):
                raise ManifestError("Redis returned a non-binary key")
            keys.add(key)
            if len(keys) > MAX_KEYS:
                raise ManifestError("the Redis keyspace exceeds the manifest safety limit")
        if cursor == 0:
            return sorted(keys)


def _collect(
    client: RedisReader,
    salt: bytes,
    *,
    policy: str,
    projection: str,
    expected_lock_token: bytes | None,
) -> Manifest:
    if policy not in {"steady", "migration"} or projection not in {"full", "non-job"}:
        raise ManifestError("an invalid Redis manifest policy was requested")
    if client.ping() is not True:
        raise ManifestError("the target Redis PING proof failed")
    _lock_is_valid(client, policy, expected_lock_token)
    records: list[dict[str, object]] = []
    for key in _scan_keys(client):
        if key == MIGRATION_LOCK_KEY:
            continue
        if projection == "non-job" and key == JOB_STATE_KEY:
            continue
        kind_raw = client.type(key)
        if not isinstance(kind_raw, bytes) or kind_raw in {b"none", b""}:
            raise ManifestError("a Redis key disappeared during manifest capture")
        try:
            kind = kind_raw.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise ManifestError("Redis returned an invalid key type") from exc
        dump = client.dump(key)
        expiry = client.execute_command("PEXPIRETIME", key)
        if not isinstance(dump, bytes) or not isinstance(expiry, int) or expiry == -2 or expiry < -1:
            raise ManifestError("a Redis value or expiry changed during manifest capture")
        ttl = "persistent" if expiry == -1 else f"expires-at-unix-ms:{expiry}"
        records.append(
            {
                "dump_hmac_sha256": _hmac_hex(
                    salt, b"value", key, kind.encode("ascii"), dump
                ),
                "key_hmac_sha256": _hmac_hex(salt, b"key", key),
                "ttl": ttl,
                "type": kind,
            }
        )
    records.sort(key=lambda item: str(item["key_hmac_sha256"]))
    canonical = {
        "key_count": len(records),
        "projection": projection,
        "records": records,
        "schema": SCHEMA,
        "ttl_policy": TTL_POLICY,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(salt, b"manifest\0" + encoded, hashlib.sha256).hexdigest()
    return Manifest(digest=digest, key_count=len(records))


def logical_manifest(
    client: RedisReader,
    salt: bytes,
    *,
    policy: str,
    projection: str,
    expected_lock_token: bytes | None = None,
) -> Manifest:
    first = _collect(
        client,
        salt,
        policy=policy,
        projection=projection,
        expected_lock_token=expected_lock_token,
    )
    second = _collect(
        client,
        salt,
        policy=policy,
        projection=projection,
        expected_lock_token=expected_lock_token,
    )
    if first != second:
        raise ManifestError("the Redis keyspace changed between manifest passes")
    return first


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-salt")
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--host", required=True)
    manifest.add_argument("--policy", choices=("steady", "migration"), required=True)
    manifest.add_argument("--projection", choices=("full", "non-job"), default="full")
    manifest.add_argument("--lock-token-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise ManifestError("the Redis manifest helper must run as root")
        if args.command == "init-salt":
            initialize_salt()
            return 0
        if args.policy == "migration":
            if args.lock_token_file is None:
                raise ManifestError("migration policy requires a private lock-token file")
            lock_token = load_lock_token(args.lock_token_file)
        else:
            if args.lock_token_file is not None:
                raise ManifestError("steady policy forbids a lock-token file")
            lock_token = None
        client = RESPRedisReader(args.host)
        try:
            result = logical_manifest(
                client,
                load_salt(),
                policy=args.policy,
                projection=args.projection,
                expected_lock_token=lock_token,
            )
        finally:
            client.close()
        print(result.digest)
        return 0
    except ManifestError as exc:
        print(f"Redis manifest failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("Redis manifest failed unexpectedly", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
