"""Package-expiry reminder emails: three days before the end and on the last day.

Paid packages are fixed 30-day or annual periods that never renew by
themselves, so the customer hears nothing before access ends. The API
process runs this check every 15 minutes (see assistant_maintenance). Within
the Europe/Istanbul daytime window it selects the active periods that end in
three local days or today and sends one email per (period, kind).

Every delivery is recorded as one row with a deterministic id in the existing
admin account event log, exactly like order confirmations, so reruns,
restarts and a second API process can never email the same period twice and
no schema change is needed. Nothing here logs an address or a name: only
counts and subscription ids.

Operator commands (inside the api container)::

    python -m lecturesift.renewal_reminders --dry-run
    python -m lecturesift.renewal_reminders --preview --language tr --kind 3_days
    python -m lecturesift.renewal_reminders --test-to ops@example.com --language tr
    python -m lecturesift.renewal_reminders --send
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import sys
import uuid

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from . import billing_service as billing, config
from .billing import LEGACY_PLAN_BY_CODE, PLAN_BY_CODE, REGIONAL_PRICES
from .mailer import email_delivery_configured, send_transactional_email
from .plan_copy import plan_name
from .renewal_reminder_templates import KINDS, render_renewal_reminder_email

LOGGER = logging.getLogger(__name__)
# Türkiye has stayed on UTC+3 all year since 2016; a fixed offset needs no tzdata.
ISTANBUL = timezone(timedelta(hours=3))
# Reminders go out in the customer's daytime. A process that was down for the
# whole window skips that day's reminders rather than emailing at night.
SEND_FROM_HOUR, SEND_UNTIL_HOUR = 9, 22
DAYS_AHEAD = {"3_days": 3, "expiry": 0}
ACTIVE_STATUSES = ("active", "cancel_at_end")
PAID_INTERVALS = ("monthly", "annual")
LOOP_INTERVAL_SECONDS = 15 * 60
# A sender that died between claiming and recording the outcome leaves its
# claim pending. The mailer gives up after 15 s, so an older claim is stale.
STALE_CLAIM = timedelta(minutes=10)
EVENT_NAMESPACE = uuid.UUID("9b7f2c1e-6d43-4a8a-b1c7-2e5f0d8a3c64")
ACTOR = "system"
PENDING, SENT, RETRY, UNKNOWN = "pending", "sent", "retry", "unknown"
CLOSED_STATES = (SENT, UNKNOWN)
_KIND_LABELS = {"3_days": "3 gün önce", "expiry": "bitiş günü"}
_SUMMARIES = {
    PENDING: "Paket bitiş hatırlatması gönderiliyor",
    SENT: "Paket bitiş hatırlatması gönderildi",
    RETRY: "Paket bitiş hatırlatması gönderilemedi; aynı gün içinde yeniden denenecek",
    UNKNOWN: "Paket bitiş hatırlatmasının teslim durumu belirsiz; yeniden gönderilmeyecek",
}


def subscription_plan_codes() -> frozenset[str]:
    """Catalog codes sold as fixed-term packages; credit packs have no expiry."""
    return frozenset(
        plan.code for plan in (*PLAN_BY_CODE.values(), *LEGACY_PLAN_BY_CODE.values())
        if plan.kind == "subscription"
    )


def event_action(kind: str, state: str) -> str:
    return f"renewal_reminder_{kind}_{state}"


def event_id(subscription_id: str, kind: str) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, f"{subscription_id}/{kind}"))


def idempotency_key(subscription_id: str, kind: str) -> str:
    return "renewal-reminder/" + hashlib.sha256(f"{subscription_id}/{kind}".encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def local_day(now: datetime) -> date:
    return _utc(now).astimezone(ISTANBUL).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC half-open bounds of one Europe/Istanbul calendar day."""
    start = datetime(day.year, day.month, day.day, tzinfo=ISTANBUL)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def in_send_window(now: datetime) -> bool:
    return SEND_FROM_HOUR <= _utc(now).astimezone(ISTANBUL).hour < SEND_UNTIL_HOUR


