"""Package-expiry reminders: selection, idempotency, copy and operator commands."""

import asyncio
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import uuid

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.pool import StaticPool

from lecturesift import (
    assistant_maintenance,
    billing_service as billing,
    config,
    plan_copy,
    renewal_reminders as reminders,
    rollout_service,
)
from lecturesift.billing import REGIONAL_PRICES
from lecturesift.renewal_reminder_templates import (
    KINDS,
    SUPPORTED_LANGUAGES,
    _COPY,
    render_renewal_reminder_email,
    renewal_url,
)


# 07:00 UTC is 10:00 in Türkiye: inside the daytime send window.
NOW = datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)
THREE_DAYS = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
TODAY = datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc)
EVENTS = rollout_service.ADMIN_ACCOUNT_EVENTS


@pytest.fixture
def state(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout_service, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(billing, "utcnow", lambda: NOW)
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(config, "FRONTEND_BASE_URL", "https://lecturesift.com")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "billing@example.com")
    monkeypatch.setattr(config, "current_maintenance_mode", lambda: "off")
    monkeypatch.delenv("LECTURESIFT_WORKER", raising=False)
    monkeypatch.setattr(reminders, "email_delivery_configured", lambda: True)
    sent = []
    monkeypatch.setattr(reminders, "send_transactional_email",
                        lambda *args, **kwargs: sent.append((args, kwargs)) or "synthetic-mail")
    yield sent
    engine.dispose()


def _user(*, language="tr", verified=True, email=None, first_name="Ayşe", credit_minutes=0) -> str:
    user_id = str(uuid.uuid4())
    with billing.ENGINE.begin() as connection:
        connection.execute(billing.USERS.insert().values(
            id=user_id, email=email or f"{user_id}@example.com", password_salt="s", password_hash="h",
            credit_minutes=credit_minutes, created_at=NOW - timedelta(days=60)))
        connection.execute(billing.USER_PROFILES.insert().values(
            user_id=user_id, first_name=first_name, last_name="Customer", country_code="TR",
            email_verified_at=NOW - timedelta(days=60) if verified else None, session_version=1,
            created_at=NOW - timedelta(days=60), updated_at=NOW - timedelta(days=60)))
        connection.execute(billing.USER_PREFERENCES.insert().values(
            user_id=user_id, preferred_language=language, updated_at=NOW))
    return user_id


def _subscription(user_id, *, ends_at, plan_code="plus", interval="monthly", status="active",
                  reference=None, starts_at=None) -> str:
    subscription_id = str(uuid.uuid4())
    days = 365 if interval == "annual" else 30
    with billing.ENGINE.begin() as connection:
        connection.execute(billing.SUBSCRIPTIONS.insert().values(
            id=subscription_id, user_id=user_id, plan_code=plan_code, interval=interval, status=status,
            starts_at=starts_at or ends_at - timedelta(days=days), ends_at=ends_at,
            source_reference=reference or f"LS{uuid.uuid4().hex[:20].upper()}", created_at=NOW))
    return subscription_id


def _events():
    with billing.ENGINE.connect() as connection:
        return {row.id: row for row in connection.execute(select(EVENTS)).all()}


def _run(now=NOW, **kwargs):
    return reminders.run_once(now, **kwargs)


def test_three_day_and_expiry_day_reminders_are_sent_once_per_period(state):
    soon = _subscription(_user(), ends_at=THREE_DAYS)
    today = _subscription(_user(language="en"), ends_at=TODAY)
    _subscription(_user(), ends_at=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc))
    _subscription(_user(), ends_at=datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc))

    first = _run()
    assert first["kinds"]["3_days"]["sent"] == 1 and first["kinds"]["expiry"]["sent"] == 1
    assert first["kinds"]["3_days"]["ids"] == {"sent": [soon]}
    assert first["kinds"]["expiry"]["ids"] == {"sent": [today]}
    second = _run(NOW + timedelta(hours=5))
    assert second["kinds"]["3_days"] == {**second["kinds"]["3_days"], "sent": 0, "duplicate": 1}
    assert second["kinds"]["expiry"] == {**second["kinds"]["expiry"], "sent": 0, "duplicate": 1}
    assert len(state) == 2

    (args, kwargs), (today_args, today_kwargs) = state
    assert args[1] == "LectureSift paketin 3 gün sonra bitiyor"
    assert today_args[1] == "Your LectureSift package ends today"
    assert kwargs["idempotency_key"] == reminders.idempotency_key(soon, "3_days")
    assert today_kwargs["idempotency_key"] == reminders.idempotency_key(today, "expiry")
    assert kwargs["idempotency_key"] != today_kwargs["idempotency_key"]
    assert kwargs["reply_to"] == "billing@example.com"
    events = _events()
    assert events[reminders.event_id(soon, "3_days")].action == "renewal_reminder_3_days_sent"
    assert events[reminders.event_id(today, "expiry")].action == "renewal_reminder_expiry_sent"
    assert soon in events[reminders.event_id(soon, "3_days")].summary
    assert events[reminders.event_id(soon, "3_days")].actor == "system"


