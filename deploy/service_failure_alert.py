#!/usr/bin/env python3
"""Email the operators when a watched LectureSift systemd unit fails.

systemd starts ``lecturesift-failure-alert@<unit>.service`` from the watched
unit's ``OnFailure=``. The ingress selector and the core's reload steps also
queue it, because a failed reconciliation or reload leaves their unit active.

The email is short: which unit failed on which host, when (UTC and Turkey
time), the unit's state and result as systemd reports them, and the command
that shows its log on the server. It carries no log lines. A log excerpt can
hold credentials or customer data, and whoever investigates reads the journal
on the server anyway.

At most one alert per unit is sent per hour, so a timer that fails every
15 minutes cannot flood the mailbox. Alerting must never affect the watched
unit: every problem is logged to the alert unit's journal and the script still
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
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Mapping, Sequence


WATCHED_UNITS = {
    "lecturesift.service": (
        "the private production core (PostgreSQL, Redis, egress proxy, API and worker)"
    ),
    "lecturesift-ingress-selector.service": "the public ingress selector",
    "lecturesift-instagram.service": "the scheduled Instagram studio and replies job",
}
RATE_LIMIT_SECONDS = 3600
# Every recipient gets the alert; each message carries at most this many, which
# both the Resend API and ordinary SMTP accounts accept.
RECIPIENTS_PER_MESSAGE = 10
# The template stops this script after TimeoutStartSec=90. The systemd state
# query and every delivery attempt are bounded so that all of it, including
# saving the hourly record, ends well before that.
STATE_QUERY_SECONDS = 10
DELIVERY_SECONDS = 60
REQUEST_SECONDS = 20
MIN_REQUEST_SECONDS = 5
# Turkey has kept UTC+3 all year since 2016.
TURKEY_TIME = timezone(timedelta(hours=3))
DEFAULT_STATE_DIR = Path("/var/lib/lecturesift/failure-alerts")
ENV_FILE_VARIABLE = "LECTURESIFT_ENV_FILE"
DEFAULT_ENV_FILE = Path("/etc/lecturesift/runtime.env")

ASSIGNMENT = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
EMAIL_ADDRESS = re.compile(r"^[^\s@,<>\"]+@[^\s@,<>\"]+\.[^\s@,<>\"]+$")
HOST_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
STATE_PROPERTIES = ("ActiveState", "SubState", "Result", "ExecMainCode", "ExecMainStatus")
# systemd reports these as fixed words and numbers; anything else is dropped.
STATE_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# ExecMainCode is the waitid() si_code of the main process.
MAIN_PROCESS_ENDINGS = {
    "1": "exited with status {}",
    "2": "was killed by signal {}",
    "3": "dumped core after signal {}",
}
STILL_ACTIVE = {"active", "reloading", "activating"}
# These two queue the alert themselves while their unit stays active.
STILL_ACTIVE_NOTES = {
    "lecturesift.service": (
        "The core is still active, so this was a failed reload "
        "(systemctl reload lecturesift)."
    ),
    "lecturesift-ingress-selector.service": (
        "The selector is still active and keeps retrying, so one reconciliation "
        "attempt failed."
    ),
}


class DeliveryTimeout(Exception):
    """A provider request did not finish within its share of the delivery time."""


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


def load_settings(path: Path) -> tuple[dict[str, str], int]:
    """Decode the runtime file's assignments; also count the undecodable ones.

    The file must be root-only. A key whose last assignment cannot be decoded
    for certain is left out, never served from an earlier assignment, because
    the shell would not use that earlier value either.
    """

    if not _private_file(path):
        raise PermissionError("not a root-only regular file")
    values: dict[str, str] = {}
    undecoded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line)
        if match is None:
            undecoded += 1
            continue
        value = decode_value(match.group(2))
        if value is None:
            undecoded += 1
            values.pop(match.group(1), None)
            continue
        values[match.group(1)] = value
    return values, undecoded


def recipients(settings: Mapping[str, str]) -> list[str]:
    """The operations mailbox first, then every admin address, without repeats."""

    found: list[str] = []
    seen: set[str] = set()
    for key in ("LECTURESIFT_OPS_ALERT_EMAIL", "LECTURESIFT_ADMIN_EMAILS"):
        for address in settings.get(key, "").split(","):
            address = address.strip()
            if EMAIL_ADDRESS.fullmatch(address) and address.casefold() not in seen:
                seen.add(address.casefold())
                found.append(address)
    return found


def read_unit_state(unit: str) -> dict[str, str]:
    """The unit's state words and main-process ending, as systemd reports them."""

    result = subprocess.run(
        ["systemctl", "show", "--property=" + ",".join(STATE_PROPERTIES), "--", unit],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=STATE_QUERY_SECONDS,
    )
    if result.returncode != 0:
        raise OSError("systemctl show failed")
    state: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in STATE_PROPERTIES and STATE_VALUE.fullmatch(value):
            state[key] = value
    return state