def due_subscriptions(connection, kind: str, day: date):
    """Active paid periods ending on ``day`` whose owner bought nothing newer."""
    subscriptions = billing.SUBSCRIPTIONS
    start, end = day_bounds(day)
    newer = subscriptions.alias("newer")
    has_newer = select(newer.c.id).where(
        newer.c.user_id == subscriptions.c.user_id,
        newer.c.id != subscriptions.c.id,
        newer.c.status.in_(ACTIVE_STATUSES),
        newer.c.ends_at > subscriptions.c.ends_at,
    ).exists()
    return connection.execute(
        select(subscriptions).where(
            subscriptions.c.status.in_(ACTIVE_STATUSES),
            subscriptions.c.interval.in_(PAID_INTERVALS),
            subscriptions.c.plan_code.in_(sorted(subscription_plan_codes())),
            subscriptions.c.ends_at >= start,
            subscriptions.c.ends_at < end,
            ~has_newer,
        ).order_by(subscriptions.c.ends_at, subscriptions.c.id)
    ).all()


def _recipient(connection, user_id: str) -> dict | None:
    # Same eligibility as order confirmations: a verified profile is required
    # and reserved .invalid domains (erased, guest and synthetic accounts)
    # never receive mail.
    row = connection.execute(select(
        billing.USERS.c.email,
        billing.USER_PROFILES.c.first_name,
        billing.USER_PREFERENCES.c.preferred_language,
    ).select_from(billing.USERS.join(billing.USER_PROFILES,
        billing.USER_PROFILES.c.user_id == billing.USERS.c.id).outerjoin(
        billing.USER_PREFERENCES, billing.USER_PREFERENCES.c.user_id == billing.USERS.c.id
    )).where(billing.USERS.c.id == user_id,
        billing.USER_PROFILES.c.email_verified_at.is_not(None))).first()
    email = str(row.email or "") if row else ""
    if "@" not in email or email.lower().endswith(".invalid"):
        return None
    return {"email": email, "first_name": row.first_name or "", "language": row.preferred_language or "tr"}


def _purchase_currency(connection, reference: str) -> str:
    for orders in (billing.PAYMENT_ORDERS, billing.MANUAL_ORDERS):
        currency = connection.execute(
            select(orders.c.currency).where(orders.c.reference == reference)).scalar()
        if currency:
            return str(currency).upper()
    return "TRY"


def catalog_price(plan_code: str, interval: str, currency: str) -> tuple[int | None, str]:
    """The current catalog price for buying the same package again."""
    prices = REGIONAL_PRICES.get(plan_code, {})
    selected = currency if prices.get(currency) is not None else "TRY"
    amount = prices.get(selected)
    if amount is None:
        return None, selected
    return int(amount) * (10 if interval == "annual" else 1), selected


def _remaining_minutes(connection, subscription, now: datetime) -> int | None:
    try:
        plan, _terms_version = billing._plan_for_purchase_reference(
            connection, reference=subscription.source_reference,
            plan_code=subscription.plan_code, interval=subscription.interval,
        )
    except billing.BillingError:
        return None
    if plan.minutes is None:
        return None
    period_start = billing._subscription_usage_period_start(subscription, now)
    used = connection.execute(
        select(func.coalesce(func.sum(billing.USAGE_EVENTS.c.minutes), 0)).where(
            billing.USAGE_EVENTS.c.user_id == subscription.user_id,
            billing.USAGE_EVENTS.c.plan_code == subscription.plan_code,
            billing.USAGE_EVENTS.c.occurred_at >= period_start,
        )
    ).scalar_one()
    return max(0, int(plan.minutes) - int(used))


def _prepare(subscription, kind: str, now: datetime) -> dict | None:
    with billing.ENGINE.connect() as connection:
        recipient = _recipient(connection, subscription.user_id)
        if not recipient:
            return None
        remaining = _remaining_minutes(connection, subscription, now)
        currency = _purchase_currency(connection, subscription.source_reference)
    price_minor, price_currency = catalog_price(subscription.plan_code, subscription.interval, currency)
    message = render_renewal_reminder_email(
        recipient["language"],
        kind=kind,
        plan_code=subscription.plan_code,
        plan_name=plan_name(subscription.plan_code, recipient["language"]),
        interval=subscription.interval,
        ends_at=_utc(subscription.ends_at),
        remaining_minutes=remaining,
        price_minor=price_minor,
        currency=price_currency,
        first_name=recipient["first_name"],
        site_url=config.FRONTEND_BASE_URL,
    )
    return {"recipient": recipient, "message": message}