@pytest.mark.parametrize("ends_at, kind, expected", [
    # 00:30 on 23 October in Türkiye is still 22 October in UTC.
    (datetime(2026, 10, 22, 21, 30, tzinfo=timezone.utc), "3_days", True),
    # 23:59 on 23 October in Türkiye.
    (datetime(2026, 10, 23, 20, 59, tzinfo=timezone.utc), "3_days", True),
    # Midnight that starts 24 October in Türkiye belongs to the next day.
    (datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc), "3_days", False),
    # Midnight that started today in Türkiye: the period ended early today.
    (datetime(2026, 10, 19, 21, 0, tzinfo=timezone.utc), "expiry", True),
    # 23:30 yesterday in Türkiye.
    (datetime(2026, 10, 19, 20, 30, tzinfo=timezone.utc), "expiry", False),
    (datetime(2026, 10, 20, 20, 59, tzinfo=timezone.utc), "expiry", True),
])
def test_calendar_days_follow_europe_istanbul(state, ends_at, kind, expected):
    _subscription(_user(), ends_at=ends_at)
    summary = _run()
    assert summary["kinds"][kind]["sent"] == (1 if expected else 0)
    assert len(state) == (1 if expected else 0)


def test_already_sent_replaced_and_superseded_periods_are_skipped(state):
    renewed = _user()
    old = _subscription(renewed, ends_at=TODAY)
    _subscription(renewed, ends_at=TODAY + timedelta(days=30),
                  starts_at=NOW - timedelta(hours=1))
    _subscription(_user(), ends_at=THREE_DAYS, status="replaced")
    _subscription(_user(), ends_at=THREE_DAYS, status="cancelled")
    cancelled_at_end = _subscription(_user(), ends_at=THREE_DAYS, status="cancel_at_end")
    done = _subscription(_user(), ends_at=THREE_DAYS)
    with billing.ENGINE.begin() as connection:
        connection.execute(EVENTS.insert().values(
            id=reminders.event_id(done, "3_days"), subject_user_id="x", subject_email="x@example.com",
            action="renewal_reminder_3_days_sent", summary="earlier run", actor="system",
            created_at=NOW - timedelta(hours=2)))

    summary = _run()
    assert summary["kinds"]["expiry"]["due"] == 0
    assert summary["kinds"]["3_days"] == {**summary["kinds"]["3_days"], "due": 2, "sent": 1, "duplicate": 1}
    assert summary["kinds"]["3_days"]["ids"] == {"sent": [cancelled_at_end]}
    assert reminders.event_id(old, "expiry") not in _events()
    assert len(state) == 1


def test_free_guest_and_credit_pack_accounts_get_no_reminder(state):
    _user(credit_minutes=180)
    _subscription(_user(), ends_at=THREE_DAYS, plan_code="guest", interval="trial")
    _subscription(_user(), ends_at=THREE_DAYS, plan_code="credit", interval="one_time")
    assert reminders.subscription_plan_codes() == {"lite", "plus", "pro", "max"}
    summary = _run()
    assert summary["kinds"]["3_days"]["due"] == 0 and summary["kinds"]["expiry"]["due"] == 0
    assert state == [] and _events() == {}


def test_unverified_erased_and_guest_addresses_are_ineligible_and_not_recorded(state):
    _subscription(_user(verified=False), ends_at=THREE_DAYS)
    _subscription(_user(email=f"deleted+{uuid.uuid4().hex}@users.invalid"), ends_at=THREE_DAYS)
    _subscription(_user(email=f"{uuid.uuid4().hex}@guest.lecturesift.invalid"), ends_at=THREE_DAYS)
    summary = _run()
    assert summary["kinds"]["3_days"] == {**summary["kinds"]["3_days"], "due": 3, "ineligible": 3, "sent": 0}
    assert state == [] and _events() == {}


