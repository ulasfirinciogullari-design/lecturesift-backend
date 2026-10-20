from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import smtplib
import stat
import subprocess
import sys
import threading
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "deploy" / "service_failure_alert.py"
SPEC = importlib.util.spec_from_file_location("service_failure_alert", SCRIPT_PATH)
assert SPEC and SPEC.loader
alert = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = alert
SPEC.loader.exec_module(alert)

WATCHED = (
    "lecturesift.service",
    "lecturesift-ingress-selector.service",
    "lecturesift-instagram.service",
)
NOW = datetime(2026, 10, 20, 2, 17, 5, tzinfo=timezone.utc).timestamp()
# Synthetic values only; none has the shape of a real provider credential.
RESEND_KEY = "synthetic-resend-Key-0123456789"
DB_PASSWORD = "Synthetic-Db-Passw0rd-42"
ADMIN_KEY = "SyntheticEmergencyAdminKey0123456789abcd"
ACCESS_TOKEN = "SyntheticGraphAccessToken0123456789XYZ"
FAILED_STATE = {
    "ActiveState": "failed",
    "SubState": "failed",
    "Result": "exit-code",
    "ExecMainCode": "1",
    "ExecMainStatus": "1",
}
STATE_COMMAND = [
    "systemctl",
    "show",
    "--property=ActiveState,SubState,Result,ExecMainCode,ExecMainStatus",
    "--",
]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _unit_section(text: str) -> str:
    return text.split("[Service]", 1)[0]


