#!/usr/bin/env python3
"""Execute trusted source operations without putting PostgreSQL secrets in argv.

The fixed root-only source dotenv is parsed strictly as data.  PostgreSQL
clients receive only canonical libpq variables, with hostname verification
forced by PGSSLMODE=verify-full.  The original URL and its password are never
rendered to stdout/stderr or appended to a child command line.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qsl, unquote, urlsplit


SOURCE_KEYS = {
    "SOURCE_DATABASE_URL",
    "SOURCE_HEALTH_URL",
    "SOURCE_REDIS_URL",
    "SOURCE_CELERY_BROKER_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
}
SOURCE_SCOPE_KEYS = {
    "fingerprint": {
        "SOURCE_DATABASE_URL",
        "SOURCE_HEALTH_URL",
        "SOURCE_REDIS_URL",
        "SOURCE_CELERY_BROKER_URL",
    },
    "health": {"SOURCE_HEALTH_URL"},
    "redis": {"SOURCE_REDIS_URL", "SOURCE_CELERY_BROKER_URL"},
}
RUNTIME_ROOT = Path("/run/lecturesift-source-postgres")
CONTAINER_PGPASSFILE = "/run/secrets/lecturesift-source.pgpass"
ASSIGNMENT = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
SESSION_NAME = re.compile(r"^session-[a-z0-9_]{8,32}$")


class TransportError(RuntimeError):
    """A secret-free source transport validation failure."""


@dataclass(frozen=True)
class SourceConfiguration:
    database_url: str
    health_url: str
    redis_url: str
    broker_url: str
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)


def _private_source(path: Path) -> None:
    if not path.is_absolute() or path != Path(os.path.realpath(path)):
        raise TransportError("source environment path must be absolute and canonical")
    try:
        details = path.lstat()
    except OSError as exc:
        raise TransportError("source environment is missing or inaccessible") from exc
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_nlink != 1
    ):
        raise TransportError(
            "source environment must be a single-link regular non-symlink file"
        )
    if os.name == "posix" and (details.st_uid != 0 or details.st_gid != 0):
        raise TransportError("source environment must be owned by root")
    if os.name == "posix" and stat.S_IMODE(details.st_mode) not in {0o400, 0o600}:
        raise TransportError("source environment must have mode 0400 or 0600")
    if details.st_size > 64 * 1024:
        raise TransportError("source environment exceeds the supported size")


def _parse_dotenv_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(raw)
        if not match:
            raise TransportError(f"invalid source dotenv syntax at line {number}")
        key, encoded = match.groups()
        if key not in SOURCE_KEYS:
            raise TransportError(f"unexpected source dotenv key at line {number}")
        if key in values:
            raise TransportError(f"duplicate source dotenv key at line {number}")
        lexer = shlex.shlex(encoded, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            words = list(lexer)
        except ValueError as exc:
            raise TransportError(f"invalid source dotenv quoting at line {number}") from exc
        if len(words) != 1 or any(character in words[0] for character in "\x00\r\n"):
            raise TransportError(f"invalid source dotenv value at line {number}")
        values[key] = words[0]
    return values


def parse_dotenv(path: Path) -> dict[str, str]:
    _private_source(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise TransportError("source environment could not be read as UTF-8") from exc
    return _parse_dotenv_lines(lines)


def _required_text(value: str, label: str) -> str:
    if not value or any(character in value for character in "\x00\r\n"):
        raise TransportError(f"source {label} is missing or invalid")
    return value


def _postgres_endpoint(value: str) -> tuple[str, int, str, str, str]:
    try:
        parsed = urlsplit(value)
        port = parsed.port or 5432
    except ValueError as exc:
        raise TransportError("source PostgreSQL URL is invalid") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = unquote(parsed.path.removeprefix("/"))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    safe_parts = (host, user, password, database)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not host.endswith(".render.com")
        or host in {"localhost", "postgres", "127.0.0.1", "::1"}
        or not 1 <= port <= 65535
        or not all(safe_parts)
        or "/" in database
        or ";" in parsed.path
        or parsed.fragment
        or query != [("sslmode", "verify-full")]
        or any(any(character in part for character in "\x00\r\n") for part in safe_parts)
    ):
        raise TransportError(
            "source PostgreSQL must be the external Render endpoint with sslmode=verify-full"
        )
    return host, port, database, user, password


def _health_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port or 443
    except ValueError as exc:
        raise TransportError("source health URL is invalid") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host.endswith(".onrender.com")
        or port != 443
        or not parsed.path.rstrip("/").endswith("/health")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise TransportError("source health URL is not the direct HTTPS Render endpoint")
    return value


def _redis_endpoint(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port or 6379
    except ValueError as exc:
        raise TransportError(f"source {label} URL is invalid") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "rediss"
        or not host.endswith(".render.com")
        or port != 6379
        or not parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise TransportError(f"source {label} must be the external TLS Render endpoint")
    return value


def configuration_from_values(values: dict[str, str]) -> SourceConfiguration:
    database_url = _required_text(values.get("SOURCE_DATABASE_URL", ""), "database URL")
    health_url = _health_endpoint(
        _required_text(values.get("SOURCE_HEALTH_URL", ""), "health URL")
    )
    redis_url = _redis_endpoint(
        _required_text(values.get("SOURCE_REDIS_URL") or values.get("REDIS_URL", ""), "Redis URL"),
        "Redis",
    )
    broker_url = _redis_endpoint(
        _required_text(
            values.get("SOURCE_CELERY_BROKER_URL") or values.get("CELERY_BROKER_URL", ""),
            "Celery broker URL",
        ),
        "Celery broker",
    )
    host, port, database, user, password = _postgres_endpoint(database_url)
    return SourceConfiguration(
        database_url=database_url,
        health_url=health_url,
        redis_url=redis_url,
        broker_url=broker_url,
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
    )


def load_configuration(path: Path) -> SourceConfiguration:
    return configuration_from_values(parse_dotenv(path))


def source_environment(config: SourceConfiguration, scope: str) -> dict[str, str]:
    selected = SOURCE_SCOPE_KEYS.get(scope)
    if selected is None:
        raise TransportError("source child scope is invalid")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in SOURCE_KEYS and not key.startswith("PG")
    }
    values = {
        "SOURCE_DATABASE_URL": config.database_url,
        "SOURCE_HEALTH_URL": config.health_url,
        "SOURCE_REDIS_URL": config.redis_url,
        "SOURCE_CELERY_BROKER_URL": config.broker_url,
    }
    environment.update({key: values[key] for key in selected})
    return environment


def libpq_environment(config: SourceConfiguration) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in SOURCE_KEYS and not key.startswith("PG")
    }
    environment.update(
        {
            "PGHOST": config.host,
            "PGPORT": str(config.port),
            "PGDATABASE": config.database,
            "PGUSER": config.user,
            "PGSSLMODE": "verify-full",
            "PGSSLROOTCERT": "system",
            "PGCONNECT_TIMEOUT": "15",
        }
    )
    return environment


def _pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def pgpass_record(config: SourceConfiguration) -> str:
    fields = (
        config.host,
        str(config.port),
        config.database,
        config.user,
        config.password,
    )
    return ":".join(_pgpass_escape(value) for value in fields) + "\n"


def _private_runtime_root() -> Path:
    if os.name == "posix" and os.geteuid() != 0:
        raise TransportError("source transport must run as root")
    try:
        RUNTIME_ROOT.mkdir(mode=0o700, parents=False, exist_ok=True)
        details = RUNTIME_ROOT.lstat()
    except OSError as exc:
        raise TransportError("source transport runtime directory is unavailable") from exc
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or RUNTIME_ROOT != Path(os.path.realpath(RUNTIME_ROOT))
        or (os.name == "posix" and (details.st_uid != 0 or details.st_gid != 0))
        or (os.name == "posix" and stat.S_IMODE(details.st_mode) != 0o700)
    ):
        raise TransportError("source transport runtime directory is unsafe")
    return RUNTIME_ROOT


def _purge_expired_sessions(root: Path) -> None:
    cutoff = time.time() - 24 * 60 * 60
    try:
        children = list(root.iterdir())
    except OSError as exc:
        raise TransportError("source transport runtime directory is unreadable") from exc
    for child in children:
        try:
            details = child.lstat()
        except OSError as exc:
            raise TransportError("source transport runtime entry is inaccessible") from exc
        if not SESSION_NAME.fullmatch(child.name):
            raise TransportError("source transport runtime contains an unknown entry")
        if (
            not stat.S_ISDIR(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
            or (os.name == "posix" and (details.st_uid != 0 or details.st_gid != 0))
            or (os.name == "posix" and stat.S_IMODE(details.st_mode) != 0o700)
        ):
            raise TransportError("source transport session directory is unsafe")
        entries = list(child.iterdir())
        if len(entries) != 1 or entries[0].name != "pgpass":
            raise TransportError("source transport session contents are unsafe")
        pgpass = entries[0]
        pgpass_details = pgpass.lstat()
        if (
            not stat.S_ISREG(pgpass_details.st_mode)
            or stat.S_ISLNK(pgpass_details.st_mode)
            or pgpass_details.st_nlink != 1
            or (os.name == "posix" and (pgpass_details.st_uid != 0 or pgpass_details.st_gid != 0))
            or (os.name == "posix" and stat.S_IMODE(pgpass_details.st_mode) != 0o600)
        ):
            raise TransportError("source transport password file is unsafe")
        if max(details.st_mtime, pgpass_details.st_mtime) >= cutoff:
            continue
        try:
            pgpass.unlink()
            child.rmdir()
        except OSError as exc:
            raise TransportError("expired source transport session could not be removed") from exc


def _create_pgpass(config: SourceConfiguration) -> tuple[Path, Path]:
    root = _private_runtime_root()
    _purge_expired_sessions(root)
    session: Path | None = None
    pgpass: Path | None = None
    try:
        session = Path(tempfile.mkdtemp(prefix="session-", dir=root))
        if not SESSION_NAME.fullmatch(session.name) or session.parent != root:
            raise TransportError("source transport generated an unsafe session path")
        os.chmod(session, 0o700)
        if os.name == "posix":
            os.chown(session, 0, 0)
        pgpass = session / "pgpass"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(pgpass, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if os.name == "posix":
                os.fchown(descriptor, 0, 0)
            payload = pgpass_record(config).encode("utf-8")
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return session, pgpass
    except BaseException:
        if pgpass is not None:
            try:
                pgpass.unlink(missing_ok=True)
            except OSError:
                pass
        if session is not None:
            try:
                session.rmdir()
            except OSError:
                pass
        raise


def _remove_pgpass(session: Path, pgpass: Path) -> None:
    try:
        details = pgpass.lstat()
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
            or details.st_nlink != 1
        ):
            raise TransportError("source transport password file changed during execution")
        pgpass.unlink()
        session.rmdir()
    except FileNotFoundError:
        raise TransportError("source transport password file disappeared during execution")
    except OSError as exc:
        raise TransportError("source transport password file cleanup failed") from exc


class _ChildSignal(RuntimeError):
    def __init__(self, signum: int):
        super().__init__("source transport child interrupted")
        self.signum = signum


def _run_child(command: list[str], environment: dict[str, str]) -> int:
    process: subprocess.Popen[bytes] | None = None
    previous: dict[signal.Signals, object] = {}

    def interrupted(signum: int, _frame: object) -> None:
        raise _ChildSignal(signum)

    watched = tuple(
        current
        for current in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None))
        if current is not None
    )
    try:
        for current in watched:
            previous[current] = signal.signal(current, interrupted)
        process = subprocess.Popen(command, env=environment, close_fds=True)
        return process.wait()
    except _ChildSignal as exc:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return 128 + exc.signum
    except OSError as exc:
        raise TransportError("source transport child command could not be executed") from exc
    finally:
        for current, handler in previous.items():
            signal.signal(current, handler)


def _run_libpq_docker(command: list[str], config: SourceConfiguration) -> int:
    if command[:1] == ["--"]:
        command = command[1:]
    if command[:2] != ["docker", "run"]:
        raise TransportError("libpq password-file mode accepts only docker run")
    if any(
        "PGPASSWORD" in item
        or "PGPASSFILE" in item
        or config.password in item
        or config.database_url in item
        for item in command
    ):
        raise TransportError("libpq Docker command contains a forbidden credential argument")
    session, pgpass = _create_pgpass(config)
    try:
        mount = (
            f"type=bind,source={pgpass},target={CONTAINER_PGPASSFILE},readonly"
        )
        child = command[:2] + [
            "--mount",
            mount,
            "--env",
            f"PGPASSFILE={CONTAINER_PGPASSFILE}",
        ] + command[2:]
        return _run_child(child, libpq_environment(config))
    finally:
        _remove_pgpass(session, pgpass)


def _exec(command: list[str], environment: dict[str, str]) -> None:
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or not command[0] or any("\x00" in item for item in command):
        raise TransportError("a non-empty child command is required")
    try:
        os.execvpe(command[0], command, environment)
    except OSError as exc:
        raise TransportError("source transport child command could not be executed") from exc


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--source-env", required=True, type=Path)
    validate.set_defaults(command=[])
    source = subparsers.add_parser("exec-source")
    source.add_argument("--source-env", required=True, type=Path)
    source.add_argument("--scope", required=True, choices=tuple(SOURCE_SCOPE_KEYS))
    source.add_argument("command", nargs=argparse.REMAINDER)
    libpq = subparsers.add_parser("exec-libpq-docker")
    libpq.add_argument("--source-env", required=True, type=Path)
    libpq.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        configuration = load_configuration(arguments.source_env)
        if arguments.mode == "validate":
            if arguments.command:
                raise TransportError("validate mode does not accept a child command")
            print("SOURCE_POSTGRES_TRANSPORT_OK")
            return 0
        if arguments.mode == "exec-source":
            _exec(
                arguments.command,
                source_environment(configuration, arguments.scope),
            )
        else:
            return _run_libpq_docker(arguments.command, configuration)
    except TransportError as exc:
        print(f"Source PostgreSQL transport failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