@pytest.mark.parametrize("hour_utc, expected", [(5, "outside_send_window"), (19, "outside_send_window"), (6, None), (18, None)])
def test_reminders_go_out_only_in_the_turkish_daytime(state, hour_utc, expected):
    _subscription(_user(), ends_at=THREE_DAYS)
    summary = _run(NOW.replace(hour=hour_utc))
    assert summary.get("skipped") == expected
    assert len(state) == (0 if expected else 1)


def test_worker_processes_maintenance_and_unconfigured_email_send_nothing(state, monkeypatch):
    _subscription(_user(), ends_at=THREE_DAYS)
    monkeypatch.setenv("LECTURESIFT_WORKER", "1")
    assert _run()["skipped"] == "maintenance"
    monkeypatch.delenv("LECTURESIFT_WORKER")
    monkeypatch.setattr(config, "current_maintenance_mode", lambda: "drain")
    assert _run()["skipped"] == "maintenance"
    monkeypatch.setattr(config, "current_maintenance_mode", lambda: "off")
    monkeypatch.setattr(reminders, "email_delivery_configured", lambda: False)
    assert _run()["skipped"] == "unconfigured"
    assert state == [] and _events() == {}


def test_failed_resend_delivery_is_retried_with_the_same_key_within_the_day(state, monkeypatch):
    subscription = _subscription(_user(), ends_at=THREE_DAYS)
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(kwargs["idempotency_key"])
        if len(attempts) == 1:
            raise RuntimeError("provider text with customer@example.com")
        return "synthetic-mail"

    monkeypatch.setattr(reminders, "send_transactional_email", flaky)
    first = _run()
    assert first["kinds"]["3_days"]["retry"] == 1
    assert _events()[reminders.event_id(subscription, "3_days")].action == "renewal_reminder_3_days_retry"
    second = _run(NOW + timedelta(minutes=15))
    assert second["kinds"]["3_days"]["sent"] == 1
    assert attempts == [reminders.idempotency_key(subscription, "3_days")] * 2
    assert _events()[reminders.event_id(subscription, "3_days")].action == "renewal_reminder_3_days_sent"
    assert _run(NOW + timedelta(minutes=30))["kinds"]["3_days"]["duplicate"] == 1
    assert len(attempts) == 2


def test_ambiguous_smtp_failure_is_recorded_and_never_replayed(state, monkeypatch, caplog):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "smtp")
    subscription = _subscription(_user(), ends_at=THREE_DAYS)

    def broken(*args, **kwargs):
        raise RuntimeError("smtp: customer@example.com rejected")

    monkeypatch.setattr(reminders, "send_transactional_email", broken)
    assert _run()["kinds"]["3_days"]["unknown"] == 1
    assert _events()[reminders.event_id(subscription, "3_days")].action == "renewal_reminder_3_days_unknown"
    monkeypatch.setattr(reminders, "send_transactional_email", lambda *a, **k: state.append((a, k)))
    assert _run(NOW + timedelta(minutes=15))["kinds"]["3_days"]["duplicate"] == 1
    assert state == []
    assert "customer@example.com" not in caplog.text


def test_a_claim_left_by_a_crashed_sender_is_recovered_only_when_stale(state):
    fresh = _subscription(_user(), ends_at=THREE_DAYS)
    stale = _subscription(_user(), ends_at=THREE_DAYS)
    with billing.ENGINE.begin() as connection:
        for subscription, age in ((fresh, timedelta(minutes=2)), (stale, timedelta(minutes=30))):
            connection.execute(EVENTS.insert().values(
                id=reminders.event_id(subscription, "3_days"), subject_user_id="x", subject_email="x@example.com",
                action="renewal_reminder_3_days_pending", summary="claimed", actor="system",
                created_at=NOW - age))
    summary = _run()
    assert summary["kinds"]["3_days"]["ids"] == {"sent": [stale]}
    assert summary["kinds"]["3_days"]["duplicate"] == 1
    assert len(state) == 1
    assert _events()[reminders.event_id(fresh, "3_days")].action == "renewal_reminder_3_days_pending"


