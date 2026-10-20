from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
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
OWNER_PASSWORD = "Synthetic-Owner-Passw0rd-77"
ADMIN_KEY = "SyntheticEmergencyAdminKey0123456789abcd"
ACCESS_TOKEN = "SyntheticGraphAccessToken0123456789XYZ"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _unit_section(text: str) -> str:
    return text.split("[Service]", 1)[0]


def _private(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def env_files(tmp_path: Path) -> list[Path]:
    runtime = _private(
        tmp_path / "runtime.env",
        "\n".join(
            (
                "# Synthetic production-shaped runtime file",
                "EMAIL_PROVIDER=resend",
                'EMAIL_FROM="LectureSift <noreply@example.com>"',
                f"RESEND_API_KEY={RESEND_KEY}",
                "LECTURESIFT_OPS_ALERT_EMAIL=ops@example.com",
                "LECTURESIFT_ADMIN_EMAILS=owner@example.org, OPS@example.com,not-an-address",
                f"ADMIN_ADMIN={ADMIN_KEY}",
                "DATABASE_URL=postgresql+psycopg://lecturesift_app:"
                f"{DB_PASSWORD}@postgres:5432/lecturesift",
                "POSTGRES_DB=lecturesift",
                "LECTURESIFT_WORK_DIR=/var/lib/lecturesift",
                "LECTURESIFT_MAX_VIDEO_BYTES=1073741824",
                f"INSTAGRAM_ACCESS_TOKEN='{ACCESS_TOKEN}'",
            )
        )
        + "\n",
    )
    database = _private(
        tmp_path / "postgres.env",
        f"POSTGRES_USER=lecturesift_owner\nPOSTGRES_PASSWORD={OWNER_PASSWORD}\n",
    )
    # restic.env is optional, exactly as in preflight.
    return [runtime, database, tmp_path / "restic.env"]


class FakeSender:
    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[dict, list[str], str, str]] = []
        self.error = error

    def __call__(self, settings, to, subject, text):
        self.calls.append((dict(settings), list(to), subject, text))
        if self.error is not None:
            raise self.error


JOURNAL = [
    "2026-10-20T02:17:01+0000 vps docker[812]: Instagram replies failed: 400 "
    f"https://graph.facebook.com/v21.0/me/messages?access_token={ACCESS_TOKEN}&fields=id",
    f"2026-10-20T02:17:01+0000 vps docker[812]: retrying with Authorization: Bearer {RESEND_KEY}",
    f"2026-10-20T02:17:02+0000 vps docker[812]: database {DB_PASSWORD} rejected",
    "2026-10-20T02:17:02+0000 vps docker[812]: reply to customer.name@example.net failed",
    f"2026-10-20T02:17:02+0000 vps docker[812]: admin key {ADMIN_KEY} and {OWNER_PASSWORD}",
    "2026-10-20T02:17:03+0000 vps docker[812]: Cookie: session=abc123; csrftoken=def456",
    "2026-10-20T02:17:04+0000 vps systemd[1]: lecturesift-instagram.service: "
    "Main process exited, code=exited, status=1/FAILURE",
    "2026-10-20T02:17:04+0000 vps systemd[1]: Failed to start lecturesift-instagram.service.",
]