def _summary(kind: str, state: str, subscription_id: str) -> str:
    return f"{_SUMMARIES[state]} ({_KIND_LABELS[kind]}): {subscription_id}"


def _claim(events, subscription_id: str, kind: str, user_id: str, email: str) -> bool:
    key = event_id(subscription_id, kind)
    now = billing.utcnow()
    values = {"subject_email": email[:320], "action": event_action(kind, PENDING),
              "summary": _summary(kind, PENDING, subscription_id), "created_at": now}
    try:
        with billing.ENGINE.begin() as connection:
            connection.execute(events.insert().values(
                id=key, subject_user_id=user_id, actor=ACTOR, **values))
        return True
    except IntegrityError:
        pass
    # Sent and ambiguous deliveries stay closed, and so does a live claim.
    # Resend deduplicates the deterministic idempotency key for 24 hours, so
    # a definite failure or a stale claim left by a crashed sender may be
    # claimed again; the conditional update lets only one caller win.
    if config.EMAIL_PROVIDER != "resend":
        return False
    reclaimable = or_(
        events.c.action == event_action(kind, RETRY),
        and_(events.c.action == event_action(kind, PENDING), events.c.created_at < now - STALE_CLAIM),
    )
    with billing.ENGINE.begin() as connection:
        result = connection.execute(update(events).where(events.c.id == key, reclaimable).values(**values))
    return result.rowcount == 1


def _finish(events, subscription_id: str, kind: str, state: str) -> None:
    with billing.ENGINE.begin() as connection:
        connection.execute(update(events).where(
            events.c.id == event_id(subscription_id, kind),
            events.c.action == event_action(kind, PENDING),
        ).values(action=event_action(kind, state), summary=_summary(kind, state, subscription_id),
                 created_at=billing.utcnow()))


def _recorded(connection, events, subscription_id: str, kind: str) -> str | None:
    action = connection.execute(
        select(events.c.action).where(events.c.id == event_id(subscription_id, kind))).scalar()
    return str(action).rsplit("_", 1)[-1] if action else None


def _deliver(events, subscription, kind: str, now: datetime) -> str:
    prepared = _prepare(subscription, kind, now)
    if prepared is None:
        return "ineligible"
    recipient, message = prepared["recipient"], prepared["message"]
    if not _claim(events, subscription.id, kind, subscription.user_id, recipient["email"]):
        return "duplicate"
    try:
        send_transactional_email(recipient["email"], message["subject"], message["html"], message["text"],
            reply_to=config.BILLING_SUPPORT_EMAIL, idempotency_key=idempotency_key(subscription.id, kind))
    except Exception:
        # Provider exceptions can contain PII: store and log only bounded state.
        # SMTP has no idempotency key, so a failed attempt is never replayed.
        retryable = config.EMAIL_PROVIDER == "resend"
        _finish(events, subscription.id, kind, RETRY if retryable else UNKNOWN)
        return RETRY if retryable else UNKNOWN
    _finish(events, subscription.id, kind, SENT)
    return SENT