def _private(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    return _private(
        tmp_path / "runtime.env",
        "\n".join(
            (
                "# Synthetic production-shaped runtime file",
                "EMAIL_PROVIDER=resend",
                'EMAIL_FROM="LectureSift <noreply@example.com>"',
                f"RESEND_API_KEY={RESEND_KEY}",
                "LECTURESIFT_OPS_ALERT_EMAIL=ops@example.com",
                'LECTURESIFT_ADMIN_EMAILS="owner@example.org, OPS@example.com,not-an-address"',
                f"ADMIN_ADMIN={ADMIN_KEY}",
                "DATABASE_URL=postgresql+psycopg://lecturesift_app:"
                f"{DB_PASSWORD}@postgres:5432/lecturesift",
                f"INSTAGRAM_ACCESS_TOKEN='{ACCESS_TOKEN}'",
            )
        )
        + "\n",
    )


class FakeSender:
    """Records one call per message; each message goes to exactly one address."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[dict, str, str, str]] = []
        self.timeouts: list[float] = []
        self.error = error

    def __call__(self, settings, address, subject, text, timeout):
        assert isinstance(address, str)
        self.calls.append((dict(settings), address, subject, text))
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error

    def addresses(self) -> list[str]:
        return [call[1] for call in self.calls]

    def alerts(self) -> int:
        # The operations mailbox is first in every alert.
        return self.addresses().count("ops@example.com")


def _no_pause(seconds: float) -> None:
    return None


def _run(env_file, state_dir, *, unit="lecturesift-instagram.service", now=NOW, sender=None,
         state=None, clock=None, pause=None):
    return alert.run(
        unit,
        env_file=env_file,
        state_dir=state_dir,
        now=now,
        host="ovh-prod",
        state_reader=state or (lambda name: dict(FAILED_STATE)),
        sender=sender,
        clock=clock or time.monotonic,
        pause=pause or _no_pause,
    )


def test_watched_units_queue_the_failure_alert_template_without_new_dependencies():
    carrying = set()
    for path in sorted((ROOT / "deploy").glob("*.service")):
        text = path.read_text(encoding="utf-8")
        if "OnFailure=lecturesift-failure-alert@%n.service" in text:
            carrying.add(path.name)
            # Only the failure edge names the alert; no Requires/Wants/After
            # ordering or network dependency is added to the watched unit.
            assert text.count("lecturesift-failure-alert") == 1
            assert "OnFailure=lecturesift-failure-alert@%n.service\n" in _unit_section(text)
    assert carrying == set(WATCHED) == set(alert.WATCHED_UNITS)

    backup = _read("deploy/lecturesift-backup.service")
    assert "OnFailure=lecturesift-backup-alert@%n.service" in backup
    assert "lecturesift-failure-alert" not in backup


def test_failure_alert_template_is_a_hardened_oneshot_that_reads_no_env_into_its_process():
    template = _read("deploy/lecturesift-failure-alert@.service")

    assert "Type=oneshot" in template
    assert (
        "ExecStart=/usr/bin/python3 -I /opt/lecturesift/deploy/service_failure_alert.py %i"
        in template
    )
    assert "Environment=LECTURESIFT_ENV_FILE=/etc/lecturesift/runtime.env" in template
    # Only the runtime file's email settings are needed; the database and
    # Restic files are never opened.
    assert "postgres.env" not in template and "restic.env" not in template
    # Secrets stay in the root-only file; systemd never exports them.
    assert "EnvironmentFile=" not in template
    assert "StateDirectory=lecturesift/failure-alerts" in template
    assert "StateDirectoryMode=0700" in template
    assert "ReadWritePaths=/var/lib/lecturesift/failure-alerts" in template
    for hardening in (
        "UMask=0077",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ProtectHome=yes",
        "ProtectSystem=strict",
        "RestrictSUIDSGID=yes",
        "MemoryDenyWriteExecute=yes",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert hardening in template
    timeout = int(re.search(r"^TimeoutStartSec=(\d+)$", template, re.MULTILINE).group(1))
    # The state query, every delivery attempt and the hourly record fit inside it.
    assert alert.STATE_QUERY_SECONDS + alert.DELIVERY_SECONDS + 10 <= timeout <= 120
    assert alert.REQUEST_SECONDS < alert.DELIVERY_SECONDS
    assert "[Install]" not in template
    assert "Restart=" not in template
    assert "OnFailure=" not in template


def test_installer_and_promotion_manage_the_same_unit_set_with_the_alert_template():
    def unit_list(path: str, name: str) -> list[str]:
        body = _read(path).split(f"{name}=(\n", 1)[1].split("\n)", 1)[0]
        return [line.strip() for line in body.splitlines()]

    installed = unit_list("deploy/install_systemd_units.sh", "units")
    promoted = unit_list("deploy/promote_rehearsed_release.sh", "managed_units")
    assert installed == promoted
    assert "lecturesift-failure-alert@.service" in installed
    assert "lecturesift-backup-alert@.service" in installed
    for unit in installed:
        assert (ROOT / "deploy" / unit).is_file()


def test_selector_queues_the_alert_after_a_failed_reconciliation_without_waiting():
    loop = _read("deploy/reconcile_production_ingress.sh")
    selector = _read("deploy/lecturesift-ingress-selector.service")

    expected = "lecturesift-failure-alert@%n.service".replace(
        "%n", "lecturesift-ingress-selector.service"
    )
    assert f"FAILURE_ALERT_UNIT={expected}\n" in loop
    failed_branch = loop.split('if ! bash "$HANDOFF" reconcile; then', 1)[1].split("\n  fi\n", 1)[0]
    assert (
        'systemctl start --no-block "$FAILURE_ALERT_UNIT" >/dev/null 2>&1 || true'
        in failed_branch
    )
    assert loop.count("systemctl start") == 1
    assert "Restart=always" in selector


RELOAD_WRAPPER = "ExecReload=/bin/bash /opt/lecturesift/deploy/core_reload_step.sh "


def test_every_core_reload_step_queues_the_alert_because_a_failed_reload_never_fails_the_unit():
    core = _read("deploy/lecturesift.service")
    wrapper = _read("deploy/core_reload_step.sh")

    reload_lines = [line for line in core.splitlines() if line.startswith("ExecReload=")]
    assert len(reload_lines) == 7
    assert all(line.startswith(RELOAD_WRAPPER) for line in reload_lines)
    # Start and stop failures put the unit in the failed state, where
    # OnFailure= already sends the alert; only reload needs the wrapper.
    other = [line for line in core.splitlines() if line.startswith(("ExecStart", "ExecStop"))]
    assert other and not any("core_reload_step.sh" in line for line in other)

    on_failure = next(
        line.removeprefix("OnFailure=") for line in core.splitlines() if line.startswith("OnFailure=")
    )
    assert f"FAILURE_ALERT_UNIT={on_failure.replace('%n', 'lecturesift.service')}\n" in wrapper
    assert 'systemctl start --no-block "$FAILURE_ALERT_UNIT" >/dev/null 2>&1 || true' in wrapper
    assert 'exit "$status"' in wrapper
    assert 'setpriv --pdeathsig KILL -- "$@" || status=$?' in wrapper


def _run_reload_wrapper(tmp_path: Path, *command: str, systemctl_status: int = 0):
    bash = shutil.which("bash")
    if os.name != "posix" or bash is None:
        pytest.skip("the reload wrapper runs on the Linux host only")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "systemctl.calls"
    fake = fake_bin / "systemctl"
    fake.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\nexit {systemctl_status}\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    environment = dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    result = subprocess.run(
        [bash, str(ROOT / "deploy" / "core_reload_step.sh"), *command],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    return result, calls.read_text(encoding="utf-8") if calls.exists() else ""


def test_a_failed_reload_step_queues_the_alert_and_keeps_its_exit_status(tmp_path):
    result, calls = _run_reload_wrapper(tmp_path, "sh", "-c", "echo step-output; exit 7")

    assert result.returncode == 7
    assert "step-output" in result.stdout
    assert calls == "start --no-block lecturesift-failure-alert@lecturesift.service.service\n"


def test_an_alert_that_cannot_be_queued_does_not_change_the_reload_status(tmp_path):
    result, calls = _run_reload_wrapper(tmp_path, "sh", "-c", "exit 3", systemctl_status=1)

    assert result.returncode == 3
    assert calls.startswith("start --no-block ")


def test_a_successful_reload_step_sends_nothing(tmp_path):
    result, calls = _run_reload_wrapper(tmp_path, "sh", "-c", "exit 0")

    assert result.returncode == 0
    assert calls == ""


def test_the_reload_wrapper_refuses_to_run_without_a_step(tmp_path):
    result, calls = _run_reload_wrapper(tmp_path)

    assert result.returncode == 2
    assert calls == ""


def test_alert_is_short_and_names_unit_host_times_and_systemd_state(env_file, tmp_path, capsys):
    sender = FakeSender()

    assert _run(env_file, tmp_path / "state", sender=sender) == 0

    # One message per recipient, the operations mailbox first, all identical.
    assert sender.addresses() == ["ops@example.com", "owner@example.org"]
    assert len({(call[2], call[3]) for call in sender.calls}) == 1
    settings, _address, subject, text = sender.calls[0]
    assert settings["EMAIL_PROVIDER"] == "resend"
    assert subject == "[LectureSift] lecturesift-instagram.service failed on ovh-prod"
    assert text.splitlines() == [
        "lecturesift-instagram.service failed on ovh-prod.",
        "This is the scheduled Instagram studio and replies job.",
        "",
        "Time: 2026-10-20 02:17:05 UTC (2026-10-20 05:17:05 Turkey time)",
        "State now: failed (failed)",
        "systemd result: exit-code",
        "Main process: exited with status 1",
        "",
        "To see its log, run this on the server:",
        "  journalctl -u lecturesift-instagram.service -n 100",
        "",
        "Further failures of this unit are not emailed for one hour.",
    ]
    assert 0 < sender.timeouts[0] <= alert.REQUEST_SECONDS

    logged = capsys.readouterr().err
    assert "sent to 2 recipient(s)" in logged
    for private in ("ops@example.com", "owner@example.org", RESEND_KEY):
        assert private not in logged


def test_the_email_carries_no_log_lines_or_configuration_values(env_file, tmp_path):
    sender = FakeSender()

    assert _run(env_file, tmp_path / "state", sender=sender) == 0

    text = sender.calls[0][3]
    for value in alert.load_settings(env_file)[0].values():
        assert value not in text
    # The script has no journal reader and no excerpt to redact.
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert '"journalctl"' not in source
    assert not hasattr(alert, "read_journal") and not hasattr(alert, "redact")


def test_the_state_comes_from_one_bounded_systemctl_show_query(env_file, tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "ActiveState=failed\nSubState=failed\nResult=signal\n"
                "ExecMainCode=2\nExecMainStatus=9\nDescription=ignored\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(alert.subprocess, "run", fake_run)
    sender = FakeSender()

    assert alert.run(
        "lecturesift-instagram.service",
        env_file=env_file,
        state_dir=tmp_path / "state",
        now=NOW,
        host="ovh-prod",
        sender=sender,
        pause=_no_pause,
    ) == 0

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [*STATE_COMMAND, "lecturesift-instagram.service"]
    assert kwargs["timeout"] == alert.STATE_QUERY_SECONDS
    assert kwargs["check"] is False
    text = sender.calls[0][3]
    assert "systemd result: signal" in text
    assert "Main process: was killed by signal 9" in text
    assert "ignored" not in text


def test_unexpected_state_values_are_dropped_not_copied_into_the_email(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "ActiveState=failed; see token=abc\nSubState=\nResult=exit-code\n"
                "ExecMainCode=9\nExecMainStatus=1\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(alert.subprocess, "run", fake_run)
    state = alert.read_unit_state("lecturesift.service")

    assert state == {"Result": "exit-code", "ExecMainCode": "9", "ExecMainStatus": "1"}
    lines = alert.describe_state("lecturesift.service", state)
    assert lines == ["State now: unknown", "systemd result: exit-code"]


@pytest.mark.parametrize(
    ("code", "status", "shown"),
    (
        ("1", "0", None),
        ("1", "3", "Main process: exited with status 3"),
        ("2", "15", "Main process: was killed by signal 15"),
        ("3", "11", "Main process: dumped core after signal 11"),
        ("0", "0", None),
    ),
)
def test_only_a_failing_main_process_is_reported(code, status, shown):
    state = {**FAILED_STATE, "ExecMainCode": code, "ExecMainStatus": status}

    lines = alert.describe_state("lecturesift.service", state)

    main_lines = [line for line in lines if line.startswith("Main process")]
    assert main_lines == ([shown] if shown else [])


@pytest.mark.parametrize(
    ("unit", "note"),
    (
        ("lecturesift.service", "this was a failed reload"),
        ("lecturesift-ingress-selector.service", "one reconciliation attempt failed"),
    ),
)
def test_a_still_active_core_or_selector_says_what_failed(env_file, tmp_path, unit, note):
    sender = FakeSender()
    active = {"ActiveState": "active", "SubState": "running", "Result": "success",
              "ExecMainCode": "0", "ExecMainStatus": "0"}

    assert _run(env_file, tmp_path / "state", sender=sender, unit=unit,
                state=lambda name: active) == 0

    text = sender.calls[0][3]
    assert "State now: active (running)" in text
    assert note in text
    assert "Main process" not in text


def test_a_running_instagram_job_gets_no_reload_note():
    active = {"ActiveState": "activating", "SubState": "start", "Result": "success"}

    lines = alert.describe_state("lecturesift-instagram.service", active)

    assert lines == ["State now: activating (start)", "systemd result: success"]


@pytest.mark.parametrize(
    "error", (OSError("systemctl missing"), subprocess.TimeoutExpired("systemctl", 10))
)
def test_an_unreadable_state_still_sends_the_alert(env_file, tmp_path, capsys, error):
    sender = FakeSender()

    def state(unit):
        raise error

    assert _run(env_file, tmp_path / "state", sender=sender, state=state) == 0
    assert sender.addresses() == ["ops@example.com", "owner@example.org"]
    text = sender.calls[0][3]
    assert "State now: unknown" in text and "systemd result: unknown" in text
    assert "could not be read" in capsys.readouterr().err


def test_an_unusual_host_name_is_not_put_in_the_subject(env_file, tmp_path):
    sender = FakeSender()

    assert alert.run(
        "lecturesift-instagram.service",
        env_file=env_file,
        state_dir=tmp_path / "state",
        now=NOW,
        host="evil\nBcc: someone@example.com",
        state_reader=lambda unit: {},
        sender=sender,
        pause=_no_pause,
    ) == 0
    assert sender.calls[0][2] == (
        "[LectureSift] lecturesift-instagram.service failed on the production host"
    )


def test_one_email_per_unit_per_hour(env_file, tmp_path, capsys):
    sender = FakeSender()
    state = tmp_path / "state"

    assert _run(env_file, state, sender=sender, now=NOW) == 0
    # The 15-minute timer fails again: suppressed.
    assert _run(env_file, state, sender=sender, now=NOW + 900) == 0
    assert _run(env_file, state, sender=sender, now=NOW + 3599) == 0
    assert sender.alerts() == 1 and len(sender.calls) == 2
    assert "suppressed" in capsys.readouterr().err
    # The limit is per unit.
    assert _run(env_file, state, sender=sender, now=NOW + 900, unit="lecturesift.service") == 0
    assert sender.alerts() == 2
    # After an hour the next failure is emailed again.
    assert _run(env_file, state, sender=sender, now=NOW + 3600) == 0
    assert sender.alerts() == 3 and len(sender.calls) == 6

    record = state / "lecturesift-instagram.service.last-sent"
    assert record.read_text(encoding="ascii") == f"{int(NOW + 3600)}\n"
    if os.name == "posix":
        assert stat.S_IMODE(record.stat().st_mode) == 0o600
        assert stat.S_IMODE(state.stat().st_mode) & 0o077 == 0


def test_a_clock_moved_backwards_does_not_silence_alerts(env_file, tmp_path):
    sender = FakeSender()
    state = tmp_path / "state"

    assert _run(env_file, state, sender=sender, now=NOW) == 0
    assert _run(env_file, state, sender=sender, now=NOW - 60) == 0
    assert sender.alerts() == 2


@pytest.mark.parametrize(
    "error",
    (
        urllib.error.HTTPError("https://api.resend.com/emails", 500, "Server Error", None, None),
        urllib.error.URLError("network unreachable"),
        TimeoutError("timed out"),
        RuntimeError("no supported EMAIL_PROVIDER is configured"),
    ),
)
def test_failed_delivery_exits_zero_and_is_retried_by_the_next_failure(
    env_file, tmp_path, capsys, error
):
    state = tmp_path / "state"
    failing = FakeSender(error)

    assert _run(env_file, state, sender=failing) == 0

    logged = capsys.readouterr().err
    assert "not sent" in logged
    assert RESEND_KEY not in logged and "ops@example.com" not in logged
    assert not (state / "lecturesift-instagram.service.last-sent").exists()
    working = FakeSender()
    assert _run(env_file, state, sender=working, now=NOW + 900) == 0
    assert working.addresses() == ["ops@example.com", "owner@example.org"]


def test_http_status_is_logged_without_the_response(env_file, tmp_path, capsys):
    error = urllib.error.HTTPError("https://api.resend.com/emails", 422, "Unprocessable", None, None)

    assert _run(env_file, tmp_path / "state", sender=FakeSender(error)) == 0
    assert "HTTPError (HTTP 422)" in capsys.readouterr().err


def test_missing_configuration_exits_zero_without_sending(env_file, tmp_path):
    sender = FakeSender()
    env_file.unlink()

    assert _run(env_file, tmp_path / "state", sender=sender) == 0
    assert sender.calls == []


def test_missing_recipients_exit_zero_without_sending(tmp_path, capsys):
    runtime = _private(tmp_path / "runtime.env", "EMAIL_PROVIDER=resend\nEMAIL_FROM=a@example.com\n")
    sender = FakeSender()

    assert _run(runtime, tmp_path / "state", sender=sender) == 0
    assert sender.calls == []
    assert "LECTURESIFT_OPS_ALERT_EMAIL" in capsys.readouterr().err


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX ownership and modes only")
def test_unsafe_runtime_env_is_refused(env_file, tmp_path):
    sender = FakeSender()
    env_file.chmod(0o644)

    assert _run(env_file, tmp_path / "state", sender=sender) == 0
    assert sender.calls == []


def test_unwatched_unit_is_refused_before_any_state_or_delivery(env_file, tmp_path):
    sender = FakeSender()
    state = tmp_path / "state"

    def no_state(unit):
        raise AssertionError("an unwatched unit must not be queried")

    assert _run(env_file, state, sender=sender, unit="sshd.service", state=no_state) == 2
    assert _run(env_file, state, sender=sender, unit="../lecturesift.service", state=no_state) == 2
    assert sender.calls == []
    assert not state.exists()
    assert alert.main([]) == 2


def test_hand_started_instance_without_the_suffix_shares_the_unit_rate_limit(env_file, tmp_path):
    sender = FakeSender()
    state = tmp_path / "state"

    assert _run(env_file, state, sender=sender, unit="lecturesift-instagram") == 0
    assert _run(env_file, state, sender=sender, now=NOW + 60) == 0

    assert sender.alerts() == 1
    assert sender.calls[0][2] == "[LectureSift] lecturesift-instagram.service failed on ovh-prod"
    assert [path.name for path in state.iterdir()] == ["lecturesift-instagram.service.last-sent"]


def test_main_never_fails_the_alert_unit_on_an_unexpected_error(monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise ValueError(RESEND_KEY)

    monkeypatch.setattr(alert, "run", explode)

    assert alert.main(["lecturesift.service"]) == 0
    logged = capsys.readouterr().err
    assert "unexpected ValueError" in logged
    assert RESEND_KEY not in logged


def test_env_values_are_decoded_with_shell_quoting(tmp_path):
    runtime = _private(
        tmp_path / "runtime.env",
        "\n".join(
            (
                "EMAIL_PROVIDER=smtp",
                'EMAIL_FROM="LectureSift \\"Ops\\" <noreply@example.com>"',
                'SMTP_PASSWORD="Synthetic\\"Quote-Passw0rd"',
                "SMTP_USERNAME=lecturesift\\ mailer",
                "export RESEND_API_KEY='Synth$Key-0123456789'",
                "LECTURESIFT_OPS_ALERT_EMAIL=ops@example.com",
            )
        )
        + "\n",
    )

    settings, undecoded = alert.load_settings(runtime)

    assert undecoded == 0
    assert settings["EMAIL_FROM"] == 'LectureSift "Ops" <noreply@example.com>'
    assert settings["SMTP_PASSWORD"] == 'Synthetic"Quote-Passw0rd'
    assert settings["SMTP_USERNAME"] == "lecturesift mailer"
    assert settings["RESEND_API_KEY"] == "Synth$Key-0123456789"


@pytest.mark.parametrize(
    "line",
    (
        'LECTURESIFT_OPS_ALERT_EMAIL="unterminated',
        "LECTURESIFT_OPS_ALERT_EMAIL=$HOME-derived",
        'LECTURESIFT_OPS_ALERT_EMAIL="prefix-$(command)"',
        "LECTURESIFT_OPS_ALERT_EMAIL=two words",
        "LECTURESIFT_OPS_ALERT_EMAIL= leading-space",
    ),
)
def test_an_undecodable_assignment_is_never_served_from_an_earlier_one(tmp_path, capsys, line):
    runtime = _private(
        tmp_path / "runtime.env",
        f"EMAIL_PROVIDER=resend\nLECTURESIFT_OPS_ALERT_EMAIL=stale@example.com\n{line}\nset -a\n",
    )

    settings, undecoded = alert.load_settings(runtime)

    assert undecoded == 2
    assert "LECTURESIFT_OPS_ALERT_EMAIL" not in settings
    sender = FakeSender()
    assert _run(runtime, tmp_path / "state", sender=sender) == 0
    assert sender.calls == []
    logged = capsys.readouterr().err
    assert "2 line(s) of the runtime environment could not be decoded" in logged
    assert "stale@example.com" not in logged


def _many_admins_runtime(tmp_path: Path, count: int) -> Path:
    admins = ", ".join(f"admin{number:02d}@example.org" for number in range(count))
    return _private(
        tmp_path / "runtime.env",
        "\n".join(
            (
                "EMAIL_PROVIDER=resend",
                "EMAIL_FROM=noreply@example.com",
                f"RESEND_API_KEY={RESEND_KEY}",
                "LECTURESIFT_OPS_ALERT_EMAIL=ops@example.com",
                # A repeat of the ops mailbox and of an admin is sent only once.
                f'LECTURESIFT_ADMIN_EMAILS="{admins}, OPS@example.com, admin03@example.org"',
            )
        )
        + "\n",
    )


def test_every_recipient_gets_a_message_of_their_own(tmp_path, capsys):
    runtime = _many_admins_runtime(tmp_path, 22)
    expected = ["ops@example.com", *(f"admin{number:02d}@example.org" for number in range(22))]
    sender = FakeSender()
    state = tmp_path / "state"

    assert alert.recipients(alert.load_settings(runtime)[0]) == expected
    assert _run(runtime, state, sender=sender) == 0

    # One message per address, in order, so nobody sees another recipient.
    assert sender.addresses() == expected
    assert len({(call[2], call[3]) for call in sender.calls}) == 1
    for _settings, address, _subject, text in sender.calls:
        assert "@" not in text
    logged = capsys.readouterr().err
    assert "sent to 23 recipient(s)" in logged
    assert "@example" not in logged

    # The hourly limit counts the whole alert, not each message.
    assert len(list(state.iterdir())) == 1
    again = FakeSender()
    assert _run(runtime, state, sender=again, now=NOW + 60) == 0
    assert again.calls == []


class RefusingSender(FakeSender):
    """Fails the messages to the given addresses with the given error."""

    def __init__(self, refused: set[str], error: Exception):
        super().__init__()
        self.refused = refused
        self.failure = error

    def __call__(self, settings, address, subject, text, timeout):
        super().__call__(settings, address, subject, text, timeout)
        if address in self.refused:
            raise self.failure


def test_refused_recipients_are_counted_and_the_others_still_get_the_alert(tmp_path, capsys):
    runtime = _many_admins_runtime(tmp_path, 22)
    refusal = smtplib.SMTPRecipientsRefused({"admin04@example.org": (550, b"5.1.1 No such user")})
    sender = RefusingSender({"admin04@example.org", "admin17@example.org"}, refusal)
    state = tmp_path / "state"

    assert _run(runtime, state, sender=sender) == 0

    assert len(sender.calls) == 23
    logged = capsys.readouterr().err
    assert (
        "reached 21 of 23 recipient(s); refused or failed: 2 (SMTPRecipientsRefused x2); "
        "not tried: 0." in logged
    )
    assert "sent to 21 recipient(s)" in logged
    assert "@example" not in logged and RESEND_KEY not in logged
    # Partly delivered still counts, so the others are not sent a copy again.
    assert (state / "lecturesift-instagram.service.last-sent").exists()


def test_an_alert_every_recipient_refused_is_not_counted_and_is_retried(env_file, tmp_path, capsys):
    state = tmp_path / "state"
    error = urllib.error.HTTPError("https://api.resend.com/emails", 429, "Too Many", None, None)

    assert _run(env_file, state, sender=FakeSender(error)) == 0

    logged = capsys.readouterr().err
    assert (
        "not sent: reached 0 of 2 recipient(s); refused or failed: 2 (HTTPError (HTTP 429) x2)"
        in logged
    )
    assert not (state / "lecturesift-instagram.service.last-sent").exists()
    working = FakeSender()
    assert _run(env_file, state, sender=working, now=NOW + 900) == 0
    assert working.alerts() == 1


def test_the_delivery_deadline_stops_sending_and_reports_who_was_reached(tmp_path, capsys):
    runtime = _many_admins_runtime(tmp_path, 4)
    state = tmp_path / "state"
    record = state / "lecturesift-instagram.service.last-sent"
    moment = [1000.0]
    recorded_before = []
    sender = FakeSender()

    def slow_provider(settings, address, subject, text, timeout):
        recorded_before.append(record.exists())
        sender(settings, address, subject, text, timeout)
        # Each request takes 25 seconds of the 60-second delivery time.
        moment[0] += 25

    assert _run(runtime, state, sender=slow_provider, clock=lambda: moment[0]) == 0

    # Five recipients: three sends fit before the deadline, two are not tried.
    assert sender.addresses() == ["ops@example.com", "admin00@example.org", "admin01@example.org"]
    assert sender.timeouts == [alert.REQUEST_SECONDS, alert.REQUEST_SECONDS, 10]
    # The alert is counted as soon as the first message is accepted, so a
    # stop at any later point cannot make the next failure resend it.
    assert recorded_before == [False, True, True]
    logged = capsys.readouterr().err
    assert (
        "reached 3 of 5 recipient(s); refused or failed: 0; not tried: 2 "
        f"(the {alert.DELIVERY_SECONDS}-second delivery limit was reached)." in logged
    )
    assert "@example" not in logged


def test_sends_are_paced_under_the_resend_request_rate(tmp_path):
    runtime = _many_admins_runtime(tmp_path, 4)
    pauses = []
    sender = FakeSender()

    assert _run(runtime, tmp_path / "state", sender=sender, pause=pauses.append) == 0

    assert len(sender.calls) == 5
    # No pause before the first send; Resend allows two requests per second.
    assert pauses == [alert.SEND_INTERVAL_SECONDS] * 4
    assert alert.SEND_INTERVAL_SECONDS >= 0.5


def test_a_hung_request_is_abandoned_at_its_time_limit(env_file, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(alert, "REQUEST_SECONDS", 0.2)
    release = threading.Event()

    def hung_provider(settings, address, subject, text, timeout):
        release.wait(10)

    state = tmp_path / "state"
    started = time.monotonic()
    try:
        assert _run(env_file, state, sender=hung_provider) == 0
    finally:
        release.set()

    assert time.monotonic() - started < 5
    assert "not sent: reached 0 of 2 recipient(s); refused or failed: 2 (DeliveryTimeout x2)" in (
        capsys.readouterr().err
    )
    assert not (state / "lecturesift-instagram.service.last-sent").exists()


RESEND_SETTINGS = {
    "EMAIL_PROVIDER": "resend",
    "EMAIL_FROM": "LectureSift <noreply@example.com>",
    "RESEND_API_KEY": RESEND_KEY,
}


class FakeResendResponse:
    def __init__(self, status=200, body=b'{"id": "4ef9a417-02e9-4d39-ad75-9611e0fcc33c"}'):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit=-1):
        return self.body


def test_resend_request_goes_to_one_address_with_the_existing_configuration(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return FakeResendResponse()

    monkeypatch.setattr(alert.urllib.request, "urlopen", fake_urlopen)
    assert alert.send_email(RESEND_SETTINGS, "ops@example.com", "subject", "body", 7) is None

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == f"Bearer {RESEND_KEY}"
    assert captured["body"] == {
        "from": "LectureSift <noreply@example.com>",
        "to": ["ops@example.com"],
        "subject": "subject",
        "text": "body",
    }
    assert captured["timeout"] == 7


@pytest.mark.parametrize(
    ("response", "status"),
    (
        # urlopen hands back a redirect it did not follow; it is not a delivery.
        (FakeResendResponse(status=302, body=b""), 302),
        (FakeResendResponse(body=b'{"name": "validation_error", "message": "bad"}'), 200),
        (FakeResendResponse(body=b'{"id": ""}'), 200),
        (FakeResendResponse(body=b"<html>gateway</html>"), 200),
        (FakeResendResponse(body=b'["4ef9a417"]'), 200),
    ),
)
def test_a_resend_answer_without_a_message_id_is_not_a_delivery(monkeypatch, response, status):
    monkeypatch.setattr(alert.urllib.request, "urlopen", lambda request, timeout: response)

    with pytest.raises(alert.DeliveryRejected) as rejected:
        alert.send_email(RESEND_SETTINGS, "ops@example.com", "s", "t", 7)
    assert rejected.value.status == status


SMTP_SETTINGS = {
    "EMAIL_PROVIDER": "smtp",
    "EMAIL_FROM": "noreply@example.com",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_USERNAME": "mailer",
    "SMTP_PASSWORD": "synthetic-smtp-password",
}


def _fake_smtp(monkeypatch, events, answer=None):
    class FakeSMTP:
        def __init__(self, host, port, timeout):
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context):
            events.append(("starttls",))

        def login(self, username, password):
            events.append(("login", username))

        def send_message(self, message, to_addrs=None):
            events.append(("send", message["To"], to_addrs))
            if isinstance(answer, Exception):
                raise answer
            return dict(answer or {})

    monkeypatch.setattr(alert.smtplib, "SMTP", FakeSMTP)


def test_smtp_sends_to_one_address_with_the_app_tls_switch_and_time_limit(monkeypatch):
    events = []
    _fake_smtp(monkeypatch, events)

    assert alert.send_email(SMTP_SETTINGS, "a@example.com", "s", "t", 7) is None
    assert events == [
        ("connect", "smtp.example.com", 587, 7),
        ("starttls",),
        ("login", "mailer"),
        ("send", "a@example.com", ["a@example.com"]),
    ]

    # Like lecturesift.config, only the literal "true" turns STARTTLS on.
    events.clear()
    alert.send_email(
        {**SMTP_SETTINGS, "SMTP_USE_TLS": "1", "SMTP_PORT": "2525"}, "a@example.com", "s", "t", 7
    )
    assert events[0] == ("connect", "smtp.example.com", 2525, 7)
    assert ("starttls",) not in events


def test_a_refused_smtp_recipient_is_not_a_delivery(monkeypatch):
    refusal = smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"5.1.1 No such user")})
    _fake_smtp(monkeypatch, [], answer=refusal)
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        alert.send_email(SMTP_SETTINGS, "a@example.com", "s", "t", 7)

    # A refusal map returned instead of raised is treated the same way.
    _fake_smtp(monkeypatch, [], answer={"a@example.com": (550, b"5.1.1 No such user")})
    with pytest.raises(alert.DeliveryRejected):
        alert.send_email(SMTP_SETTINGS, "a@example.com", "s", "t", 7)


def test_unconfigured_provider_raises_for_run_to_log():
    with pytest.raises(RuntimeError):
        alert.send_email({"EMAIL_PROVIDER": "none", "EMAIL_FROM": "a@example.com"}, "b@example.com", "s", "t", 5)
    with pytest.raises(RuntimeError):
        alert.send_email({"EMAIL_PROVIDER": "resend", "EMAIL_FROM": "a@example.com"}, "b@example.com", "s", "t", 5)


def test_operator_docs_explain_the_manual_alert_test():
    runbook = _read("deploy/POST_CUTOVER_RELEASE.md")
    deployment = _read("VPS_DEPLOYMENT.md")

    section = runbook.split("## Failure alerts", 1)[1]
    # The manual test ends by removing the hourly record it wrote, which is
    # the same record a real Instagram failure would be suppressed by.
    record = alert.DEFAULT_STATE_DIR / "lecturesift-instagram.service.last-sent"
    procedure = (
        "```sh\n"
        "sudo systemctl start lecturesift-failure-alert@lecturesift-instagram.service.service\n"
        "journalctl -u lecturesift-failure-alert@lecturesift-instagram.service.service -n 20\n"
        f"sudo rm -f {record.as_posix()}\n"
        "```\n"
    )
    assert procedure in section
    assert section.count("sudo systemctl start lecturesift-failure-alert@") == 1
    assert "no log lines" in section
    assert "lecturesift-failure-alert@.service" in deployment