def describe_state(unit: str, state: Mapping[str, str]) -> list[str]:
    active = state.get("ActiveState")
    sub = state.get("SubState")
    lines = [
        f"State now: {active} ({sub})" if active and sub else f"State now: {active or 'unknown'}",
        f"systemd result: {state.get('Result') or 'unknown'}",
    ]
    code = state.get("ExecMainCode", "")
    status = state.get("ExecMainStatus", "")
    ending = MAIN_PROCESS_ENDINGS.get(code)
    # A clean exit says nothing about this failure (a start step or a reload
    # may have failed instead), so only a failing main process is shown.
    if ending and status.isdigit() and (code != "1" or status != "0"):
        lines.append(f"Main process: {ending.format(status)}")
    if active in STILL_ACTIVE and unit in STILL_ACTIVE_NOTES:
        lines.append(STILL_ACTIVE_NOTES[unit])
    return lines


def compose_message(
    unit: str, host: str, now: float, state: Mapping[str, str]
) -> tuple[str, str]:
    moment = datetime.fromtimestamp(now, timezone.utc)
    local = moment.astimezone(TURKEY_TIME)
    subject = f"[LectureSift] {unit} failed on {host}"
    lines = [
        f"{unit} failed on {host}.",
        f"This is {WATCHED_UNITS[unit]}.",
        "",
        f"Time: {moment:%Y-%m-%d %H:%M:%S} UTC ({local:%Y-%m-%d %H:%M:%S} Turkey time)",
        *describe_state(unit, state),
        "",
        "To see its log, run this on the server:",
        f"  journalctl -u {unit} -n 100",
        "",
        "Further failures of this unit are not emailed for one hour.",
    ]
    return subject, "\n".join(lines) + "\n"


def send_email(
    settings: Mapping[str, str], to: list[str], subject: str, text: str, timeout: float
) -> None:
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
        # Same switch as lecturesift.config: only the literal "true" enables STARTTLS.
        use_tls = settings.get("SMTP_USE_TLS", "true").strip().lower() == "true"
        with smtplib.SMTP(host, int(settings.get("SMTP_PORT") or 587), timeout=timeout) as client:
            if use_tls:
                client.starttls(context=ssl.create_default_context())
            client.login(username, password)
            client.send_message(message)
        return
    raise RuntimeError("no supported EMAIL_PROVIDER is configured")


def _send_within(
    sender: Callable[..., None],
    settings: Mapping[str, str],
    batch: list[str],
    subject: str,
    text: str,
    limit: float,
) -> None:
    """Run one provider request, giving up on it after ``limit`` seconds.

    The request runs in a daemon thread, so a slow DNS lookup or a stalled SMTP
    exchange cannot hold the script past its deadline; the thread ends with the
    process.
    """

    outcome: list[BaseException | None] = []

    def deliver() -> None:
        try:
            sender(settings, batch, subject, text, limit)
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting thread
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=deliver, name="failure-alert-delivery", daemon=True)
    worker.start()
    worker.join(limit)
    if not outcome:
        raise DeliveryTimeout()
    if outcome[0] is not None:
        raise outcome[0]


