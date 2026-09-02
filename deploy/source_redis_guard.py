#!/usr/bin/env python3
"""Read-only TLS verification/export for the frozen source Redis endpoints.

This helper deliberately uses only the Python standard library.  It never runs
the release-candidate image and it reads both SOURCE_REDIS_URL and
SOURCE_CELERY_BROKER_URL from the process environment so credentials do not
enter argv or diagnostics.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import tempfile
from typing import Callable
from urllib.parse import parse_qsl, unquote, urlsplit


STATE_KEY = "lecturesift:jobs:v2"
WRITE_LOCK_KEY = "lecturesift:jobs:v2:write-lock"
PROCESSING_LOCK_PATTERN = "lecturesift:job:*:processing"
MAX_REDIS_BULK_BYTES = 64 * 1024 * 1024
MAX_REDIS_ARRAY_ITEMS = 1_000_000
MAX_REDIS_LINE_BYTES = 64 * 1024


STATE_LUA = r'''
local function type_name(key)
  local reply = redis.call("TYPE", key)
  if type(reply) == "table" then return reply.ok end
  return reply
end
local state_type = type_name(KEYS[1])
if state_type ~= "none" and state_type ~= "string" then
  return redis.error_reply(KEYS[1] .. " has unexpected Redis type " .. state_type)
end
local lock_type = type_name(KEYS[2])
if lock_type ~= "none" and lock_type ~= "string" then
  return redis.error_reply(KEYS[2] .. " has unexpected Redis type " .. lock_type)
end
local state = redis.call("GET", KEYS[1])
if not state then state = "" end
local cursor = "0"
local processing = 0
repeat
  local scanned = redis.call("SCAN", cursor, "MATCH", ARGV[1], "COUNT", 250)
  cursor = scanned[1]
  processing = processing + #scanned[2]
until cursor == "0"
return {state, processing, redis.call("EXISTS", KEYS[2])}
'''.strip()


BROKER_LUA = r'''
local function type_name(key)
  local reply = redis.call("TYPE", key)
  if type(reply) == "table" then return reply.ok end
  return reply
end
local cursor = "0"
local queued = 0
repeat
  local scanned = redis.call("SCAN", cursor, "COUNT", 250)
  cursor = scanned[1]
  for _, key in ipairs(scanned[2]) do
    if type_name(key) == "list" then
      queued = queued + redis.call("LLEN", key)
    end
  end
until cursor == "0"
local function checked_size(key, expected_type, command)
  local actual_type = type_name(key)
  if actual_type == "none" then return 0, nil end
  if actual_type ~= expected_type then
    return 0, key .. " has unexpected Redis type " .. actual_type
  end
  return redis.call(command, key), nil
end
local unacked, unacked_error = checked_size(KEYS[1], "hash", "HLEN")
if unacked_error then return redis.error_reply(unacked_error) end
local unacked_index, index_error = checked_size(KEYS[2], "zset", "ZCARD")
if index_error then return redis.error_reply(index_error) end
return {queued, unacked, unacked_index}
'''.strip()


class GuardError(RuntimeError):
    """Expected, secret-free validation failure."""


class RedisCommandError(GuardError):
    """Redis returned an error without exposing its untrusted text."""


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    username: str | None
    password: str
    database: int


def resolve_public_addresses(endpoint: Endpoint):
    try:
        addresses = socket.getaddrinfo(
            endpoint.host,
            endpoint.port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise GuardError("source Redis hostname could not be resolved") from exc
    if not addresses:
        raise GuardError("source Redis hostname returned no addresses")
    result = []
    seen = set()
    for family, socket_type, protocol, _canonical_name, socket_address in addresses:
        raw_address = str(socket_address[0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise GuardError("source Redis hostname returned an invalid address") from exc
        if not address.is_global:
            raise GuardError("source Redis hostname resolves to a non-public address")
        identity = (family, socket_type, protocol, socket_address)
        if identity not in seen:
            seen.add(identity)
            result.append(identity)
    return result


def parse_endpoint(raw: str, *, label: str) -> Endpoint:
    if not raw or len(raw) > 16_384 or any(ord(character) < 32 for character in raw):
        raise GuardError(f"{label} is missing or malformed")
    try:
        parsed = urlsplit(raw)
        port = parsed.port or 6379
    except ValueError as exc:
        raise GuardError(f"{label} is malformed") from exc
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "rediss"
        or not host
        or host in {"redis", "localhost", "127.0.0.1", "::1", "0.0.0.0"}
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise GuardError(f"{label} must be a remote TLS Redis endpoint")
    if parsed.path in {"", "/"}:
        database = 0
    elif re.fullmatch(r"/[0-9]+", parsed.path):
        database = int(parsed.path[1:])
    else:
        raise GuardError(f"{label} has an invalid database path")
    if database > 2_147_483_647:
        raise GuardError(f"{label} has an invalid database number")
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise GuardError(f"{label} has a malformed TLS query") from exc
    allowed_tls_values = {"required", "cert_required"}
    if any(
        key.casefold() != "ssl_cert_reqs" or value.casefold() not in allowed_tls_values
        for key, value in query
    ):
        raise GuardError(f"{label} contains unsupported or unsafe TLS options")
    username = unquote(parsed.username) if parsed.username is not None else None
    if username == "":
        username = None
    password = unquote(parsed.password) if parsed.password is not None else ""
    for credential in (username, password):
        if credential is not None and (
            not credential
            or len(credential) > 4096
            or any(ord(character) < 32 for character in credential)
        ):
            raise GuardError(f"{label} credentials are missing or malformed")
    if not password:
        raise GuardError(f"{label} credentials are missing or malformed")
    return Endpoint(host, port, username, password, database)


def _encode_command(parts: tuple[object, ...]) -> bytes:
    encoded: list[bytes] = []
    for part in parts:
        if isinstance(part, bytes):
            encoded.append(part)
        elif isinstance(part, str):
            encoded.append(part.encode("utf-8"))
        elif isinstance(part, int):
            encoded.append(str(part).encode("ascii"))
        else:
            raise GuardError("the Redis command contains an unsupported argument")
    payload = [f"*{len(encoded)}\r\n".encode("ascii")]
    for part in encoded:
        payload.extend((f"${len(part)}\r\n".encode("ascii"), part, b"\r\n"))
    return b"".join(payload)


class RedisConnection:
    def __init__(self, endpoint: Endpoint) -> None:
        self.endpoint = endpoint
        self._socket: ssl.SSLSocket | None = None
        self._reader = None

    def __enter__(self) -> "RedisConnection":
        plain = None
        last_error: OSError | None = None
        for family, socket_type, protocol, socket_address in resolve_public_addresses(
            self.endpoint
        ):
            candidate = socket.socket(family, socket_type, protocol)
            candidate.settimeout(10)
            try:
                candidate.connect(socket_address)
            except OSError as exc:
                last_error = exc
                candidate.close()
                continue
            plain = candidate
            break
        if plain is None:
            raise GuardError("source Redis public endpoint could not be reached") from last_error
        plain.settimeout(20)
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            self._socket = context.wrap_socket(
                plain, server_hostname=self.endpoint.host
            )
        except BaseException:
            plain.close()
            raise
        self._socket.settimeout(20)
        self._reader = self._socket.makefile("rb")
        try:
            if self.endpoint.username is None:
                authenticated = self.command("AUTH", self.endpoint.password)
            else:
                authenticated = self.command(
                    "AUTH", self.endpoint.username, self.endpoint.password
                )
            if authenticated != b"OK":
                raise GuardError("Redis authentication was not acknowledged")
            if self.endpoint.database:
                selected = self.command("SELECT", self.endpoint.database)
                if selected != b"OK":
                    raise GuardError("Redis database selection was not acknowledged")
            if self.command("PING") != b"PONG":
                raise GuardError("Redis did not acknowledge the health probe")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        if self._reader is not None:
            self._reader.close()
        if self._socket is not None:
            self._socket.close()

    def _line(self) -> bytes:
        if self._reader is None:
            raise GuardError("Redis connection is not open")
        line = self._reader.readline(MAX_REDIS_LINE_BYTES + 1)
        if not line or len(line) > MAX_REDIS_LINE_BYTES or not line.endswith(b"\r\n"):
            raise GuardError("Redis returned an invalid protocol line")
        return line[:-2]

    def _exact(self, length: int) -> bytes:
        if self._reader is None:
            raise GuardError("Redis connection is not open")
        value = self._reader.read(length)
        if value is None or len(value) != length:
            raise GuardError("Redis returned a truncated protocol value")
        return value

    def _response(self):
        prefix = self._exact(1)
        if prefix == b"+":
            return self._line()
        if prefix == b"-":
            self._line()
            raise RedisCommandError("Redis rejected a read-only verification command")
        if prefix == b":":
            try:
                return int(self._line())
            except ValueError as exc:
                raise GuardError("Redis returned an invalid integer") from exc
        if prefix == b"$":
            try:
                length = int(self._line())
            except ValueError as exc:
                raise GuardError("Redis returned an invalid bulk length") from exc
            if length == -1:
                return None
            if length < 0 or length > MAX_REDIS_BULK_BYTES:
                raise GuardError("Redis returned an unsafe bulk length")
            value = self._exact(length)
            if self._exact(2) != b"\r\n":
                raise GuardError("Redis returned an invalid bulk terminator")
            return value
        if prefix == b"*":
            try:
                length = int(self._line())
            except ValueError as exc:
                raise GuardError("Redis returned an invalid array length") from exc
            if length == -1:
                return None
            if length < 0 or length > MAX_REDIS_ARRAY_ITEMS:
                raise GuardError("Redis returned an unsafe array length")
            return [self._response() for _ in range(length)]
        raise GuardError("Redis returned an unsupported protocol response")

    def command(self, *parts: object):
        if self._socket is None:
            raise GuardError("Redis connection is not open")
        self._socket.sendall(_encode_command(parts))
        return self._response()


def _state_read(connection: RedisConnection) -> bytes:
    response = connection.command(
        "EVAL_RO",
        STATE_LUA,
        2,
        STATE_KEY,
        WRITE_LOCK_KEY,
        PROCESSING_LOCK_PATTERN,
    )
    if (
        not isinstance(response, list)
        or len(response) != 3
        or not isinstance(response[0], bytes)
        or not isinstance(response[1], int)
        or not isinstance(response[2], int)
        or response[1] < 0
        or response[2] not in {0, 1}
    ):
        raise GuardError("source Redis returned malformed state evidence")
    if response[1] or response[2]:
        raise GuardError("source Redis still has active job locks")
    return response[0]


def _broker_idle(connection: RedisConnection) -> None:
    response = connection.command(
        "EVAL_RO", BROKER_LUA, 2, "unacked", "unacked_index"
    )
    if (
        not isinstance(response, list)
        or len(response) != 3
        or any(not isinstance(value, int) or value < 0 for value in response)
    ):
        raise GuardError("source Celery broker returned malformed queue evidence")
    if any(response):
        raise GuardError("source Celery broker still contains queued or unacknowledged work")


def _decode_state(raw: bytes) -> tuple[dict[str, object], bytes, dict[str, int]]:
    if not raw:
        raw = b'{"version":2,"saved_at":0,"jobs":{}}'

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise GuardError("source Redis job state contains duplicate JSON fields")
            result[key] = value
        return result

    def reject_constant(_value: str):
        raise GuardError("source Redis job state contains a non-finite number")

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GuardError("source Redis job state is malformed") from exc
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise GuardError("source Redis job state has an unsupported schema")
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict) or any(
        not isinstance(job, dict) for job in jobs.values()
    ):
        raise GuardError("source Redis jobs payload is malformed")
    active = [
        job_id
        for job_id, job in jobs.items()
        if str(job.get("status") or "") in {"queued", "working"}
    ]
    if active:
        raise GuardError("source Redis still contains queued or working jobs")
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GuardError("source Redis job state cannot be canonicalized") from exc
    terminal_counts: dict[str, int] = {"done": 0, "error": 0, "unknown": 0}
    for job in jobs.values():
        status = str(job.get("status") or "unknown")
        bucket = status if status in {"done", "error"} else "unknown"
        terminal_counts[bucket] += 1
    return payload, canonical, terminal_counts


ConnectionFactory = Callable[[Endpoint], RedisConnection]


def inspect_source(
    state_url: str,
    broker_url: str,
    *,
    connection_factory: ConnectionFactory = RedisConnection,
) -> tuple[bytes, int, dict[str, int]]:
    state_endpoint = parse_endpoint(state_url, label="SOURCE_REDIS_URL")
    broker_endpoint = parse_endpoint(
        broker_url, label="SOURCE_CELERY_BROKER_URL"
    )
    with connection_factory(state_endpoint) as state_connection:
        before = _state_read(state_connection)
        _payload, _canonical, _counts = _decode_state(before)
        with connection_factory(broker_endpoint) as broker_connection:
            _broker_idle(broker_connection)
        after = _state_read(state_connection)
    if before != after:
        raise GuardError("source Redis job state changed during verification")
    payload, canonical, terminal_counts = _decode_state(after)
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    return canonical, len(jobs), terminal_counts


def _write_private_atomic(destination: Path, payload: bytes) -> None:
    if not destination.is_absolute() or destination.name not in {
        "source-before.json",
        "source-after.json",
        "source-final.json",
    }:
        raise GuardError("refusing an unexpected Redis migration output path")
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink() or destination.exists() or destination.is_symlink():
        raise GuardError("Redis migration output location is missing or unsafe")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("assert-idle")
    export = subparsers.add_parser("export")
    export.add_argument("output", type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        canonical, jobs, terminal_counts = inspect_source(
            os.environ.get("SOURCE_REDIS_URL", ""),
            os.environ.get("SOURCE_CELERY_BROKER_URL", ""),
        )
        digest = hashlib.sha256(canonical).hexdigest()
        if arguments.command == "export":
            _write_private_atomic(arguments.output, canonical)
        print(
            json.dumps(
                {
                    "active_locks": 0,
                    "jobs": jobs,
                    "ok": True,
                    "sha256": digest,
                    "terminal_counts": terminal_counts,
                    "verified_endpoints": [
                        "SOURCE_REDIS_URL",
                        "SOURCE_CELERY_BROKER_URL",
                    ],
                },
                sort_keys=True,
            )
        )
        return 0
    except GuardError as exc:
        print(f"Source Redis verification failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, ssl.SSLError, ValueError):
        print(
            "Source Redis verification failed: TLS transport or protocol failure",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