def test_email_reads_plan_name_price_and_language_at_send_time(state, monkeypatch):
    monkeypatch.setattr(plan_copy, "_snapshot", lambda: {"lite": ["Öğrenci", "Student"] + ["Student"] * 11})
    monkeypatch.setitem(REGIONAL_PRICES["lite"], "TRY", 14900)
    turkish = _user()
    reference = f"LS{uuid.uuid4().hex[:16].upper()}"
    _subscription(turkish, ends_at=THREE_DAYS, plan_code="lite", reference=reference)
    with billing.ENGINE.begin() as connection:
        connection.execute(billing.USAGE_EVENTS.insert().values(
            id=str(uuid.uuid4()), user_id=turkish, job_id="job-1", plan_code="lite", minutes=100,
            occurred_at=NOW - timedelta(days=2)))
    english = _user(language="en", first_name="Jane")
    order = f"LS{uuid.uuid4().hex[:16].upper()}"
    with billing.ENGINE.begin() as connection:
        connection.execute(billing.PAYMENT_ORDERS.insert().values(
            reference=order, user_id=english, provider="paytr", plan_code="pro", interval="annual",
            amount_minor=30990, currency="EUR", status="paid", created_at=NOW, updated_at=NOW))
    _subscription(english, ends_at=TODAY, plan_code="pro", interval="annual", reference=order,
                  starts_at=TODAY - timedelta(days=365))

    _run()
    (lite_args, _), (pro_args, _) = state
    lite_text, pro_text = lite_args[3], pro_args[3]
    assert lite_args[1] == "LectureSift paketin 3 gün sonra bitiyor"
    assert "Öğrenci paketinin dönemi 3 gün sonra, 2026-10-23 15:00 (UTC+03:00) tarihinde sona eriyor." in lite_text
    assert "Paket: Öğrenci · 30 günlük dönem" in lite_text
    # Legacy terms resolve to the frozen legacy lite allowance of 600 minutes.
    assert "Bu dönemde kalan dakika: 500 dakika" in lite_text
    assert "Yeniden satın alma fiyatı: 149,00 TRY" in lite_text
    assert "Hiçbir ödeme otomatik olarak alınmaz" in lite_text
    assert "Paketini yenile: https://lecturesift.com/plans.html?plan=lite&interval=monthly" in lite_text
    assert "Merhaba Ayşe," in lite_text
    assert pro_args[1] == "Your LectureSift package ends today"
    assert "Hello Jane," in pro_text
    assert "Package: Pro · annual period" in pro_text
    assert f"Price to buy again: {REGIONAL_PRICES['pro']['EUR'] * 10 // 100}.{REGIONAL_PRICES['pro']['EUR'] * 10 % 100:02d} EUR" in pro_text
    assert "Renew your package: https://lecturesift.com/en/plans.html?plan=pro&interval=annual" in pro_text
    assert "Nothing is charged automatically" in pro_text


def test_annual_package_that_ended_earlier_today_reports_its_final_period(state):
    # Started 06:00 Türkiye on 2025-10-20; the 365-day period ended at 06:00
    # today, before the 10:00 run. The last allowance window ran from
    # 2026-09-20, so only usage inside it counts.
    user = _user(language="en")
    starts_at = datetime(2025, 10, 20, 3, 0, tzinfo=timezone.utc)
    _subscription(user, ends_at=starts_at + timedelta(days=365), plan_code="pro", interval="annual",
                  starts_at=starts_at)
    with billing.ENGINE.begin() as connection:
        for job, minutes, occurred_at in (("final", 1200, datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)),
                                          ("earlier", 500, datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc))):
            connection.execute(billing.USAGE_EVENTS.insert().values(
                id=str(uuid.uuid4()), user_id=user, job_id=job, plan_code="pro", minutes=minutes,
                occurred_at=occurred_at))
    assert _run()["kinds"]["expiry"]["sent"] == 1
    (args, _), = state
    # Legacy terms resolve to the frozen legacy Pro allowance of 5000 minutes a month.
    assert "Minutes left in this period: 3800 minutes" in args[3]
    assert "Package: Pro · annual period" in args[3]