def _run(env_files, state_dir, *, unit="lecturesift-instagram.service", now=NOW, sender=None,
         journal=None):
    return alert.run(
        unit,
        env_files=env_files,
        state_dir=state_dir,
        now=now,
        host="ovh-prod",
        journal_reader=journal or (lambda name: list(JOURNAL)),
        sender=sender,
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
    assert "Environment=LECTURESIFT_DB_ENV_FILE=/etc/lecturesift/postgres.env" in template
    assert "Environment=LECTURESIFT_RESTIC_ENV_FILE=/etc/lecturesift/restic.env" in template
    # Secrets stay in the root-only files; systemd never exports them.
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
    assert timeout <= 120
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


def test_alert_names_unit_host_and_time_and_redacts_every_secret(env_files, tmp_path, capsys):
    sender = FakeSender()

    assert _run(env_files, tmp_path / "state", sender=sender) == 0

    assert len(sender.calls) == 1
    settings, to, subject, text = sender.calls[0]
    assert to == ["ops@example.com", "owner@example.org"]
    assert settings["EMAIL_PROVIDER"] == "resend"
    assert subject == "[LectureSift] lecturesift-instagram.service failed on ovh-prod"
    assert "lecturesift-instagram.service failed on ovh-prod at 2026-10-20 02:17:05 UTC." in text
    assert "systemctl status lecturesift-instagram.service" in text
    assert "Further failures of this unit are not emailed for one hour." in text
    assert "Main process exited, code=exited, status=1/FAILURE" in text
    assert "Failed to start lecturesift-instagram.service." in text
    for secret in (RESEND_KEY, DB_PASSWORD, OWNER_PASSWORD, ADMIN_KEY, ACCESS_TOKEN,
                   "abc123", "def456", "customer.name@example.net"):
        assert secret not in text
    assert "access_token=[redacted]" in text
    assert "reply to [email] failed" in text
    assert "Cookie: [redacted]" in text
    assert len(text.splitlines()) < 30 + 20

    logged = capsys.readouterr().err
    assert "sent to 2 recipient(s)" in logged
    for private in ("ops@example.com", "owner@example.org", RESEND_KEY):
        assert private not in logged


def test_one_email_per_unit_per_hour(env_files, tmp_path, capsys):
    sender = FakeSender()
    state = tmp_path / "state"

    assert _run(env_files, state, sender=sender, now=NOW) == 0
    # The 15-minute timer fails again: suppressed.
    assert _run(env_files, state, sender=sender, now=NOW + 900) == 0
    assert _run(env_files, state, sender=sender, now=NOW + 3599) == 0
    assert len(sender.calls) == 1
    assert "suppressed" in capsys.readouterr().err
    # The limit is per unit.
    assert _run(env_files, state, sender=sender, now=NOW + 900, unit="lecturesift.service") == 0
    assert len(sender.calls) == 2
    # After an hour the next failure is emailed again.
    assert _run(env_files, state, sender=sender, now=NOW + 3600) == 0
    assert len(sender.calls) == 3

    record = state / "lecturesift-instagram.service.last-sent"
    assert record.read_text(encoding="ascii") == f"{int(NOW + 3600)}\n"
    if os.name == "posix":
        assert stat.S_IMODE(record.stat().st_mode) == 0o600
        assert stat.S_IMODE(state.stat().st_mode) & 0o077 == 0


def test_a_clock_moved_backwards_does_not_silence_alerts(env_files, tmp_path):
    sender = FakeSender()
    state = tmp_path / "state"

    assert _run(env_files, state, sender=sender, now=NOW) == 0
    assert _run(env_files, state, sender=sender, now=NOW - 60) == 0
    assert len(sender.calls) == 2


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
    env_files, tmp_path, capsys, error
):
    state = tmp_path / "state"
    failing = FakeSender(error)

    assert _run(env_files, state, sender=failing) == 0

    logged = capsys.readouterr().err
    assert "not sent" in logged
    assert RESEND_KEY not in logged and "ops@example.com" not in logged
    assert not (state / "lecturesift-instagram.service.last-sent").exists()
    working = FakeSender()
    assert _run(env_files, state, sender=working, now=NOW + 900) == 0
    assert len(working.calls) == 1


def test_http_status_is_logged_without_the_response(env_files, tmp_path, capsys):
    error = urllib.error.HTTPError("https://api.resend.com/emails", 422, "Unprocessable", None, None)

    assert _run(env_files, tmp_path / "state", sender=FakeSender(error)) == 0
    assert "HTTPError (HTTP 422)" in capsys.readouterr().err