def run_once(now: datetime | None = None, *, dry_run: bool = False) -> dict:
    """Send the reminders due today; safe to call any number of times.

    Returns per-kind counts and the subscription ids it acted on. ``dry_run``
    only reports what a run inside the send window would do.
    """
    now = _utc(now or billing.utcnow())
    summary: dict = {"day": local_day(now).isoformat(), "dry_run": dry_run, "kinds": {}}
    if not dry_run:
        if os.getenv("LECTURESIFT_WORKER") == "1" or config.current_maintenance_mode() != "off":
            summary["skipped"] = "maintenance"
            return summary
        if not email_delivery_configured():
            summary["skipped"] = "unconfigured"
            return summary
        if not in_send_window(now):
            summary["skipped"] = "outside_send_window"
            return summary
    from .rollout_service import ADMIN_ACCOUNT_EVENTS, init_rollout_database
    billing.init_billing_database()
    init_rollout_database()
    for kind, days in DAYS_AHEAD.items():
        day = local_day(now) + timedelta(days=days)
        with billing.ENGINE.connect() as connection:
            due = due_subscriptions(connection, kind, day)
            recorded = {row.id: _recorded(connection, ADMIN_ACCOUNT_EVENTS, row.id, kind) for row in due}
        counts = {"due": len(due), "sent": 0, "duplicate": 0, "ineligible": 0, RETRY: 0, UNKNOWN: 0}
        acted: dict[str, list[str]] = {}
        for subscription in due:
            if dry_run:
                state = recorded[subscription.id]
                outcome = "duplicate" if state in (*CLOSED_STATES, PENDING) else "would_send"
                counts[outcome] = counts.get(outcome, 0) + 1
            elif recorded[subscription.id] in CLOSED_STATES:
                outcome = "duplicate"
                counts[outcome] += 1
            else:
                try:
                    outcome = _deliver(ADMIN_ACCOUNT_EVENTS, subscription, kind, now)
                except Exception:
                    # Driver exceptions can carry row values; keep the signal only.
                    LOGGER.warning("Renewal reminder could not be delivered")
                    outcome = "error"
                counts[outcome] = counts.get(outcome, 0) + 1
            acted.setdefault(outcome, []).append(str(subscription.id))
        summary["kinds"][kind] = {"day": day.isoformat(), **counts,
                                  "ids": {state: ids for state, ids in acted.items() if state != "duplicate"}}
        if not dry_run and due:
            LOGGER.info("Renewal reminders (%s, %s): due=%d sent=%d retry=%d unknown=%d duplicate=%d ineligible=%d",
                        kind, day.isoformat(), counts["due"], counts["sent"], counts[RETRY],
                        counts[UNKNOWN], counts["duplicate"], counts["ineligible"])
    return summary


async def reminder_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception:
            LOGGER.error("Renewal reminder run failed")
        await asyncio.sleep(LOOP_INTERVAL_SECONDS)


def _sample_message(language: str, kind: str) -> dict[str, str]:
    now = billing.utcnow()
    plan_code, interval = "plus", "monthly"
    price_minor, currency = catalog_price(plan_code, interval, "TRY")
    return render_renewal_reminder_email(
        language, kind=kind, plan_code=plan_code, plan_name=plan_name(plan_code, language),
        interval=interval, ends_at=now + timedelta(days=DAYS_AHEAD[kind]), remaining_minutes=712,
        price_minor=price_minor, currency=currency, site_url=config.FRONTEND_BASE_URL,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package-expiry reminder emails.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true",
                       help="Report what today's run would send, without sending or recording anything.")
    group.add_argument("--send", action="store_true", help="Run today's reminders now (idempotent).")
    group.add_argument("--preview", action="store_true", help="Print a sample email to stdout.")
    group.add_argument("--test-to", metavar="ADDRESS", help="Send one sample email to this address only.")
    parser.add_argument("--language", default="tr", help="Interface language of the sample (default: tr).")
    parser.add_argument("--kind", default="3_days", choices=KINDS, help="Sample kind (default: 3_days).")
    args = parser.parse_args(argv)
    if args.preview or args.test_to:
        message = _sample_message(args.language, args.kind)
        if args.preview:
            print(message["subject"])
            print()
            print(message["text"])
            return 0
        if "@" not in args.test_to:
            print("The test address is not an email address.", file=sys.stderr)
            return 2
        if not email_delivery_configured():
            print("Email delivery is not configured in this environment.", file=sys.stderr)
            return 1
        try:
            send_transactional_email(args.test_to, message["subject"], message["html"], message["text"],
                                     reply_to=config.BILLING_SUPPORT_EMAIL)
        except Exception:
            print("The sample email was not accepted by the provider.", file=sys.stderr)
            return 1
        print("Sample renewal reminder accepted by the email provider.")
        return 0
    summary = run_once(dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
