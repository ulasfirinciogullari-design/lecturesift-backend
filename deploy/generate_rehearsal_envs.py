#!/usr/bin/env python3
"""Generate isolated root-only API/worker environments for one rehearsal.

Production dotenv files are read only on the host so their secret values can
be excluded and compared.  They are never copied, sourced, mounted or passed
to a rehearsal container.  The generated files contain an explicit, minimal
allowlist plus dedicated rehearsal credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


ASSIGNMENT = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[0-9]{14}$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")

REHEARSAL_INPUT_KEYS = frozenset(
    {
        "LECTURESIFT_REHEARSAL_S3_ENDPOINT_URL",
        "LECTURESIFT_REHEARSAL_S3_REGION",
        "LECTURESIFT_REHEARSAL_S3_BUCKET",
        "LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID",
        "LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY",
        "LECTURESIFT_REHEARSAL_OPENAI_API_KEY",
        "LECTURESIFT_REHEARSAL_FORMAT_CASES",
        "LECTURESIFT_REHEARSAL_FORMAT_TIMEOUT_SECONDS",
    }
)

# These are processing controls rather than credentials.  Keeping the list
# explicit lets the rehearsal match production limits without allowing a
# future application or provider secret to cross the boundary by default.
SAFE_PROCESSING_KEYS = frozenset(
    {
        "LECTURESIFT_MAX_VIDEO_BYTES",
        "LECTURESIFT_MAX_SOURCE_FILES",
        "LECTURESIFT_MAX_DOCUMENT_BYTES",
        "LECTURESIFT_MAX_DOCUMENT_PAGES",
        "LECTURESIFT_MAX_DOCUMENT_CHARACTERS",
        "LECTURESIFT_DOCUMENT_WORDS_PER_CREDIT_MINUTE",
        "LECTURESIFT_JOB_TTL_SECONDS",
        "LECTURESIFT_HOST_DISK_RESERVE_BYTES",
        "LECTURESIFT_MAX_JOB_WORK_BYTES",
        "LECTURESIFT_TRANSCRIPTION_PARALLELISM",
        "LECTURESIFT_MEDIA_PREP_PARALLELISM",
        "LECTURESIFT_SLIDE_ANALYSIS_PARALLELISM",
        "LECTURESIFT_SLIDE_EXPORT_PARALLELISM",
        "LECTURESIFT_TRANSLATION_PARALLELISM",
        "LECTURESIFT_STUDY_PACK_PARALLELISM",
        "LECTURESIFT_SOURCE_DOWNLOAD_PARALLELISM",
        "LECTURESIFT_STORAGE_TRANSFER_PARALLELISM",
        "LECTURESIFT_STORAGE_FILE_TRANSFER_CONCURRENCY",
        "LECTURESIFT_DURATION_PROBE_PARALLELISM",
        "LECTURESIFT_ARTIFACT_EXPORT_PARALLELISM",
        "LECTURESIFT_PRECISE_TRANSCRIPT_TIMESTAMPS",
        "LECTURESIFT_OCR_ENABLED",
        "LECTURESIFT_OCR_COMMAND",
        "LECTURESIFT_OCR_DPI",
        "LECTURESIFT_OCR_MAX_PAGES",
        "LECTURESIFT_OCR_PARALLELISM",
        "LECTURESIFT_OCR_PAGE_TIMEOUT_SECONDS",
        "LECTURESIFT_OCR_MIN_NATIVE_CHARACTERS",
        "LECTURESIFT_OCR_ESTIMATED_WORDS_PER_PAGE",
    }
)

SENSITIVE_EXACT_KEYS = frozenset(
    {
        "ADMIN_ADMIN",
        "BILLING_LEGACY_SESSION_SECRET_HEX",
        "BILLING_SESSION_SECRET",
        "DATABASE_URL",
        "LECTURESIFT_WORKER_DATABASE_URL",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_ADMIN_TOKEN",
        "INSTAGRAM_APP_SECRET",
        "IYZICO_API_KEY",
        "IYZICO_SECRET_KEY",
        "OPENAI_API_KEY",
        "PAYMENT_TOKEN_BINDING_LEGACY_SECRET",
        "PAYMENT_TOKEN_BINDING_SECRET",
        "PAYTR_MERCHANT_KEY",
        "PAYTR_MERCHANT_SALT",
        "RESEND_API_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "SMTP_PASSWORD",
    }
)
SENSITIVE_NAME_MARKERS = (
    "API_KEY",
    "ACCESS_KEY",
    "_SECRET",
    "_TOKEN",
    "_PASSWORD",
    "_PRIVATE_KEY",
    "_CREDENTIAL",
)


class RehearsalEnvironmentError(RuntimeError):
    """Raised when isolation cannot be proved without exposing a secret."""


def parse_dotenv_text(text: str, *, label: str) -> dict[str, str]:
    """Parse simple dotenv literals as data; never execute shell syntax."""

    if "\x00" in text:
        raise RehearsalEnvironmentError(f"{label} contains a NUL byte")
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(raw)
        if not match:
            raise RehearsalEnvironmentError(
                f"unsupported {label} syntax at line {number}"
            )
        key, rhs = match.groups()
        if key in values:
            raise RehearsalEnvironmentError(f"duplicate {label} key: {key}")
        if not rhs.strip():
            value = ""
        else:
            lexer = shlex.shlex(rhs, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            try:
                words = list(lexer)
            except ValueError as exc:
                raise RehearsalEnvironmentError(
                    f"invalid {label} quoting at line {number}"
                ) from exc
            if len(words) != 1:
                raise RehearsalEnvironmentError(
                    f"ambiguous {label} value at line {number}"
                )
            value = words[0]
        if any(character in value for character in "\r\n"):
            raise RehearsalEnvironmentError(f"multiline {label} value: {key}")
        values[key] = value
    return values


def _private_file(path: Path, *, label: str) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise RehearsalEnvironmentError(f"missing {label}: {path}") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise RehearsalEnvironmentError(f"{label} must be a regular non-symlink file")
    if details.st_uid != 0 or stat.S_IMODE(details.st_mode) not in {0o400, 0o600}:
        raise RehearsalEnvironmentError(f"{label} must be root-owned mode 0400/0600")


def read_private_dotenv(path: Path, *, label: str) -> dict[str, str]:
    _private_file(path, label=label)
    try:
        return parse_dotenv_text(path.read_text(encoding="utf-8"), label=label)
    except (OSError, UnicodeError) as exc:
        raise RehearsalEnvironmentError(f"cannot read {label}") from exc


def _production_sensitive_values(*sources: Mapping[str, str]) -> set[str]:
    values: set[str] = set()
    for source in sources:
        for key, value in source.items():
            if value and (
                key in SENSITIVE_EXACT_KEYS
                or any(marker in key for marker in SENSITIVE_NAME_MARKERS)
            ):
                values.add(value)
            if value and (key == "DATABASE_URL" or key.endswith("_DATABASE_URL")):
                password = urlsplit(value).password
                if password:
                    values.add(password)
    return values


def _validate_database_url(value: str, *, database: str, role: str) -> None:
    parsed = urlsplit(value)
    if not (
        parsed.scheme == "postgresql+psycopg"
        and parsed.hostname == "postgres"
        and parsed.port == 5432
        and parsed.path.strip("/") == database
        and parsed.username == role
        and bool(parsed.password)
    ):
        raise RehearsalEnvironmentError("rehearsal database URL identity is invalid")


def _new_secret(forbidden: set[str], generated: set[str]) -> str:
    for _ in range(10):
        candidate = secrets.token_urlsafe(48)
        if candidate not in forbidden and candidate not in generated:
            generated.add(candidate)
            return candidate
    raise RehearsalEnvironmentError("could not generate a distinct rehearsal secret")


def build_environments(
    runtime: Mapping[str, str],
    production_database: Mapping[str, str],
    production_api: Mapping[str, str],
    production_worker: Mapping[str, str],
    rehearsal: Mapping[str, str],
    *,
    api_database_url: str,
    worker_database_url: str,
    revision: str,
    run_id: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, object]]:
    """Return isolated role values and a secret-free proof document."""

    unknown = set(rehearsal).difference(REHEARSAL_INPUT_KEYS)
    if unknown:
        raise RehearsalEnvironmentError(
            "unreviewed rehearsal keys: " + ", ".join(sorted(unknown))
        )
    if not REVISION.fullmatch(revision) or not RUN_ID.fullmatch(run_id):
        raise RehearsalEnvironmentError("invalid rehearsal revision or run identity")
    database = f"lecturesift_rehearsal_{run_id}"
    _validate_database_url(
        api_database_url, database=database, role=f"lecturesift_rehearsal_api_{run_id}"
    )
    _validate_database_url(
        worker_database_url,
        database=database,
        role=f"lecturesift_rehearsal_worker_{run_id}",
    )
    if api_database_url == worker_database_url:
        raise RehearsalEnvironmentError("rehearsal database roles must be distinct")

    required = (
        "LECTURESIFT_REHEARSAL_S3_ENDPOINT_URL",
        "LECTURESIFT_REHEARSAL_S3_BUCKET",
        "LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID",
        "LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY",
    )
    missing = [key for key in required if not rehearsal.get(key)]
    if missing:
        raise RehearsalEnvironmentError(
            "missing dedicated rehearsal storage values: " + ", ".join(missing)
        )
    bucket = rehearsal["LECTURESIFT_REHEARSAL_S3_BUCKET"]
    if not BUCKET.fullmatch(bucket):
        raise RehearsalEnvironmentError("invalid dedicated rehearsal bucket")
    production_bucket = runtime.get("S3_BUCKET") or production_api.get("S3_BUCKET", "")
    if not production_bucket or bucket == production_bucket:
        raise RehearsalEnvironmentError("rehearsal bucket must differ from production")
    endpoint = rehearsal["LECTURESIFT_REHEARSAL_S3_ENDPOINT_URL"]
    endpoint_parts = urlsplit(endpoint)
    endpoint_host = (endpoint_parts.hostname or "").lower().rstrip(".")
    if not (
        endpoint_parts.scheme == "https"
        and endpoint_host.endswith(".r2.cloudflarestorage.com")
        and endpoint_parts.port in {None, 443}
        and endpoint_parts.path in {"", "/"}
        and not endpoint_parts.query
        and not endpoint_parts.fragment
        and not endpoint_parts.username
        and not endpoint_parts.password
    ):
        raise RehearsalEnvironmentError("rehearsal storage endpoint must be HTTPS")
    region = rehearsal.get("LECTURESIFT_REHEARSAL_S3_REGION") or (
        runtime.get("S3_REGION") or production_api.get("S3_REGION") or "auto"
    )

    production_sensitive = _production_sensitive_values(
        runtime, production_database, production_api, production_worker
    )
    api_password = urlsplit(api_database_url).password or ""
    worker_password = urlsplit(worker_database_url).password or ""
    supplied_sensitive = {
        api_database_url,
        worker_database_url,
        api_password,
        worker_password,
        rehearsal["LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID"],
        rehearsal["LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY"],
    }
    rehearsal_openai = rehearsal.get("LECTURESIFT_REHEARSAL_OPENAI_API_KEY", "")
    if rehearsal_openai:
        supplied_sensitive.add(rehearsal_openai)
    overlap = supplied_sensitive.intersection(production_sensitive)
    if overlap:
        raise RehearsalEnvironmentError(
            "a sensitive rehearsal value equals a production value"
        )
    if len(supplied_sensitive) != 6 + int(bool(rehearsal_openai)):
        raise RehearsalEnvironmentError("rehearsal credentials must be mutually distinct")

    generated: set[str] = set()
    admin_secret = _new_secret(production_sensitive | supplied_sensitive, generated)
    billing_secret = _new_secret(production_sensitive | supplied_sensitive, generated)
    payment_secret = _new_secret(production_sensitive | supplied_sensitive, generated)
    common = {
        "CELERY_BROKER_URL": "redis://lecturesift-redis-rehearsal:6379/0",
        "REDIS_URL": "redis://lecturesift-redis-rehearsal:6379/0",
        "S3_ENDPOINT_URL": endpoint,
        "S3_REGION": region,
        "S3_BUCKET": bucket,
        "S3_ACCESS_KEY_ID": rehearsal["LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID"],
        "S3_SECRET_ACCESS_KEY": rehearsal[
            "LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY"
        ],
        "LECTURESIFT_WORK_DIR": "/var/lib/lecturesift",
        "LECTURESIFT_REQUIRE_POSTGRES": "true",
        "LECTURESIFT_REQUIRE_DURABLE_PROCESSING": "true",
        "LECTURESIFT_MAINTENANCE_MODE": "off",
        "LECTURESIFT_REHEARSAL": "1",
        "LECTURESIFT_EXPECTED_BUILD_REVISION": revision,
        "NO_PROXY": "postgres,lecturesift-redis-rehearsal,localhost,127.0.0.1",
        "no_proxy": "postgres,lecturesift-redis-rehearsal,localhost,127.0.0.1",
    }
    for key in SAFE_PROCESSING_KEYS:
        value = runtime.get(key) or production_worker.get(key) or production_api.get(key)
        if value:
            common[key] = value
    worker = {
        **common,
        "DATABASE_URL": worker_database_url,
        "LECTURESIFT_WORKER": "1",
        "HTTP_PROXY": "http://egress-proxy-worker:3128",
        "HTTPS_PROXY": "http://egress-proxy-worker:3128",
        "http_proxy": "http://egress-proxy-worker:3128",
        "https_proxy": "http://egress-proxy-worker:3128",
    }
    if rehearsal_openai:
        worker["OPENAI_API_KEY"] = rehearsal_openai
    api = {
        **common,
        "DATABASE_URL": api_database_url,
        "PORT": "8000",
        "ADMIN_ADMIN": admin_secret,
        "BILLING_SESSION_SECRET": billing_secret,
        "PAYMENT_TOKEN_BINDING_SECRET": payment_secret,
        "PUBLIC_BASE_URL": "https://rehearsal.invalid",
        "FRONTEND_BASE_URL": "https://rehearsal.invalid",
        "EMAIL_PROVIDER": "none",
        "PAYTR_TEST_MODE": "true",
        "PAYTR_DEBUG": "false",
        "IYZICO_BANK_TRANSFER_ENABLED": "false",
        "INSTAGRAM_DAILY_AUTOMATION_ENABLED": "false",
        "LECTURESIFT_REHEARSAL_AI_PROVIDER": (
            "dedicated" if rehearsal_openai else "intentionally_absent"
        ),
        # The API has its own R2-only proxy. The worker's separate policy may
        # additionally allow OpenAI when it receives the dedicated AI key.
        "HTTP_PROXY": "http://egress-proxy-api:3128",
        "HTTPS_PROXY": "http://egress-proxy-api:3128",
        "http_proxy": "http://egress-proxy-api:3128",
        "https_proxy": "http://egress-proxy-api:3128",
    }
    for key in (
        "LECTURESIFT_REHEARSAL_FORMAT_CASES",
        "LECTURESIFT_REHEARSAL_FORMAT_TIMEOUT_SECONDS",
    ):
        if rehearsal.get(key):
            api[key] = rehearsal[key]
    forbidden_role_keys = {
        "RESEND_API_KEY",
        "SMTP_PASSWORD",
        "PAYTR_MERCHANT_ID",
        "PAYTR_MERCHANT_KEY",
        "PAYTR_MERCHANT_SALT",
        "IYZICO_API_KEY",
        "IYZICO_SECRET_KEY",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_ACCOUNT_ID",
        "INSTAGRAM_APP_SECRET",
        "INSTAGRAM_ADMIN_TOKEN",
    }
    if forbidden_role_keys.intersection(api) or forbidden_role_keys.intersection(worker):
        raise RehearsalEnvironmentError("a disabled provider key entered a role")
    generated_sensitive = {
        value
        for key, value in {**worker, **api}.items()
        if key in SENSITIVE_EXACT_KEYS
        or any(marker in key for marker in SENSITIVE_NAME_MARKERS)
    }
    if generated_sensitive.intersection(production_sensitive):
        raise RehearsalEnvironmentError(
            "a generated sensitive rehearsal value equals production"
        )
    proof: dict[str, object] = {
        "format": "lecturesift-rehearsal-environment-proof-v1",
        "revision": revision,
        "run_id": run_id,
        "production_api_worker_env_inherited": False,
        "production_database_env_inherited": False,
        "production_sensitive_overlap": False,
        # Distinct literal values are only a candidate isolation signal.  The
        # separate live, read-only negative-capability proof must establish
        # that this identity is denied by the production bucket before an
        # exact rehearsal can be admitted.
        "storage_credentials": "distinct_pending_negative_capability",
        "billing_provider": "disabled",
        "email_provider": "disabled",
        "instagram_provider": "disabled",
        "ai_provider": "dedicated" if rehearsal_openai else "intentionally_absent",
        "api_allowed_egress_hosts": [endpoint_host],
        "worker_allowed_egress_hosts": [
            endpoint_host,
            *(["api.openai.com"] if rehearsal_openai else []),
        ],
        "api_keys": sorted(api),
        "worker_keys": sorted(worker),
    }
    return api, worker, proof


def _render_env(values: Mapping[str, str]) -> str:
    for key, value in values.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise RehearsalEnvironmentError("invalid generated key")
        if any(character in value for character in "\x00\r\n"):
            raise RehearsalEnvironmentError("invalid generated value")
    return "".join(f"{key}={values[key]}\n" for key in sorted(values))


def _render_squid_configuration(hosts: list[str]) -> str:
    if not hosts or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
        for host in hosts
    ):
        raise RehearsalEnvironmentError("invalid rehearsal egress hostname")
    domains = " ".join(f".{host}" for host in sorted(set(hosts)))
    return f"""# Generated fail-closed rehearsal egress policy; contains no secrets.