def _failure_name(exc: BaseException) -> str:
    status = getattr(exc, "code", None) or getattr(exc, "status", None)
    detail = f" (HTTP {status})" if isinstance(status, int) else ""
    return f"{type(exc).__name__}{detail}"


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


def run(
    unit: str,
    *,
    env_file: Path,
    state_dir: Path,
    now: float | None = None,
    host: str | None = None,
    state_reader: Callable[[str], Mapping[str, str]] | None = None,
    sender: Callable[..., None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Send at most one alert for this unit per hour. Returns the exit status."""

    state_reader = state_reader or read_unit_state
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
        settings, undecoded = load_settings(env_file)
    except (OSError, UnicodeError):
        log(f"Failure alert for {unit} not sent: the runtime environment is missing or unsafe.")
        return 0
    if undecoded:
        log(f"{undecoded} line(s) of the runtime environment could not be decoded and were ignored.")
    to = recipients(settings)
    if not to:
        log(
            f"Failure alert for {unit} not sent: set LECTURESIFT_OPS_ALERT_EMAIL or "
            "LECTURESIFT_ADMIN_EMAILS to a valid address."
        )
        return 0
    try:
        state = dict(state_reader(unit))
    except (OSError, subprocess.SubprocessError):
        log(f"The systemd state of {unit} could not be read; the alert is sent without it.")
        state = {}
    host = host or socket.gethostname()
    if not HOST_NAME.fullmatch(host):
        host = "the production host"
    subject, text = compose_message(unit, host, now, state)

    deadline = clock() + DELIVERY_SECONDS
    delivered = 0
    failures: list[str] = []
    stopped = False
    for start in range(0, len(to), RECIPIENTS_PER_MESSAGE):
        remaining = deadline - clock()
        if remaining < MIN_REQUEST_SECONDS:
            stopped = True
            break
        batch = to[start : start + RECIPIENTS_PER_MESSAGE]
        try:
            _send_within(sender, settings, batch, subject, text, min(REQUEST_SECONDS, remaining))
        except Exception as exc:  # noqa: BLE001 - never fail the alert unit over delivery
            failure = _failure_name(exc)
            if failure not in failures:
                failures.append(failure)
            continue
        if not delivered:
            # Count the alert as soon as anyone has it, so being stopped later
            # cannot make the next failure send it to the same people again.
            try:
                record_sent(state_dir, unit, now)
            except OSError:
                log(f"Failure alert for {unit} sent, but the one-hour rate-limit state was not saved.")
        delivered += len(batch)

    reasons = list(failures)
    if stopped:
        reasons.append(f"the {DELIVERY_SECONDS}-second delivery limit was reached")
    if not delivered:
        log(f"Failure alert for {unit} not sent: {', '.join(reasons)}.")
        return 0
    if delivered < len(to):
        log(
            f"Failure alert for {unit} reached {delivered} of {len(to)} recipient(s); "
            f"the rest were not reached: {', '.join(reasons)}."
        )
    log(f"Failure alert for {unit} sent to {delivered} recipient(s).")
    return 0


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1:
        log("Usage: service_failure_alert.py <failed-unit-name>")
        return 2
    unit = argv[0]
    env_file = Path(os.environ.get(ENV_FILE_VARIABLE) or DEFAULT_ENV_FILE)
    try:
        return run(unit, env_file=env_file, state_dir=DEFAULT_STATE_DIR)
    except Exception as exc:  # noqa: BLE001 - the watched unit must never depend on this
        log(f"Failure alert for {unit} not sent: unexpected {type(exc).__name__}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
