from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lecturesift import adsense_management
from lecturesift import adsense_status_snapshot as container


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "deploy" / "adsense_status_alert.py"
SPEC = importlib.util.spec_from_file_location("adsense_status_alert", SCRIPT_PATH)
assert SPEC and SPEC.loader
status_alert = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = status_alert
SPEC.loader.exec_module(status_alert)

NOW = datetime(2026, 10, 21, 3, 10, 5, tzinfo=timezone.utc).timestamp()
DAY = 86400
# Synthetic values only; none has the shape of a real provider credential.
RESEND_KEY = "synthetic-resend-Key-0123456789"
CLIENT_ID = "000000000000-synthetic.apps.googleusercontent.com"
CLIENT_SECRET = "Synthetic-AdSense-Client-Secret-42"
REFRESH_TOKEN = "Synthetic-AdSense-Refresh-Token-0123456789"
# The live read of 2026-10-20 15:58 UTC.
LIVE = {
    "site_state": "NEEDS_ATTENTION",
    "account_state": "READY",
    "alerts": {
        "adsense-onboarding-incomplete": "WARNING",
        "ua-conflict-policy-update": "WARNING",
    },
    "policy_issues": 0,
    "auto_ads": True,
}
STATE_KEYS = {
    "schema",
    "snapshot",
    "checked_at",
    "consecutive_failures",
    "unreadable_email_sent",
}


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _private(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    # runtime.env is the master file, so it also holds the AdSense values the
    # role generator copies into api.env. The host script must never use them.
    return _private(
        tmp_path / "runtime.env",
        "\n".join(
            (
                "EMAIL_PROVIDER=resend",
                'EMAIL_FROM="LectureSift <noreply@example.com>"',
                f"RESEND_API_KEY={RESEND_KEY}",
                "LECTURESIFT_OPS_ALERT_EMAIL=ops@example.com",
                'LECTURESIFT_ADMIN_EMAILS="owner@example.org, OPS@example.com"',
                "LECTURESIFT_ADSENSE_API_ENABLED=true",
                f"LECTURESIFT_ADSENSE_API_CLIENT_ID={CLIENT_ID}",
                f"LECTURESIFT_ADSENSE_API_CLIENT_SECRET={CLIENT_SECRET}",
                f"LECTURESIFT_ADSENSE_API_REFRESH_TOKEN='{REFRESH_TOKEN}'",
                "LECTURESIFT_ADSENSE_PUBLISHER_ID=ca-pub-0000000000000000",
            )
        )
        + "\n",
    )


class FakeSender:
    """Records one call per message; each message goes to exactly one address."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, str, str]] = []
        self.error = error

    def __call__(self, settings, address, subject, text, timeout):
        self.calls.append((address, subject, text))
        if self.error is not None:
            raise self.error

    def emails(self) -> list[tuple[str, str]]:
        # The operations mailbox is first in every email.
        return [(subject, text) for address, subject, text in self.calls if address == "ops@example.com"]

    def subjects(self) -> list[str]:
        return [subject for subject, _text in self.emails()]


def ok(**changes) -> object:
    snapshot = status_alert.validate_snapshot({**LIVE, **changes})
    assert snapshot is not None
    return status_alert.Reading(snapshot)


def failed(part: str = "account", error: str = "provider_unavailable") -> object:
    return status_alert.Reading(None, part, error)


def _run(env_file, state_dir, reading, *, now=NOW, sender=None, manual=False, pauses=None):
    sender = sender if sender is not None else FakeSender()
    code = status_alert.run(
        env_file=env_file,
        state_dir=state_dir,
        manual_test=manual,
        now=now,
        reader=lambda: reading,
        sender=sender,
        pause=pauses.append if pauses is not None else (lambda seconds: None),
    )
    return code, sender


def _state(state_dir: Path) -> dict:
    return json.loads((state_dir / "state.json").read_text(encoding="utf-8"))


BASELINE = "[LectureSift] AdSense durum takibi başladı"
CHANGED = "[LectureSift] AdSense durumu değişti"
UNREADABLE = "[LectureSift] AdSense durumu okunamıyor"
TEST = "[LectureSift] AdSense durum kontrolü testi"


def test_first_check_saves_the_snapshot_and_sends_one_baseline_email(env_file, tmp_path, capsys):
    state_dir = tmp_path / "state"
    pauses: list[float] = []

    code, sender = _run(env_file, state_dir, ok(), pauses=pauses)

    assert code == 0
    # One message per recipient, the operations mailbox first, all identical.
    assert [call[0] for call in sender.calls] == ["ops@example.com", "owner@example.org"]
    assert len({call[1:] for call in sender.calls}) == 1
    assert pauses == [status_alert.alert_mail.SEND_INTERVAL_SECONDS]
    subject, text = sender.emails()[0]
    assert subject == BASELINE
    lines = text.splitlines()
    assert lines[0].startswith("lecturesift.com için günlük AdSense durum kontrolü çalışıyor.")
    assert "Zaman: 2026-10-21 03:10:05 UTC (2026-10-21 06:10:05 Türkiye saati)" in lines
    assert "Site (lecturesift.com): NEEDS_ATTENTION (İşlem gerekiyor)" in lines
    assert (
        "Anlamı: AdSense panelinde Siteler > lecturesift.com satırındaki sebebi kontrol et. "
        "Düzelttikten sonra aynı yerden incelemeyi yeniden iste." in lines
    )
    assert "Hesap: READY (Hazır)" in lines
    assert any(line.startswith("- adsense-onboarding-incomplete, WARNING (uyarı). Anlamı:") for line in lines)
    assert any(line.startswith("- ua-conflict-policy-update, WARNING (uyarı). Anlamı:") for line in lines)
    assert "Politika sorunları: 0" in lines
    assert "Otomatik reklamlar: Açık" in lines
    assert "Yönetim paneli: https://lecturesift.com/admin.html" in lines

    saved = _state(state_dir)
    assert saved == {
        "schema": status_alert.STATE_SCHEMA,
        "snapshot": LIVE,
        "checked_at": "2026-10-21T03:10:05Z",
        "consecutive_failures": 0,
        "unreadable_email_sent": False,
    }
    if os.name == "posix":
        assert stat.S_IMODE((state_dir / "state.json").stat().st_mode) == 0o600
        assert stat.S_IMODE(state_dir.stat().st_mode) & 0o077 == 0
    logged = capsys.readouterr().err
    assert "AdSense baseline email sent to 2 recipient(s)." in logged
    assert "@example" not in logged and RESEND_KEY not in logged


def test_an_unchanged_status_sends_nothing_and_records_the_check(env_file, tmp_path, capsys):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())
    # Same alerts in another order are the same status.
    reordered = ok(alerts=dict(reversed(list(LIVE["alerts"].items()))))

    code, sender = _run(env_file, state_dir, reordered, now=NOW + DAY)

    assert code == 0
    assert sender.calls == []
    assert _state(state_dir)["checked_at"] == "2026-10-22T03:10:05Z"
    assert _state(state_dir)["snapshot"] == LIVE
    assert "AdSense status unchanged" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("changes", "expected"),
    (
        (
            {"site_state": "READY"},
            [
                "Site durumu: NEEDS_ATTENTION (İşlem gerekiyor) → READY (Hazır)",
                "Anlamı: Google siteyi onayladı. Reklamların gerçekten gösterildiğini ve "
                "kazancı AdSense raporlarından doğrula.",
            ],
        ),
        (
            {"account_state": "NEEDS_ATTENTION"},
            ["Hesap durumu: READY (Hazır) → NEEDS_ATTENTION (İşlem gerekiyor)"],
        ),
        ({"policy_issues": 2}, ["Politika sorunu sayısı: 0 → 2"]),
        ({"auto_ads": False}, ["Otomatik reklamlar: Açık → Kapalı"]),
        (
            {"alerts": {**LIVE["alerts"], "ua-conflict-policy-update": "SEVERE"}},
            [
                "- Önem derecesi değişti: ua-conflict-policy-update, WARNING (uyarı) → "
                "SEVERE (ciddi). Anlamı: Ukrayna ile ilgili genel bir politika duyurusu. "
                "Tek başına bu siteye verilmiş bir ihlal kararı değil."
            ],
        ),
        (
            {"alerts": {"adsense-onboarding-incomplete": "WARNING"}},
            ["- Kalktı: ua-conflict-policy-update, WARNING (uyarı)"],
        ),
        (
            {"alerts": {**LIVE["alerts"], "payment-hold": "SEVERE"}},
            [
                "- Yeni: payment-hold, SEVERE (ciddi). Anlamı: Ayrıntısını AdSense panelinin "
                "ana sayfasındaki uyarılarda oku."
            ],
        ),
    ),
)
def test_each_compared_field_is_emailed_old_to_new(env_file, tmp_path, changes, expected):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())

    code, sender = _run(env_file, state_dir, ok(**changes), now=NOW + DAY)

    assert code == 0
    assert sender.subjects() == [CHANGED]
    text = sender.emails()[0][1]
    lines = text.splitlines()
    assert lines[0] == "lecturesift.com için AdSense durumu değişti."
    assert "Zaman: 2026-10-22 03:10:05 UTC (2026-10-22 06:10:05 Türkiye saati)" in lines
    assert "Önceki başarılı kontrol: 2026-10-21 03:10:05 UTC (2026-10-21 06:10:05 Türkiye saati)" in lines
    for line in expected:
        assert line in lines
    # Only the changed field is listed as a change.
    changes_part = text.split("Değişenler:", 1)[1].split("Şu anki durum:", 1)[0]
    headers = ("Site durumu:", "Hesap durumu:", "Uyarılar:", "Politika sorunu sayısı:", "Otomatik reklamlar:")
    assert sum(header in changes_part for header in headers) == 1
    assert "Yönetim paneli: https://lecturesift.com/admin.html" in lines
    assert _state(state_dir)["snapshot"] == status_alert.validate_snapshot({**LIVE, **changes})

    # The next check with the same status is quiet again.
    _code, again = _run(env_file, state_dir, ok(**changes), now=NOW + 2 * DAY)
    assert again.calls == []


def test_several_changes_are_listed_together_in_a_fixed_order(env_file, tmp_path):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())

    _code, sender = _run(
        env_file, state_dir, ok(site_state="READY", policy_issues=1, auto_ads=None), now=NOW + DAY
    )

    text = sender.emails()[0][1]
    site = text.index("Site durumu: NEEDS_ATTENTION")
    policy = text.index("Politika sorunu sayısı: 0 → 1")
    auto_ads = text.index("Otomatik reklamlar: Açık → Bilinmiyor")
    assert site < policy < auto_ads


@pytest.mark.parametrize("state", sorted(status_alert.SITE_STATES))
def test_every_site_state_has_a_plain_language_meaning(env_file, tmp_path, state):
    state_dir = tmp_path / "state"
    start = "READY" if state != "READY" else "GETTING_READY"
    _run(env_file, state_dir, ok(site_state=start))

    _code, sender = _run(env_file, state_dir, ok(site_state=state), now=NOW + DAY)

    label, meaning = status_alert.SITE_MEANINGS[state]
    lines = sender.emails()[0][1].splitlines()
    assert any(line.endswith(f"→ {state} ({label})") for line in lines)
    assert f"Anlamı: {meaning}" in lines


def test_meanings_cover_every_value_the_reader_can_return():
    assert set(status_alert.SITE_MEANINGS) == status_alert.SITE_STATES == adsense_management._SITE_STATES
    assert (
        set(status_alert.ACCOUNT_MEANINGS)
        == status_alert.ACCOUNT_STATES
        == adsense_management._ACCOUNT_STATES
    )
    assert set(status_alert.SEVERITY_LABELS) == status_alert.SEVERITIES == adsense_management._ALERT_SEVERITIES
    assert set(status_alert.AUTO_ADS_MEANINGS) == {True, False, None}
    assert status_alert.ALERT_TYPE.pattern == adsense_management._ALERT_TYPE.pattern
    source = _read("lecturesift/adsense_management.py")
    reader_codes = set(re.findall(r'_AdSenseError\(\s*"([a-z_]+)"', source))
    reader_codes |= set(re.findall(r'not_found_code="([a-z_]+)"', source))
    assert reader_codes and reader_codes <= set(status_alert.READ_ERRORS)
    assert "not_configured" in status_alert.READ_ERRORS
    assert set(status_alert.READ_PARTS) == {"account", "alerts", "policy", "api"}


def test_three_failed_checks_in_a_row_send_one_unreadable_email(env_file, tmp_path, capsys):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())
    day = [0]

    def check(reading):
        day[0] += 1
        return _run(env_file, state_dir, reading, now=NOW + day[0] * DAY)

    for expected_failures in (1, 2):
        code, sender = check(failed("policy", "provider_unavailable"))
        assert code == 0 and sender.calls == []
        assert _state(state_dir)["consecutive_failures"] == expected_failures
        assert _state(state_dir)["snapshot"] == LIVE

    code, sender = check(failed("policy", "provider_unavailable"))
    assert code == 0
    assert sender.subjects() == [UNREADABLE]
    lines = sender.emails()[0][1].splitlines()
    assert lines[0] == (
        "lecturesift.com için AdSense durumu art arda 3 kontrolde okunamadı "
        "(her gün bir kontrol yapılır)."
    )
    assert "Okunamayan: politika sorunları" in lines
    assert "Sebep: Google AdSense API zamanında yanıt vermedi. (provider_unavailable)" in lines
    assert "Son başarılı kontrol: 2026-10-21 03:10:05 UTC (2026-10-21 06:10:05 Türkiye saati)" in lines
    assert "- Sunucuda kontrolün kaydını oku: journalctl -u lecturesift-adsense-status.service -n 50" in lines
    assert _state(state_dir)["unreadable_email_sent"] is True

    # Further failures stay quiet, whatever their reason.
    for reading in (failed("account", "authentication_failed"), failed("api", "api_unreachable")):
        code, sender = check(reading)
        assert code == 0 and sender.calls == []
    assert _state(state_dir)["consecutive_failures"] == 5

    # A success resets the count; with nothing changed it sends nothing.
    code, sender = check(ok())
    assert code == 0 and sender.calls == []
    assert _state(state_dir)["consecutive_failures"] == 0
    assert _state(state_dir)["unreadable_email_sent"] is False

    # Only a new run of three failures sends the email again.
    for _ in range(2):
        assert check(failed())[1].calls == []
    assert check(failed())[1].subjects() == [UNREADABLE]
    assert "@example" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "reading",
    (
        failed("account", "authentication_failed"),
        failed("alerts", "rate_limited"),
        failed("policy", "provider_unavailable"),
        failed("api", "timeout"),
        failed("api", "invalid_snapshot"),
    ),
)
def test_a_failed_check_never_emails_a_change_or_touches_the_snapshot(env_file, tmp_path, reading):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())

    code, sender = _run(env_file, state_dir, reading, now=NOW + DAY)

    assert code == 0 and sender.calls == []
    saved = _state(state_dir)
    assert saved["snapshot"] == LIVE and saved["checked_at"] == "2026-10-21T03:10:05Z"
    # The status read after the failure is compared with the last good one.
    _code, sender = _run(env_file, state_dir, ok(), now=NOW + 2 * DAY)
    assert sender.calls == []


def test_a_partial_read_is_a_failure_even_when_the_site_state_differs(env_file, tmp_path):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())
    readiness = _readiness(site_state="READY")
    readiness.update(alerts=None, alerts_error_code="provider_unavailable")
    output = json.dumps(container.status_snapshot(readiness)).encode()

    code, sender = _run(env_file, state_dir, status_alert.parse_snapshot(output), now=NOW + DAY)

    assert code == 0 and sender.calls == []
    assert _state(state_dir)["snapshot"]["site_state"] == "NEEDS_ATTENTION"
    assert _state(state_dir)["consecutive_failures"] == 1


def test_failures_before_any_snapshot_still_count_and_the_first_success_is_the_baseline(env_file, tmp_path):
    state_dir = tmp_path / "state"
    for day in range(3):
        _code, sender = _run(env_file, state_dir, failed("account", "not_configured"), now=NOW + day * DAY)
    assert sender.subjects() == [UNREADABLE]
    lines = sender.emails()[0][1].splitlines()
    assert "Sebep: AdSense API bağlantısı sunucuda kapalı. (not_configured)" in lines
    assert "Son başarılı kontrol: henüz yok" in lines

    _code, sender = _run(env_file, state_dir, ok(), now=NOW + 3 * DAY)
    assert sender.subjects() == [BASELINE]


def test_a_change_email_that_reached_nobody_keeps_the_old_snapshot_and_is_retried(env_file, tmp_path, capsys):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())
    refusing = FakeSender(urllib.error.HTTPError("https://api.resend.com/emails", 503, "Busy", None, None))

    code, _sender = _run(env_file, state_dir, ok(site_state="READY"), now=NOW + DAY, sender=refusing)

    # A failed unit is reported by the failure alert's OnFailure= edge.
    assert code == 1
    assert len(refusing.calls) == 2
    assert _state(state_dir)["snapshot"] == LIVE
    logged = capsys.readouterr().err
    assert "AdSense status change email not sent: reached 0 of 2 recipient(s)" in logged
    assert "HTTPError (HTTP 503) x2" in logged
    assert "@example" not in logged and RESEND_KEY not in logged

    code, working = _run(env_file, state_dir, ok(site_state="READY"), now=NOW + 2 * DAY)
    assert code == 0
    assert working.subjects() == [CHANGED]
    assert "Önceki başarılı kontrol: 2026-10-21 03:10:05 UTC" in working.emails()[0][1]
    assert _state(state_dir)["snapshot"]["site_state"] == "READY"


def test_a_baseline_that_reached_nobody_is_sent_again_by_the_next_check(env_file, tmp_path):
    state_dir = tmp_path / "state"

    code, _sender = _run(env_file, state_dir, ok(), sender=FakeSender(TimeoutError("timed out")))

    assert code == 1
    assert _state(state_dir)["snapshot"] is None
    code, working = _run(env_file, state_dir, ok(), now=NOW + DAY)
    assert code == 0 and working.subjects() == [BASELINE]


def test_an_unreadable_email_that_reached_nobody_is_tried_again(env_file, tmp_path):
    state_dir = tmp_path / "state"
    for day in range(2):
        _run(env_file, state_dir, failed(), now=NOW + day * DAY)

    code, _sender = _run(env_file, state_dir, failed(), now=NOW + 2 * DAY, sender=FakeSender(OSError("down")))
    assert code == 1
    assert _state(state_dir)["unreadable_email_sent"] is False

    code, working = _run(env_file, state_dir, failed(), now=NOW + 3 * DAY)
    assert code == 0 and working.subjects() == [UNREADABLE]
    assert "art arda 4 kontrolde" in working.emails()[0][1]


def test_missing_recipients_fail_the_unit_without_losing_the_change(tmp_path, capsys):
    runtime = _private(tmp_path / "runtime.env", "EMAIL_PROVIDER=resend\nEMAIL_FROM=a@example.com\n")
    state_dir = tmp_path / "state"
    sender = FakeSender()

    code, _sender = _run(runtime, state_dir, ok(), sender=sender)

    assert code == 1 and sender.calls == []
    assert _state(state_dir)["snapshot"] is None
    assert "LECTURESIFT_OPS_ALERT_EMAIL" in capsys.readouterr().err


def _every_email(env_file: Path, tmp_path: Path) -> list[tuple[str, str]]:
    state_dir = tmp_path / "state"
    collected = FakeSender()
    _run(env_file, state_dir, ok(), sender=collected)
    changed = ok(
        site_state="READY",
        account_state="NEEDS_ATTENTION",
        alerts={"ua-conflict-policy-update": "SEVERE", "payment-hold": "INFO"},
        policy_issues=3,
        auto_ads=False,
    )
    _run(env_file, state_dir, changed, now=NOW + DAY, sender=collected)
    _run(env_file, state_dir, ok(), now=NOW + 2 * DAY, sender=collected, manual=True)
    for day in range(3, 6):
        _run(env_file, state_dir, failed("account", "authentication_failed"), now=NOW + day * DAY, sender=collected)
    emails = collected.emails()
    assert [subject for subject, _text in emails] == [BASELINE, CHANGED, TEST, UNREADABLE]
    return emails


SECRET_SHAPES = (
    re.compile(r"ya29\."),
    re.compile(r"apps\.googleusercontent\.com"),
    re.compile(r"(?:ca-)?pub-[0-9]"),
    re.compile(r"accounts/"),
    re.compile(r"Bearer|refresh_token|client_secret|access_token|api_key", re.IGNORECASE),
    re.compile(r"[A-Za-z0-9_-]{32,}"),
    re.compile(r"@"),
)
# A raw API payload or provider HTML would bring braces or tags along. A lone
# ">" is the panel path separator ("Siteler > lecturesift.com"); tags need "<".
RAW_PAYLOAD = re.compile(r"[{}\[\]<]")


def test_no_email_carries_a_secret_or_secret_looking_value(env_file, tmp_path):
    settings, _undecoded = status_alert.alert_mail.load_settings(env_file)

    for subject, text in _every_email(env_file, tmp_path):
        for value in settings.values():
            if len(value) >= 8:
                assert value not in subject and value not in text
        for shape in SECRET_SHAPES:
            assert not shape.search(subject), shape.pattern
            assert not shape.search(text), shape.pattern
        assert subject.startswith("[LectureSift] ")
        assert not RAW_PAYLOAD.search(text)
        assert not RAW_PAYLOAD.search(subject.removeprefix("[LectureSift] "))


def test_unexpected_container_fields_reach_neither_the_email_nor_the_state(env_file, tmp_path):
    state_dir = tmp_path / "state"
    output = container.status_snapshot(_readiness())
    output.update(
        refresh_token=REFRESH_TOKEN,
        client_id=CLIENT_ID,
        raw={"alerts": [{"message": "<a href='https://private.example'>detail</a>"}]},
    )

    reading = status_alert.parse_snapshot(json.dumps(output).encode())
    code, sender = _run(env_file, state_dir, reading)

    assert code == 0
    saved = (state_dir / "state.json").read_text(encoding="utf-8")
    assert set(json.loads(saved)) == STATE_KEYS
    for leaked in (REFRESH_TOKEN, CLIENT_ID, "private.example", "message"):
        assert leaked not in saved
        assert all(leaked not in text for _subject, text in sender.emails())


def test_the_manual_test_emails_the_current_status_and_leaves_the_state_as_it_was(env_file, tmp_path, capsys):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())
    for day in (1, 2):
        _run(env_file, state_dir, failed(), now=NOW + day * DAY)
    before = (state_dir / "state.json").read_bytes()

    code, sender = _run(env_file, state_dir, ok(site_state="READY"), now=NOW + 3 * DAY, manual=True)

    assert code == 0
    assert sender.subjects() == [TEST]
    lines = sender.emails()[0][1].splitlines()
    assert lines[0].startswith("Bu e-posta, sunucuda elle çalıştırılan testle gönderildi.")
    assert lines[1].startswith("Kayıtlı durumdan farklı olanlar: site durumu.")
    assert "Site (lecturesift.com): READY (Hazır)" in lines
    assert (state_dir / "state.json").read_bytes() == before
    assert "the saved AdSense state was not changed" in capsys.readouterr().err

    # The daily check still reports the change, old to new, and the failure
    # count the test left alone is reset by that success.
    code, daily = _run(env_file, state_dir, ok(site_state="READY"), now=NOW + 4 * DAY)
    assert daily.subjects() == [CHANGED]
    assert "Site durumu: NEEDS_ATTENTION (İşlem gerekiyor) → READY (Hazır)" in daily.emails()[0][1]
    assert _state(state_dir)["consecutive_failures"] == 0


def test_the_manual_test_says_when_nothing_differs(env_file, tmp_path):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())

    _code, sender = _run(env_file, state_dir, ok(), now=NOW + DAY, manual=True)

    assert sender.emails()[0][1].splitlines()[1] == (
        "Kayıtlı durumla aynı; günlük kontrol bu durum için e-posta göndermeyecek."
    )


def test_a_manual_test_that_cannot_read_sends_and_changes_nothing(env_file, tmp_path, capsys):
    state_dir = tmp_path / "state"
    _run(env_file, state_dir, ok())
    before = (state_dir / "state.json").read_bytes()

    code, sender = _run(env_file, state_dir, failed("api", "api_unreachable"), now=NOW + DAY, manual=True)

    assert code == 1 and sender.calls == []
    assert (state_dir / "state.json").read_bytes() == before
    assert "could not be read (api: api_unreachable)" in capsys.readouterr().err

    fresh = tmp_path / "fresh"
    code, sender = _run(env_file, fresh, failed(), manual=True)
    assert code == 1 and sender.calls == []
    assert not (fresh / "state.json").exists()


def test_the_first_manual_test_is_the_baseline_and_the_timer_sends_no_second_one(env_file, tmp_path):
    state_dir = tmp_path / "state"

    code, sender = _run(env_file, state_dir, ok(), manual=True)

    assert code == 0 and sender.subjects() == [BASELINE]
    assert _state(state_dir)["snapshot"] == LIVE
    _code, daily = _run(env_file, state_dir, ok(), now=NOW + DAY)
    assert daily.calls == []


@pytest.mark.skipif(status_alert.fcntl is None, reason="flock is Linux-only")
def test_a_check_that_finds_another_running_does_nothing(env_file, tmp_path):
    state_dir = tmp_path / "state"

    def unexpected():
        raise AssertionError("a second check must not read the status")

    with status_alert.exclusive(state_dir):
        scheduled = status_alert.run(
            env_file=env_file, state_dir=state_dir, now=NOW, reader=unexpected, sender=FakeSender()
        )
        manual = status_alert.run(
            env_file=env_file, state_dir=state_dir, manual_test=True, now=NOW,
            reader=unexpected, sender=FakeSender(),
        )
    assert (scheduled, manual) == (0, 1)
    assert not (state_dir / "state.json").exists()


@pytest.mark.parametrize(
    "content",
    (
        b"not json",
        b'{"schema": "other"}',
        json.dumps({"schema": "lecturesift-adsense-status-state-v1", "snapshot": {"site_state": "?"}}).encode(),
    ),
)
def test_an_unusable_state_file_starts_a_new_baseline(env_file, tmp_path, content, capsys):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    (state_dir / "state.json").write_bytes(content)

    code, sender = _run(env_file, state_dir, ok())

    assert code == 0 and sender.subjects() == [BASELINE]
    assert _state(state_dir)["snapshot"] == LIVE


def _readiness(**site_changes) -> dict:
    """The admin readiness summary as lecturesift.adsense_management returns it."""

    alerts = adsense_management._alert_summary(
        [
            {"type": "adsense-onboarding-incomplete", "severity": "WARNING", "message": "<b>x</b>"},
            {"type": "ua-conflict-policy-update", "severity": "INFO"},
            {"type": "ua-conflict-policy-update", "severity": "WARNING"},
            {"type": "<script>dropped</script>", "severity": "SEVERE"},
        ]
    )
    return {
        "enabled": True,
        "configured": True,
        "connected": True,
        "status": "connected",
        "checked_at": "2026-10-20T15:58:00Z",
        "cached": False,
        "account": {"state": "READY", "pending_task_count": 2},
        "site": {
            "domain": "lecturesift.com",
            "state": site_changes.get("site_state", "NEEDS_ATTENTION"),
            "auto_ads_enabled": True,
        },
        "alerts": alerts,
        "policy_issues": {
            "total": 0,
            "warned": 0,
            "ad_serving_restricted": 0,
            "ad_serving_disabled": 0,
            "ad_personalization_restricted": 0,
        },
        "reports": {},
        "error_code": None,
    }


def test_the_reader_keeps_the_most_severe_level_for_each_alert_type():
    summary = adsense_management._alert_summary(
        [
            {"type": "a", "severity": "WARNING"},
            {"type": "a", "severity": "SEVERE"},
            {"type": "a", "severity": "INFO"},
            {"type": "b", "severity": "unknown-level"},
            {"type": "<bad>", "severity": "SEVERE"},
        ]
    )

    assert summary["types"] == ["a", "b"]
    assert summary["severities"] == {"a": "SEVERE", "b": "SEVERITY_UNSPECIFIED"}
    assert summary["total"] == 5 and summary["severe"] == 2


def test_the_container_summary_round_trips_to_the_live_status():
    output = container.status_snapshot(_readiness())

    assert output == {"schema": container.SCHEMA, "ok": True, **LIVE}
    assert container.SCHEMA == status_alert.SNAPSHOT_SCHEMA
    reading = status_alert.parse_snapshot(json.dumps(output).encode())
    assert reading.ok and reading.snapshot == LIVE


@pytest.mark.parametrize(
    ("readiness", "part", "error"),
    (
        ({"status": "not_configured", "connected": False, "error_code": None}, "account", "not_configured"),
        (
            {"status": "unavailable", "connected": False, "error_code": "authentication_failed"},
            "account",
            "authentication_failed",
        ),
        (
            {"status": "unavailable", "connected": False, "error_code": "Provider said: token=abc"},
            "account",
            "unknown",
        ),
        ({**_readiness(), "alerts": None, "alerts_error_code": "rate_limited"}, "alerts", "rate_limited"),
        (
            {**_readiness(), "policy_issues": None, "policy_error_code": "provider_unavailable"},
            "policy",
            "provider_unavailable",
        ),
        ({**_readiness(), "site": None}, "account", "invalid_response"),
        (None, "account", "unknown"),
    ),
)
def test_an_incomplete_read_is_reported_as_a_failure_with_a_fixed_code(readiness, part, error):
    output = container.status_snapshot(readiness)

    reading = status_alert.parse_snapshot(json.dumps(output).encode())
    assert reading.snapshot is None
    assert (reading.part, reading.error) == (part, error)


def test_the_container_module_prints_one_json_line_and_never_raises(monkeypatch, capsys):
    monkeypatch.setattr(container, "adsense_management_readiness", _readiness)
    assert container.main() == 0
    printed = capsys.readouterr().out
    assert printed.count("\n") == 1
    assert json.loads(printed) == {"schema": container.SCHEMA, "ok": True, **LIVE}

    def explode():
        raise RuntimeError(f"refresh_token={REFRESH_TOKEN}")

    monkeypatch.setattr(container, "adsense_management_readiness", explode)
    assert container.main() == 0
    printed = capsys.readouterr().out
    assert json.loads(printed) == {"schema": container.SCHEMA, "ok": False, "part": "account", "error": "unknown"}
    assert REFRESH_TOKEN not in printed


@pytest.mark.parametrize(
    "output",
    (
        b"",
        b"not json",
        b"[]",
        json.dumps({"ok": True, **LIVE}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", **LIVE}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "site_state": "APPROVED"}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "account_state": None}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "auto_ads": "yes"}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "auto_ads": 0}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "policy_issues": -1}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "policy_issues": True}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "policy_issues": 10001}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "alerts": {"<b>": "INFO"}}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "alerts": {"a": "LOUD"}}}).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "alerts": ["a"]}}).encode(),
        json.dumps(
            {"schema": "lecturesift-adsense-status-v1", "ok": True, **{**LIVE, "alerts": {f"a{n}": "INFO" for n in range(26)}}}
        ).encode(),
        json.dumps({"schema": "lecturesift-adsense-status-v1", "ok": True, "site_state": "READY"}).encode(),
        b" " * (status_alert.MAX_OUTPUT_BYTES + 1),
    ),
)
def test_container_output_that_does_not_validate_is_an_unreadable_check(output):
    reading = status_alert.parse_snapshot(output)

    assert reading.snapshot is None
    assert (reading.part, reading.error) == ("api", "invalid_snapshot")


def test_the_status_is_read_by_one_bounded_exec_in_the_running_api_container(monkeypatch, capsys):
    calls = []
    output = json.dumps({"schema": container.SCHEMA, "ok": True, **LIVE}).encode()

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"container noise")

    monkeypatch.setattr(status_alert.subprocess, "run", fake_run)
    reading = status_alert.read_status()

    assert reading.snapshot == LIVE
    command, kwargs = calls[0]
    assert len(calls) == 1
    assert list(command) == [
        "/usr/bin/docker", "compose",
        "--project-directory", "/opt/lecturesift",
        "--file", "/opt/lecturesift/compose.yaml",
        "exec", "-T", "api",
        "python", "-m", "lecturesift.adsense_status_snapshot",
    ]
    assert kwargs["timeout"] == status_alert.READ_SECONDS
    assert kwargs["check"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL
    # Nothing from the caller's environment, and no credential, reaches Docker.
    assert kwargs["env"] == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C.UTF-8"}
    assert (ROOT / "lecturesift" / "adsense_status_snapshot.py").is_file()
    assert "container noise" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("outcome", "error"),
    (
        (subprocess.TimeoutExpired("docker", 90), "timeout"),
        (FileNotFoundError("docker"), "api_unreachable"),
        (subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"service \"api\" is not running"), "api_unreachable"),
    ),
)
def test_an_unreachable_api_container_is_an_unreadable_check(monkeypatch, capsys, outcome, error):
    def fake_run(command, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(status_alert.subprocess, "run", fake_run)

    reading = status_alert.read_status()

    assert (reading.snapshot, reading.part, reading.error) == (None, "api", error)
    assert "is not running" not in capsys.readouterr().err


def test_main_refuses_unknown_arguments_and_never_logs_an_unexpected_value(monkeypatch, capsys):
    assert status_alert.main(["--force"]) == 2

    def explode(**kwargs):
        raise ValueError(REFRESH_TOKEN)

    monkeypatch.setattr(status_alert, "run", explode)
    assert status_alert.main([]) == 1
    logged = capsys.readouterr().err
    assert "unexpected ValueError" in logged and REFRESH_TOKEN not in logged


def test_mail_goes_through_the_failure_alert_sender():
    mail = status_alert.alert_mail
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert Path(mail.__file__).resolve() == (ROOT / "deploy" / "service_failure_alert.py").resolve()
    assert "lecturesift-adsense-status.service" in mail.WATCHED_UNITS
    # No mail path of its own.
    for own_path in ("smtplib", "urllib.request", "api.resend.com"):
        assert own_path not in source
    assert "alert_mail.deliver(" in source and "alert_mail.send_email" in source


def _unit_section(text: str) -> str:
    return text.split("[Service]", 1)[0]


def test_the_service_is_a_bounded_oneshot_that_never_starts_the_core_or_reads_role_files():
    service = _read("deploy/lecturesift-adsense-status.service")

    assert "Type=oneshot" in service
    assert "ExecStart=/usr/bin/python3 -I /opt/lecturesift/deploy/adsense_status_alert.py\n" in service
    assert "Environment=LECTURESIFT_ENV_FILE=/etc/lecturesift/runtime.env" in service
    # The AdSense credentials stay in the api container; no role file is
    # loaded, generated or exported here.
    for absent in ("EnvironmentFile=", "api.env", "generate_role_envs", "ADSENSE"):
        assert absent not in service
    # Ordering only: a timer firing during a release must not pull the core up.
    assert "Requires=" not in service and "BindsTo=" not in service
    assert "After=docker.service lecturesift.service network-online.target" in service
    assert "OnFailure=lecturesift-failure-alert@%n.service\n" in _unit_section(service)
    assert f"StateDirectory={status_alert.DEFAULT_STATE_DIR.relative_to('/var/lib').as_posix()}" in service
    assert "StateDirectoryMode=0700" in service
    for hardening in ("UMask=0077", "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=full"):
        assert hardening in service
    timeout = int(re.search(r"^TimeoutStartSec=(\d+)$", service, re.MULTILINE).group(1))
    assert status_alert.READ_SECONDS + status_alert.alert_mail.DELIVERY_SECONDS + 30 <= timeout <= 600
    assert "[Install]" not in service and "Restart=" not in service


def test_the_timer_runs_daily_at_0310_utc_away_from_the_backup_and_instagram_slots():
    timer = _read("deploy/lecturesift-adsense-status.timer")
    backup = _read("deploy/lecturesift-backup.timer")

    assert "OnCalendar=*-*-* 03:10:00 UTC\n" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer
    delay = int(re.search(r"^RandomizedDelaySec=(\d+)$", timer, re.MULTILINE).group(1))
    assert delay <= 300
    # The backup starts by 02:25 UTC at the latest.
    assert "OnCalendar=*-*-* 02:15:00 UTC" in backup
    backup_delay = int(re.search(r"^RandomizedDelaySec=(\d+)$", backup, re.MULTILINE).group(1))
    assert 2 * 3600 + 15 * 60 + backup_delay < 3 * 3600 + 10 * 60
    # 06:10 in Turkey; the carousel (10:00 UTC) and Reel (17:00 UTC) slots are hours away.
    assert datetime(2026, 10, 21, 3, 10, tzinfo=timezone.utc).astimezone(
        status_alert.alert_mail.TURKEY_TIME
    ).strftime("%H:%M") == "06:10"


def test_installer_promotion_and_failure_alert_carry_the_new_units():
    def unit_list(path: str, name: str) -> list[str]:
        body = _read(path).split(f"{name}=(\n", 1)[1].split("\n)", 1)[0]
        return [line.strip() for line in body.splitlines()]

    installer = _read("deploy/install_systemd_units.sh")
    for units in (
        unit_list("deploy/install_systemd_units.sh", "units"),
        unit_list("deploy/promote_rehearsed_release.sh", "managed_units"),
    ):
        assert "lecturesift-adsense-status.service" in units
        assert "lecturesift-adsense-status.timer" in units
    # Like the other timers, it must be disabled while units are replaced.
    disabled_gate = installer.split("for unit in lecturesift.service lecturesift-ingress.service", 1)[1]
    disabled_gate = disabled_gate.split("do", 1)[0]
    assert "lecturesift-adsense-status.timer" in disabled_gate
    for path in ("deploy/install_systemd_units.sh", "deploy/promote_rehearsed_release.sh"):
        assert (
            "'lecturesift-failure-alert@lecturesift-adsense-status.service.service'"
            in _read(path)
        )


def test_the_runbook_documents_where_it_runs_and_the_one_command_test():
    runbook = _read("deploy/POST_CUTOVER_RELEASE.md")
    deployment = _read("VPS_DEPLOYMENT.md")

    section = runbook.split("## AdSense status email", 1)[1]
    command = "sudo python3 -I /opt/lecturesift/deploy/adsense_status_alert.py --manual-test\n"
    first = section.split("### First release with the AdSense status email", 1)[1]
    assert first.index(command) < first.index(
        "sudo systemctl enable --now lecturesift-adsense-status.timer"
    )
    assert "AdSense baseline email sent to N recipient(s)." in first
    again = section.split("### Testing again", 1)[1]
    assert f"```sh\n{command}```" in again
    assert "the saved state exactly as it was" in again
    for fact in (
        "docker compose exec -T api python -m lecturesift.adsense_status_snapshot",
        "03:10 UTC (06:10 in Turkey)",
        status_alert.DEFAULT_STATE_DIR.joinpath("state.json").as_posix(),
        "No new environment variable or file is needed",
        "journalctl -u lecturesift-adsense-status.service -n 20",
        "https://lecturesift.com/admin.html",
    ):
        assert fact in section
    # Step 1 pauses it and waits for a running check before units are replaced.
    step1 = runbook.split("## 1. Pause scheduled jobs", 1)[1].split("## 2.", 1)[0]
    assert "sudo systemctl disable --now lecturesift-adsense-status.timer" in step1
    assert "systemctl is-active lecturesift-adsense-status.service" in step1
    assert "lecturesift-adsense-status.timer" in runbook.split("## 0. Before you start", 1)[1].split("## 1.", 1)[0]
    assert "lecturesift-adsense-status.timer" in deployment