def test_missing_configuration_exits_zero_without_sending(env_files, tmp_path):
    sender = FakeSender()
    env_files[0].unlink()

    assert _run(env_files, tmp_path / "state", sender=sender) == 0
    assert sender.calls == []


def test_missing_recipients_exit_zero_without_sending(tmp_path, capsys):
    runtime = _private(tmp_path / "runtime.env", "EMAIL_PROVIDER=resend\nEMAIL_FROM=a@example.com\n")
    sender = FakeSender()

    assert _run([runtime], tmp_path / "state", sender=sender) == 0
    assert sender.calls == []
    assert "LECTURESIFT_OPS_ALERT_EMAIL" in capsys.readouterr().err


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX ownership and modes only")
def test_unsafe_runtime_env_is_refused(env_files, tmp_path):
    sender = FakeSender()
    env_files[0].chmod(0o644)

    assert _run(env_files, tmp_path / "state", sender=sender) == 0
    assert sender.calls == []


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX ownership and modes only")
def test_unsafe_secondary_env_leaves_the_journal_out(env_files, tmp_path):
    sender = FakeSender()
    env_files[1].chmod(0o644)

    def journal(unit):
        raise AssertionError("the journal must not be read without every redaction source")

    assert _run(env_files, tmp_path / "state", sender=sender, journal=journal) == 0
    text = sender.calls[0][3]
    assert "journal lines were left out" in text
    assert OWNER_PASSWORD not in text


def test_unreadable_journal_still_sends_the_alert(env_files, tmp_path):
    sender = FakeSender()

    def journal(unit):
        raise OSError("journalctl failed")

    assert _run(env_files, tmp_path / "state", sender=sender, journal=journal) == 0
    assert len(sender.calls) == 1
    assert "journal lines were left out" in sender.calls[0][3]


def test_unwatched_unit_is_refused_before_any_state_or_delivery(env_files, tmp_path):
    sender = FakeSender()
    state = tmp_path / "state"

    assert _run(env_files, state, sender=sender, unit="sshd.service") == 2
    assert _run(env_files, state, sender=sender, unit="../lecturesift.service") == 2
    assert sender.calls == []
    assert not state.exists()
    assert alert.main([]) == 2


def test_hand_started_instance_without_the_suffix_shares_the_unit_rate_limit(env_files, tmp_path):
    sender = FakeSender()
    state = tmp_path / "state"

    assert _run(env_files, state, sender=sender, unit="lecturesift-instagram") == 0
    assert _run(env_files, state, sender=sender, now=NOW + 60) == 0

    assert len(sender.calls) == 1
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


@pytest.mark.parametrize(
    ("line", "hidden", "kept"),
    (
        ("postgresql+psycopg://lecturesift_app:pw-9@postgres:5432/lecturesift", "pw-9",
         "postgresql+psycopg://[redacted]@postgres:5432/lecturesift"),
        ('{"access_token": "Tok3nValue", "id": 5}', "Tok3nValue", '"id": 5'),
        ("X-Amz-Signature=deadbeef&X-Amz-Credential=KEY/2026", "deadbeef", "X-Amz-Signature=[redacted]"),
        ("Set-Cookie: sid=s3cr3t; Path=/", "s3cr3t", "Set-Cookie: [redacted]"),
        ("password=hunter2 user=lecturesift_app", "hunter2", "user=lecturesift_app"),
        ("Bearer AbC.dEf-123", "AbC.dEf-123", "Bearer [redacted]"),
        ("image sha256:" + "ab" * 32, "ab" * 32, "image sha256:[redacted]"),
        ("token MixedCaseOpaque0123456789abcXYZ end", "MixedCaseOpaque0123456789abcXYZ", " end"),
        ("mail Jane.Doe+x@example.co.uk now", "Jane.Doe+x@example.co.uk", "mail [email] now"),
        ("\x1b[31mred\x1b[0m bell\x07", "\x1b", "red bell"),
    ),
)
def test_redaction_hides_credentials_and_addresses(line, hidden, kept):
    redacted = alert.redact(line, [])

    assert hidden not in redacted
    assert kept in redacted