def test_a_package_replaced_between_selection_and_claim_is_not_emailed(state, monkeypatch):
    renewing = _user()
    old = _subscription(renewing, ends_at=THREE_DAYS)
    cancelling = _user()
    cancelled = _subscription(cancelling, ends_at=THREE_DAYS)
    untouched = _subscription(_user(), ends_at=THREE_DAYS)
    prepare = reminders._prepare

    def buy_between_selection_and_claim(subscription, kind, now):
        prepared = prepare(subscription, kind, now)
        with billing.ENGINE.begin() as connection:
            if subscription.id == old:
                # The purchase path marks the period replaced and adds the new one.
                connection.execute(update(billing.SUBSCRIPTIONS).where(
                    billing.SUBSCRIPTIONS.c.id == old).values(status="replaced"))
                connection.execute(billing.SUBSCRIPTIONS.insert().values(
                    id=str(uuid.uuid4()), user_id=renewing, plan_code="plus", interval="monthly",
                    status="active", starts_at=now, ends_at=now + timedelta(days=30),
                    source_reference=f"LS{uuid.uuid4().hex[:20].upper()}", created_at=now))
            if subscription.id == cancelled:
                connection.execute(update(billing.SUBSCRIPTIONS).where(
                    billing.SUBSCRIPTIONS.c.id == cancelled).values(status="cancelled"))
        return prepared

    monkeypatch.setattr(reminders, "_prepare", buy_between_selection_and_claim)
    summary = _run()
    assert summary["kinds"]["3_days"] == {**summary["kinds"]["3_days"], "due": 3, "superseded": 2, "sent": 1}
    assert summary["kinds"]["3_days"]["ids"] == {"superseded": [old, cancelled], "sent": [untouched]} or \
        summary["kinds"]["3_days"]["ids"] == {"superseded": [cancelled, old], "sent": [untouched]}
    assert len(state) == 1
    events = _events()
    assert reminders.event_id(old, "3_days") not in events
    assert reminders.event_id(cancelled, "3_days") not in events
    assert reminders.event_id(untouched, "3_days") in events


def test_a_retry_reclaim_also_rechecks_that_the_period_is_still_due(state, monkeypatch):
    subscription = _subscription(_user(), ends_at=THREE_DAYS)
    with billing.ENGINE.begin() as connection:
        connection.execute(EVENTS.insert().values(
            id=reminders.event_id(subscription, "3_days"), subject_user_id="x", subject_email="x@example.com",
            action="renewal_reminder_3_days_retry", summary="failed earlier", actor="system",
            created_at=NOW - timedelta(minutes=15)))
    prepare = reminders._prepare

    def replace_after_prepare(row, kind, now):
        prepared = prepare(row, kind, now)
        with billing.ENGINE.begin() as connection:
            connection.execute(update(billing.SUBSCRIPTIONS).where(
                billing.SUBSCRIPTIONS.c.id == subscription).values(status="replaced"))
        return prepared

    monkeypatch.setattr(reminders, "_prepare", replace_after_prepare)
    assert _run()["kinds"]["3_days"]["superseded"] == 1
    assert state == []
    assert _events()[reminders.event_id(subscription, "3_days")].action == "renewal_reminder_3_days_retry"