http_port 3128
visible_hostname lecturesift-rehearsal-egress-proxy
pid_filename /run/squid/squid.pid
coredump_dir /tmp
access_log stdio:/dev/stdout
cache_log /dev/stderr
cache_store_log none
cache deny all
cache_mem 16 MB
cache_dir null /tmp
acl manager proto cache_object
acl localhost src 127.0.0.1/32 ::1/128
acl SSL_ports port 443
acl Safe_ports port 443
acl CONNECT method CONNECT
acl allowed_methods method GET HEAD POST PUT PATCH DELETE OPTIONS CONNECT
acl allowed_rehearsal_domains dstdomain {domains}
acl forbidden_destination dst 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.88.99.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 fc00::/7 fe80::/10 ff00::/8 2001:db8::/32
http_access allow localhost manager
http_access deny manager
http_access deny !Safe_ports
http_access deny CONNECT !SSL_ports
http_access deny !allowed_methods
http_access deny forbidden_destination
http_access allow allowed_rehearsal_domains
http_access deny all
forwarded_for delete
request_header_access X-Forwarded-For deny all
request_header_access Via deny all
request_header_access Cache-Control allow all
request_header_access All allow all
shutdown_lifetime 1 seconds
"""


def _atomic_write(path: Path, payload: bytes) -> None:
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
        details = path.lstat()
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o600:
            raise RehearsalEnvironmentError("generated file metadata is unsafe")
    finally:
        temporary.unlink(missing_ok=True)


def materialize(args: argparse.Namespace) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RehearsalEnvironmentError("rehearsal environment generation requires root")
    output = args.output_dir
    allowed = Path("/var/backups/lecturesift/rehearsal")
    try:
        details = output.lstat()
        resolved = output.resolve(strict=True)
    except OSError as exc:
        raise RehearsalEnvironmentError("missing private rehearsal run directory") from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or stat.S_IMODE(details.st_mode) != 0o700
        or resolved.parent != allowed
        or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", resolved.name)
    ):
        raise RehearsalEnvironmentError("unsafe rehearsal environment output directory")

    runtime = read_private_dotenv(args.runtime, label="production runtime environment")
    production_database = read_private_dotenv(
        args.database, label="production database environment"
    )
    production_api = read_private_dotenv(args.api, label="production API environment")
    production_worker = read_private_dotenv(
        args.worker, label="production worker environment"
    )
    rehearsal = read_private_dotenv(args.rehearsal, label="rehearsal environment")
    release = read_private_dotenv(args.release, label="release environment")
    if set(release) != {"LECTURESIFT_EXPECTED_BUILD_REVISION"}:
        raise RehearsalEnvironmentError("release environment contract is invalid")
    revision = release["LECTURESIFT_EXPECTED_BUILD_REVISION"]
    run_id = resolved.name.replace("T", "").removesuffix("Z")
    api, worker, proof = build_environments(
        runtime,
        production_database,
        production_api,
        production_worker,
        rehearsal,
        api_database_url=os.environ.get("LECTURESIFT_REHEARSAL_API_DATABASE_URL", ""),
        worker_database_url=os.environ.get("LECTURESIFT_REHEARSAL_WORKER_DATABASE_URL", ""),
        revision=revision,
        run_id=run_id,
    )
    destinations = {
        "api": output / "rehearsal-api.env",
        "worker": output / "rehearsal-worker.env",
        "proof": output / "rehearsal-environment-proof.json",
        "api_proxy": output / "rehearsal-api-squid.conf",
        "worker_proxy": output / "rehearsal-worker-squid.conf",
    }
    if any(path.exists() or path.is_symlink() for path in destinations.values()):
        raise RehearsalEnvironmentError("generated rehearsal environment already exists")
    _atomic_write(destinations["api"], _render_env(api).encode("utf-8"))
    try:
        _atomic_write(destinations["worker"], _render_env(worker).encode("utf-8"))
        _atomic_write(
            destinations["proof"],
            (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        for role in ("api", "worker"):
            _atomic_write(
                destinations[f"{role}_proxy"],
                _render_squid_configuration(
                    list(proof[f"{role}_allowed_egress_hosts"])
                ).encode(),
            )
            # Each proxy runs as an unprivileged container user. These
            # generated files contain hostnames only (no credentials), so
            # root-owned 0644 bind mounts are intentional; dotenv files stay
            # mode 0600.
            os.chmod(destinations[f"{role}_proxy"], 0o644)
        directory_fd = os.open(output, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        for path in destinations.values():
            path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--api", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--rehearsal", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        materialize(args)
    except RehearsalEnvironmentError as exc:
        parser.exit(1, f"Rehearsal environment generation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