@pytest.mark.parametrize(
    "line",
    (
        "lecturesift-instagram.service: Main process exited, code=exited, status=1/FAILURE",
        'File "/usr/local/lib/python3.12/site-packages/lecturesift/daily_social.py", line 704',
        "Triggering lecturesift-failure-alert@lecturesift-instagram.service.service",
        "/opt/lecturesift/deploy/preflight.sh: Missing required environment values: OPENAI_API_KEY",
        "Ingress reconciliation attempt failed; durable state remains fail-closed.",
    ),
)
def test_redaction_keeps_unit_names_paths_and_diagnostics(line):
    assert alert.redact(line, []) == line


def test_configuration_values_are_redacted_but_plain_names_and_paths_stay(tmp_path, env_files):
    settings, secrets, complete = alert.load_env_files(env_files)

    assert complete is True
    assert settings["EMAIL_FROM"] == "LectureSift <noreply@example.com>"
    for secret in (RESEND_KEY, ADMIN_KEY, ACCESS_TOKEN, OWNER_PASSWORD):
        assert secret in secrets
    assert any(DB_PASSWORD in value for value in secrets)
    assert "lecturesift" not in secrets
    assert "lecturesift_owner" not in secrets
    assert "/var/lib/lecturesift" not in secrets
    assert "1073741824" not in secrets
    assert secrets == sorted(secrets, key=len, reverse=True)
    line = f"user lecturesift_owner at /var/lib/lecturesift used {OWNER_PASSWORD}"
    assert alert.redact(line, secrets) == (
        "user lecturesift_owner at /var/lib/lecturesift used [redacted]"
    )


def test_long_lines_are_cut_without_leaving_half_a_secret():
    line = "x " * 990 + "partial" + "Z9" * 40
    redacted = alert.redact(line, [])

    assert "partial" not in redacted
    assert redacted.endswith("[truncated]")
    assert len(redacted) <= alert.MAX_SHOWN_LINE + len(" [truncated]")


def test_resend_request_uses_the_existing_configuration(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(alert.urllib.request, "urlopen", fake_urlopen)
    alert.send_email(
        {"EMAIL_PROVIDER": "resend", "EMAIL_FROM": "LectureSift <noreply@example.com>",
         "RESEND_API_KEY": RESEND_KEY},
        ["ops@example.com", "owner@example.org"],
        "subject",
        "body",
    )

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == f"Bearer {RESEND_KEY}"
    assert captured["body"] == {
        "from": "LectureSift <noreply@example.com>",
        "to": ["ops@example.com", "owner@example.org"],
        "subject": "subject",
        "text": "body",
    }
    assert captured["timeout"] <= 30


def test_unconfigured_provider_raises_for_run_to_log():
    with pytest.raises(RuntimeError):
        alert.send_email({"EMAIL_PROVIDER": "none", "EMAIL_FROM": "a@example.com"}, ["b@example.com"], "s", "t")
    with pytest.raises(RuntimeError):
        alert.send_email({"EMAIL_PROVIDER": "resend", "EMAIL_FROM": "a@example.com"}, ["b@example.com"], "s", "t")


def test_operator_docs_explain_the_manual_alert_test():
    runbook = _read("deploy/POST_CUTOVER_RELEASE.md")
    deployment = _read("VPS_DEPLOYMENT.md")

    section = runbook.split("## Failure alerts", 1)[1]
    assert "sudo systemctl start lecturesift-failure-alert@lecturesift-instagram.service.service" in section
    assert "/var/lib/lecturesift/failure-alerts/" in section
    assert "journalctl -u lecturesift-failure-alert@lecturesift-instagram.service.service" in section
    assert "lecturesift-failure-alert@.service" in deployment
