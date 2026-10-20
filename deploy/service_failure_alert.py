#!/usr/bin/env python3
"""Email the operators when a watched LectureSift systemd unit fails.

systemd starts ``lecturesift-failure-alert@<unit>.service`` from the watched
unit's ``OnFailure=``; the ingress selector also queues it after a failed
reconciliation, because its loop retries instead of exiting. One email names
the unit, host and time and carries the unit's last journal lines with
configuration values, tokens, cookies and email addresses redacted.

At most one email per unit is sent per hour, so a timer that fails every
15 minutes cannot flood the mailbox. Alerting must never affect the watched
unit: every delivery problem is logged to the journal and the script still
exits 0. The scheduled backup keeps its own lecturesift-backup-alert@ unit.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import smtplib
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import unquote


WATCHED_UNITS = {
    "lecturesift.service": (
        "the private production core (PostgreSQL, Redis, egress proxy, API and worker)"
    ),
    "lecturesift-ingress-selector.service": (
        "the public ingress selector; a reconciliation attempt failed or systemd gave up on it"
    ),
    "lecturesift-instagram.service": "the scheduled Instagram studio and replies job",
}
RATE_LIMIT_SECONDS = 3600
JOURNAL_LINES = 30
MAX_RECIPIENTS = 10
DEFAULT_STATE_DIR = Path("/var/lib/lecturesift/failure-alerts")
DEFAULT_ENV_FILES = (
    ("LECTURESIFT_ENV_FILE", "/etc/lecturesift/runtime.env"),
    ("LECTURESIFT_DB_ENV_FILE", "/etc/lecturesift/postgres.env"),
    ("LECTURESIFT_RESTIC_ENV_FILE", "/etc/lecturesift/restic.env"),
)

ASSIGNMENT = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
EMAIL_ADDRESS = re.compile(r"^[^\s@,]+@[^\s@,]+\.[^\s@,]+$")
# Every value of a key with one of these name parts is a secret, whatever it
# looks like. Other values are redacted by shape (see _is_secret_value).
SECRET_KEY_PARTS = (
    "PASSWORD", "SECRET", "TOKEN", "KEY", "SALT", "CREDENTIAL", "PRIVATE",
    "COOKIE", "SESSION", "SERVICE_ACCOUNT", "DATABASE_URL", "REDIS_URL",
    "BROKER_URL", "REPOSITORY", "ADMIN_ADMIN",
)
PLAIN_VALUE = re.compile(r"^[a-z._-]+$")
URL_PASSWORD = re.compile(r"://[^/\s:@]*:([^/\s@]+)@")
REDACTED = "[redacted]"

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")
SECRET_HEADER = re.compile(
    r"(?i)\b(authorization|proxy-authorization|set-cookie|cookie|x-api-key)\b(\s*[:=]\s*).*"
)
BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
SECRET_FIELD = re.compile(
    r"(?i)([\w.-]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|signature|session|cookie|auth)[\w.-]*[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)"
)
EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# The only address-shaped text kept: an instance of one of the two alert
# templates for a LectureSift unit, e.g.
# lecturesift-failure-alert@lecturesift-instagram.service.service.
ALERT_INSTANCE = re.compile(
    r"lecturesift-(?:failure|backup)-alert@lecturesift(?:-[a-z]+)*\.service(?:\.service)?"
)
HEX_RUN = re.compile(r"\b[0-9A-Fa-f]{32,}\b")
OPAQUE_RUN = re.compile(r"[A-Za-z0-9_+/=.-]{24,}")
MAX_SCANNED_LINE = 2000
MAX_SHOWN_LINE = 400


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _private_file(path: Path) -> bool:
    """A root-only regular file; ownership is checked only where POSIX ids exist."""

    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        return False
    if hasattr(os, "geteuid"):
        return details.st_uid == os.geteuid() and not stat.S_IMODE(details.st_mode) & 0o077
    return True


def decode_value(rhs: str) -> str | None:
    """Decode one right-hand side the way the sourcing shell does, or None.

    Same rule as generate_rehearsal_envs.parse_dotenv_text: the value must be
    exactly one POSIX shell word. Expansions outside single quotes are refused
    because the shell would have replaced them with something else.
    """

    if not rhs.strip():
        return ""
    if rhs[0] in " \t":
        return None
    single_quoted = len(rhs) >= 2 and rhs[0] == rhs[-1] == "'" and rhs.count("'") == 2
    if ("$" in rhs or "`" in rhs) and not single_quoted:
        return None
    lexer = shlex.shlex(rhs, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        words = list(lexer)
    except ValueError:
        return None
    return words[0] if len(words) == 1 else None


def parse_env_file(path: Path) -> tuple[dict[str, str], int]:
    """Decode the assignments; also count lines that cannot be decoded for certain."""

    values: dict[str, str] = {}
    undecoded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line)
        value = decode_value(match.group(2)) if match else None
        if value is None:
            undecoded += 1
            continue
        values[match.group(1)] = value
    return values, undecoded


def load_env_files(paths: Sequence[Path]) -> tuple[dict[str, str], list[str], bool]:
    """Return the first file's settings, every file's secret values and completeness.

    The first file is the runtime configuration and must be readable. A later
    file may be absent; one that exists but is unsafe or unreadable makes the
    redaction set incomplete, so the caller omits the journal lines. So does a
    line whose value cannot be decoded for certain; that key is left out.
    """

    settings: dict[str, str] = {}
    secrets: set[str] = set()
    complete = True
    for index, path in enumerate(paths):
        try:
            if index > 0 and not os.path.lexists(path):
                continue
            if not _private_file(path):
                raise PermissionError("not a root-only regular file")
            values, undecoded = parse_env_file(path)
        except (OSError, UnicodeError):
            if index == 0:
                raise
            complete = False
            continue
        if undecoded:
            complete = False
        if index == 0:
            settings = values
        for key, value in values.items():
            if _is_secret_value(key, value):
                secrets.add(value)
            # A connection URL's password can also be logged on its own.
            for password in URL_PASSWORD.findall(value):
                secrets.update(item for item in (password, unquote(password)) if len(item) >= 4)
    return settings, sorted(secrets, key=len, reverse=True), complete


def _is_secret_value(key: str, value: str) -> bool:
    if len(value) < 4:
        return False
    if any(part in key.upper() for part in SECRET_KEY_PARTS):
        return True
    # Plain lowercase names (database, bucket, user names), byte limits and
    # absolute paths stay readable; anything else long enough to be a
    # credential is hidden.
    return (
        len(value) >= 8
        and not PLAIN_VALUE.fullmatch(value)
        and not value.isdigit()
        and not value.startswith("/")
    )


def _keep_alert_instances(match: re.Match[str]) -> str:
    text = match.group(0)
    return text if ALERT_INSTANCE.fullmatch(text) else "[email]"


def _hide_opaque(match: re.Match[str]) -> str:
    """Hide token-like runs but keep paths, unit and module names readable."""

    text = match.group(0)
    has_digit = any(character.isdigit() for character in text)
    mixed_case = any(character.isupper() for character in text) and any(
        character.islower() for character in text
    )
    long_key = len(text) >= 32 and "/" not in text and "." not in text and has_digit
    return REDACTED if (mixed_case and has_digit) or long_key else text


def redact(line: str, secrets: Iterable[str]) -> str:
    """Hide configuration values, credentials, cookies and email addresses."""

    text = CONTROL.sub(" ", ANSI_ESCAPE.sub("", line))
    if len(text) > MAX_SCANNED_LINE:
        # Never keep a token cut in half: drop the partial last word.
        text = text[:MAX_SCANNED_LINE].rsplit(" ", 1)[0] + " [truncated]"
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    text = URL_USERINFO.sub(lambda match: match.group(1) + REDACTED + "@", text)
    text = SECRET_HEADER.sub(lambda match: match.group(1) + match.group(2) + REDACTED, text)
    text = BEARER.sub(lambda match: match.group(1) + " " + REDACTED, text)
    text = SECRET_FIELD.sub(lambda match: match.group(1) + REDACTED, text)
    text = EMAIL_IN_TEXT.sub(_keep_alert_instances, text)
    text = HEX_RUN.sub(REDACTED, text)
    text = OPAQUE_RUN.sub(_hide_opaque, text)
    if len(text) > MAX_SHOWN_LINE:
        text = text[:MAX_SHOWN_LINE] + " [truncated]"
    return text


def recipients(settings: Mapping[str, str]) -> list[str]:
    """The operations mailbox first, then each admin address, without repeats."""

    found: list[str] = []
    seen: set[str] = set()
    for key in ("LECTURESIFT_OPS_ALERT_EMAIL", "LECTURESIFT_ADMIN_EMAILS"):
        for address in settings.get(key, "").split(","):
            address = address.strip()
            if EMAIL_ADDRESS.fullmatch(address) and address.casefold() not in seen:
                seen.add(address.casefold())
                found.append(address)
    return found[:MAX_RECIPIENTS]


def read_journal(unit: str) -> list[str]:
    result = subprocess.run(
        [
            "journalctl", "--unit", unit, "--lines", str(JOURNAL_LINES),
            "--no-pager", "--quiet", "--utc", "--output", "short-iso",
        ],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    if result.returncode != 0:
        raise OSError("journalctl failed")
    return result.stdout.splitlines()[-JOURNAL_LINES:]


def send_email(settings: Mapping[str, str], to: list[str], subject: str, text: str) -> None:
    """Send one plain-text email through the configured Resend or SMTP account."""

    provider = settings.get("EMAIL_PROVIDER", "").strip().lower()
    sender = settings.get("EMAIL_FROM", "").strip()
    if not sender:
        raise RuntimeError("EMAIL_FROM is not configured")
    if provider == "resend":
        api_key = settings.get("RESEND_API_KEY", "")
        if not api_key:
            raise RuntimeError("RESEND_API_KEY is not configured")
        request = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps({"from": sender, "to": to, "subject": subject, "text": text}).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "LectureSift/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Resend answered HTTP {response.status}")
        return
    if provider == "smtp":
        host = settings.get("SMTP_HOST", "")
        username = settings.get("SMTP_USERNAME", "")
        password = settings.get("SMTP_PASSWORD", "")
        if not host or not username or not password:
            raise RuntimeError("SMTP is not configured")
        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(to)
        message["Subject"] = subject
        message.set_content(text)
        use_tls = settings.get("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}
        with smtplib.SMTP(host, int(settings.get("SMTP_PORT") or 587), timeout=20) as client:
            if use_tls:
                client.starttls(context=ssl.create_default_context())
            client.login(username, password)
            client.send_message(message)
        return
    raise RuntimeError("no supported EMAIL_PROVIDER is configured")


def _state_path(state_dir: Path, unit: str) -> Path:
    return state_dir / f"{unit}.last-sent"


def recently_sent(state_dir: Path, unit: str, now: float) -> bool:
    try:
        last = int(_state_path(state_dir, unit).read_text(encoding="ascii").strip())
    except (OSError, ValueError, UnicodeError):
        return False
    # A clock that moved backwards must not silence alerts for a long time.
    return 0 <= now - last < RATE_LIMIT_SECONDS


def record_sent(state_dir: Path, unit: str, now: float) -> None:
    if state_dir.is_symlink():
        raise OSError("the alert state directory is a symlink")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=state_dir, prefix=".last-sent-")
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(f"{int(now)}\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, _state_path(state_dir, unit))
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def compose_message(
    unit: str, host: str, now: float, journal: list[str] | None
) -> tuple[str, str]:
    moment = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = f"[LectureSift] {unit} failed on {host}"
    lines = [
        f"{unit} failed on {host} at {moment}.",
        "",
        f"This is {WATCHED_UNITS[unit]}.",
        "Further failures of this unit are not emailed for one hour.",
        "",
        "Inspect it on the host:",
        f"  systemctl status {unit}",
        f"  journalctl -u {unit} -n 200",
        "",
    ]
    if journal is None:
        lines.append("The journal lines were left out because they could not be read safely.")
    else:
        lines.append(
            f"Last {len(journal)} journal lines (configuration values, tokens, cookies "
            "and email addresses redacted):"
        )
        lines.append("")
        lines.extend(journal or ["(no journal lines)"])
    return subject, "\n".join(lines) + "\n"


def run(
    unit: str,
    *,
    env_files: Sequence[Path],
    state_dir: Path,
    now: float | None = None,
    host: str | None = None,
    journal_reader: Callable[[str], list[str]] | None = None,
    sender: Callable[[Mapping[str, str], list[str], str, str], None] | None = None,
) -> int:
    """Send at most one alert for this unit per hour. Returns the exit status."""

    journal_reader = journal_reader or read_journal
    sender = sender or send_email
    if f"{unit}.service" in WATCHED_UNITS:
        # A hand-started lecturesift-failure-alert@lecturesift-instagram.service
        # names the unit without its suffix.
        unit = f"{unit}.service"
    if unit not in WATCHED_UNITS:
        log(
            "Refusing a failure alert for an unwatched unit; expected one of: "
            f"{', '.join(WATCHED_UNITS)}."
        )
        return 2
    now = time.time() if now is None else now
    if recently_sent(state_dir, unit, now):
        log(f"Failure alert for {unit} suppressed: one was already sent within the last hour.")
        return 0
    try:
        settings, secrets, complete = load_env_files(env_files)
    except (OSError, UnicodeError):
        log(f"Failure alert for {unit} not sent: the runtime environment is missing or unsafe.")
        return 0
    to = recipients(settings)
    if not to:
        log(
            f"Failure alert for {unit} not sent: set LECTURESIFT_OPS_ALERT_EMAIL or "
            "LECTURESIFT_ADMIN_EMAILS to a valid address."
        )
        return 0
    journal: list[str] | None = None
    if complete:
        try:
            journal = [redact(line, secrets) for line in journal_reader(unit)]
        except (OSError, subprocess.SubprocessError):
            log(f"The journal of {unit} could not be read; the alert is sent without it.")
    else:
        log(
            "An environment file is unsafe or has a value that cannot be decoded for "
            f"certain; the alert for {unit} is sent without journal lines."
        )
    subject, text = compose_message(unit, host or socket.gethostname(), now, journal)
    try:
        sender(settings, to, subject, text)
    except Exception as exc:  # noqa: BLE001 - never fail the alert unit over delivery
        status = getattr(exc, "code", None) or getattr(exc, "status", None)
        detail = f" (HTTP {status})" if isinstance(status, int) else ""
        log(f"Failure alert for {unit} not sent: {type(exc).__name__}{detail}.")
        return 0
    try:
        record_sent(state_dir, unit, now)
    except OSError:
        log(f"Failure alert for {unit} sent, but the one-hour rate-limit state was not saved.")
        return 0
    log(f"Failure alert for {unit} sent to {len(to)} recipient(s).")
    return 0


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1:
        log("Usage: service_failure_alert.py <failed-unit-name>")
        return 2
    unit = argv[0]
    env_files = [Path(os.environ.get(name) or default) for name, default in DEFAULT_ENV_FILES]
    try:
        return run(unit, env_files=env_files, state_dir=DEFAULT_STATE_DIR)
    except Exception as exc:  # noqa: BLE001 - the watched unit must never depend on this
        log(f"Failure alert for {unit} not sent: unexpected {type(exc).__name__}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