def test_dry_run_reports_without_sending_or_recording(state, capsys):
    pending = _subscription(_user(), ends_at=THREE_DAYS)
    done = _subscription(_user(), ends_at=TODAY)
    with billing.ENGINE.begin() as connection:
        connection.execute(EVENTS.insert().values(
            id=reminders.event_id(done, "expiry"), subject_user_id="x", subject_email="x@example.com",
            action="renewal_reminder_expiry_sent", summary="earlier run", actor="system", created_at=NOW))
    summary = _run(NOW.replace(hour=2), dry_run=True)
    assert summary["dry_run"] is True and "skipped" not in summary
    assert summary["kinds"]["3_days"]["would_send"] == 1 and summary["kinds"]["3_days"]["ids"] == {"would_send": [pending]}
    assert summary["kinds"]["expiry"]["duplicate"] == 1 and summary["kinds"]["expiry"]["ids"] == {}
    assert state == [] and len(_events()) == 1
    assert reminders.main(["--dry-run"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["dry_run"] is True and printed["kinds"]["expiry"]["duplicate"] == 1


def test_operator_preview_and_test_send_use_a_synthetic_package_only(state, capsys, monkeypatch):
    assert reminders.main(["--preview", "--language", "en", "--kind", "expiry"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Your LectureSift package ends today\n")
    assert "https://lecturesift.com/en/plans.html?plan=plus&interval=monthly" in out
    assert reminders.main(["--test-to", "ops@example.com"]) == 0
    assert capsys.readouterr().out.strip() == "Sample renewal reminder accepted by the email provider."
    (args, kwargs), = state
    assert args[0] == "ops@example.com" and args[1] == "LectureSift paketin 3 gün sonra bitiyor"
    assert "idempotency_key" not in kwargs and kwargs["reply_to"] == "billing@example.com"
    assert _events() == {}
    assert reminders.main(["--test-to", "not-an-address"]) == 2
    monkeypatch.setattr(reminders, "email_delivery_configured", lambda: False)
    assert reminders.main(["--test-to", "ops@example.com"]) == 1
    assert len(state) == 1


def test_run_summary_and_logs_carry_counts_and_ids_only(state, caplog):
    email = f"{uuid.uuid4().hex}@example.com"
    subscription = _subscription(_user(email=email, first_name="Gizli"), ends_at=THREE_DAYS)
    with caplog.at_level("INFO", logger="lecturesift.renewal_reminders"):
        summary = _run()
    assert subscription in json.dumps(summary)
    for private in (email, "Gizli"):
        assert private not in json.dumps(summary)
        assert private not in caplog.text
    assert "due=1 sent=1" in caplog.text


def test_api_lifespan_runs_and_cancels_the_reminder_loop(monkeypatch):
    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def loop():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(reminders, "reminder_loop", loop)
        async with assistant_maintenance.lifespan(None):
            await started.wait()
            assert not stopped.is_set()
        assert stopped.is_set()

    asyncio.run(scenario())


def test_reminder_loop_survives_a_failing_run(monkeypatch, caplog):
    calls = []

    def failing_run():
        calls.append(True)
        raise RuntimeError("database text with customer@example.com")

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(reminders, "run_once", failing_run)
    monkeypatch.setattr(reminders.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(reminders.reminder_loop())
    assert calls == [True]
    assert "Renewal reminder run failed" in caplog.text and "customer@example.com" not in caplog.text


# --- templates -------------------------------------------------------------

ENDS_AT = datetime(2026, 10, 23, 11, 32, tzinfo=timezone.utc)


class _EmailHTML(HTMLParser):
    def __init__(self, markup: str) -> None:
        super().__init__()
        self.root: dict[str, str] = {}
        self.links: list[str] = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.root = {"lang": attributes.get("lang", ""), "dir": attributes.get("dir", "")}
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])


def test_every_language_has_the_same_copy_keys():
    keys = set(_COPY["tr"])
    assert set(SUPPORTED_LANGUAGES) == set(_COPY) == set(plan_copy.LANGUAGES)
    assert all(set(copy) == keys for copy in _COPY.values())
    assert all(value.strip() for copy in _COPY.values() for value in copy.values())
    for copy in _COPY.values():
        assert "{plan}" in copy["intro_3_days"] and "{date}" in copy["intro_3_days"]
        assert "{plan}" in copy["intro_expiry"] and "{date}" in copy["intro_expiry"]
        assert "{value}" in copy["minutes"] and "{name}" in copy["greeting"]


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("kind", KINDS)
def test_all_site_languages_render_facts_and_working_links(language, kind):
    mail = render_renewal_reminder_email(
        language, kind=kind, plan_code="plus", plan_name="Plus", interval="monthly", ends_at=ENDS_AT,
        remaining_minutes=712, price_minor=59900, currency="TRY", first_name="Ayşe",
    )
    parsed = _EmailHTML(mail["html"])
    prefix = "" if language == "tr" else f"/{language}"
    assert parsed.root == {"lang": language, "dir": "rtl" if language == "ar" else "ltr"}
    assert parsed.links == [
        f"https://lecturesift.com{prefix}/plans.html?plan=plus&interval=monthly",
        f"https://lecturesift.com{prefix}/contact",
    ]
    assert parsed.links[0] in mail["text"]
    for value in ("Plus", "712", "599", "TRY", "2026-10-23 14:32", "Ayşe"):
        assert value in mail["text"]
        assert value in mail["html"]
    assert mail["subject"] == _COPY[language][f"subject_{kind}"]
    assert not any(token in mail["text"] + mail["subject"] for token in ("{value}", "{plan}", "{date}", "{name}", "None"))
    if language != "en":
        assert "ends in 3 days" not in mail["text"]


def test_turkish_and_english_subjects_match_the_approved_wording():
    def subject(language, kind):
        return render_renewal_reminder_email(language, kind=kind, plan_code="lite", plan_name="Lite",
                                             interval="monthly", ends_at=ENDS_AT)["subject"]
    assert subject("tr", "3_days") == "LectureSift paketin 3 gün sonra bitiyor"
    assert subject("tr", "expiry") == "LectureSift paketin bugün bitiyor"
    assert subject("en", "3_days") == "Your LectureSift package ends in 3 days"
    assert subject("en", "expiry") == "Your LectureSift package ends today"
    assert subject("pt-BR", "expiry") == _COPY["pt"]["subject_expiry"]
    assert subject("unknown", "expiry") == _COPY["tr"]["subject_expiry"]


def test_optional_rows_annual_terms_and_escaping():
    minimal = render_renewal_reminder_email("en", kind="3_days", plan_code="max", plan_name="", interval="annual",
                                            ends_at=ENDS_AT)
    assert "Package: Max · annual period" in minimal["text"]
    assert "Minutes left" not in minimal["text"] and "Price to buy again" not in minimal["text"]
    assert "Hello," in minimal["text"]
    assert "plans.html?plan=max&interval=annual" in minimal["text"]
    yen = render_renewal_reminder_email("ja", kind="expiry", plan_code="plus", plan_name="Plus", interval="monthly",
                                        ends_at=ENDS_AT, price_minor=2001, currency="JPY")["text"]
    assert "2001 JPY" in yen
    hostile = render_renewal_reminder_email(
        "tr", kind="expiry", plan_code="plus", plan_name="<b>Plus</b>", interval="monthly", ends_at=ENDS_AT,
        first_name="<script>alert(1)</script>",
    )
    assert "<script>" not in hostile["html"] and "<b>Plus" not in hostile["html"]
    assert "&lt;script&gt;" in hostile["html"] and "&lt;b&gt;Plus&lt;/b&gt;" in hostile["html"]
    with pytest.raises(ValueError):
        render_renewal_reminder_email("tr", kind="weekly", plan_code="plus", plan_name="Plus", interval="monthly",
                                      ends_at=ENDS_AT)
    assert renewal_url("https://lecturesift.com/", "de", "pro", "one_time") == \
        "https://lecturesift.com/de/plans.html?plan=pro&interval=monthly"


def test_catalog_price_follows_the_purchase_currency_and_interval():
    assert reminders.catalog_price("plus", "monthly", "TRY") == (REGIONAL_PRICES["plus"]["TRY"], "TRY")
    assert reminders.catalog_price("plus", "annual", "EUR") == (REGIONAL_PRICES["plus"]["EUR"] * 10, "EUR")
    assert reminders.catalog_price("plus", "monthly", "XXX") == (REGIONAL_PRICES["plus"]["TRY"], "TRY")
    assert reminders.catalog_price("business", "monthly", "TRY") == (None, "TRY")


# --- plan names -------------------------------------------------------------

def test_plan_names_snapshot_matches_the_site_copy(capsys):
    assert plan_copy.main(["--check"]) == 0
    names = plan_copy.frontend_plan_names()
    assert {"lite", "plus", "pro", "max", "credit", "free"} <= set(names)
    assert all(len(values) == 13 for values in names.values())
    assert plan_copy.plan_name("plus", "tr") == names["plus"][0]
    assert plan_copy.plan_name("credit", "ja") == names["credit"][plan_copy.LANGUAGES.index("ja")]
    assert plan_copy.plan_name("credit", "pt-BR") == names["credit"][plan_copy.LANGUAGES.index("pt")]
    assert plan_copy.plan_name("credit", "xx") == names["credit"][0]
    assert plan_copy.plan_name("new_plan", "en") == "New Plan"


def test_plan_names_sync_detects_drift(tmp_path, monkeypatch, capsys):
    snapshot = tmp_path / "plan_copy.json"
    monkeypatch.setattr(plan_copy, "SNAPSHOT_PATH", snapshot)
    assert plan_copy.main(["--check"]) == 1
    assert "python -m lecturesift.plan_copy --sync" in capsys.readouterr().err
    assert plan_copy.main(["--sync"]) == 0
    assert plan_copy.main(["--check"]) == 0
    assert json.loads(snapshot.read_text(encoding="utf-8"))["plans"]["plus"] == plan_copy.frontend_plan_names()["plus"]
