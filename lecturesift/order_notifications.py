"""Best-effort order confirmation email after a purchase has committed.

Payment completion calls this only after the paid transaction has committed.
Email trouble is logged without PII and never fails, changes or rolls back an
order. Each order's delivery state is one row with a deterministic id in the
existing admin account event log, so duplicate provider callbacks and repeated
admin approvals cannot email the same order twice. No schema change is needed.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import billing_service as billing, config
from .mailer import email_delivery_configured, send_transactional_email

LOGGER = logging.getLogger(__name__)
# A later callback may retry a failed send only while the provider still
# deduplicates the idempotency key. Older paid orders are never backfilled.
RETRY_WINDOW = timedelta(hours=24)
EVENT_NAMESPACE = uuid.UUID("5d0c7e43-2f1b-4c55-9a35-0b8f3c6e7a21")
ACTOR = "system"
PENDING = "order_confirmation_pending"
SENT = "order_confirmation_sent"
RETRY = "order_confirmation_retry"
UNKNOWN = "order_confirmation_unknown"
_SUMMARIES = {
    PENDING: "Sipariş onay e-postası gönderiliyor",
    SENT: "Sipariş onay e-postası gönderildi",
    RETRY: "Sipariş onay e-postası gönderilemedi; sonraki ödeme bildiriminde yeniden denenecek",
    UNKNOWN: "Sipariş onay e-postasının teslim durumu belirsiz; yeniden gönderilmeyecek",
}


def event_id(reference: str) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, reference))


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _find_order(connection, reference: str):
    for table in (billing.PAYMENT_ORDERS, billing.MANUAL_ORDERS):
        order = connection.execute(select(table).where(table.c.reference == reference)).first()
        if order:
            return order
    return None


def _recipient(connection, user_id: str, reference: str) -> dict | None:
    # Same eligibility as job notifications: a verified profile is required.
    # Reserved .invalid domains (erased, guest and synthetic accounts) never
    # receive mail.
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
    # A saved account language wins; otherwise use the language the checkout
    # was shown in, recorded with the payment consent.
    language = row.preferred_language or connection.execute(
        select(billing.PAYMENT_CONSENTS.c.language).where(
            billing.PAYMENT_CONSENTS.c.order_reference == reference)
    ).scalar() or "tr"
    return {"email": email, "first_name": row.first_name or "", "language": language}


def _prepare(reference: str) -> tuple[str, dict | None]:
    with billing.ENGINE.connect() as connection:
        order = _find_order(connection, reference)
        if order is None or order.status != "paid":
            return "not_paid", None
        if _utc(order.updated_at) < billing.utcnow() - RETRY_WINDOW:
            return "historical", None
        recipient = _recipient(connection, order.user_id, reference)
        if not recipient:
            return "ineligible", None
        plan, _terms_version = billing._plan_for_purchase_reference(
            connection, reference=reference, plan_code=order.plan_code, interval=order.interval,
        )
        ends_at = None
        if plan.kind == "subscription":
            ends_at = connection.execute(select(billing.SUBSCRIPTIONS.c.ends_at).where(
                billing.SUBSCRIPTIONS.c.source_reference == reference)).scalar()
    from .order_notification_templates import render_order_confirmation_email
    message = render_order_confirmation_email(
        recipient["language"],
        reference=reference,
        plan_code=order.plan_code,
        plan_kind=plan.kind,
        interval=order.interval,
        amount_minor=order.amount_minor,
        currency=order.currency,
        purchased_at=order.updated_at,
        minutes=plan.minutes,
        assistant_credits=plan.assistant_credits,
        ends_at=ends_at,
        first_name=recipient["first_name"],
        seller=billing.commerce_identity(),
        site_url=config.FRONTEND_BASE_URL,
    )
    return "ready", {"order": order, "recipient": recipient, "message": message}


def _claim(events, reference: str, user_id: str, email: str) -> bool:
    key = event_id(reference)
    values = {"subject_email": email[:320], "action": PENDING,
              "summary": f"{_SUMMARIES[PENDING]}: {reference}", "created_at": billing.utcnow()}
    try:
        with billing.ENGINE.begin() as connection:
            connection.execute(events.insert().values(
                id=key, subject_user_id=user_id, actor=ACTOR, **values))
        return True
    except IntegrityError:
        pass
    # Pending, sent and ambiguous deliveries stay closed. Only a definite
    # failure that the provider would still deduplicate is claimed again.
    with billing.ENGINE.begin() as connection:
        result = connection.execute(update(events).where(
            events.c.id == key, events.c.action == RETRY).values(**values))
    return result.rowcount == 1


def _finish(events, reference: str, action: str) -> None:
    with billing.ENGINE.begin() as connection:
        connection.execute(update(events).where(
            events.c.id == event_id(reference), events.c.action == PENDING,
        ).values(action=action, summary=f"{_SUMMARIES[action]}: {reference}", created_at=billing.utcnow()))


def _deliver(reference: str) -> str:
    if not email_delivery_configured():
        return "unconfigured"
    from .rollout_service import ADMIN_ACCOUNT_EVENTS, init_rollout_database
    init_rollout_database()
    state, prepared = _prepare(reference)
    if prepared is None:
        return state
    recipient, message = prepared["recipient"], prepared["message"]
    if not _claim(ADMIN_ACCOUNT_EVENTS, reference, prepared["order"].user_id, recipient["email"]):
        return "duplicate"
    # A retry within the window reuses this key, so the provider drops a copy
    # whose first attempt was accepted despite an ambiguous response.
    key = "order-confirmation/" + hashlib.sha256(reference.encode()).hexdigest()
    try:
        send_transactional_email(recipient["email"], message["subject"], message["html"], message["text"],
            reply_to=config.BILLING_SUPPORT_EMAIL, idempotency_key=key)
    except Exception:
        # Provider exceptions can contain PII: store and log only bounded state.
        # SMTP has no idempotency key, so a failed attempt is never replayed.
        retryable = config.EMAIL_PROVIDER == "resend"
        _finish(ADMIN_ACCOUNT_EVENTS, reference, RETRY if retryable else UNKNOWN)
        LOGGER.warning("Order confirmation email delivery failed")
        return "retry" if retryable else "unknown"
    _finish(ADMIN_ACCOUNT_EVENTS, reference, SENT)
    return "sent"


def notify_paid_order(reference: str) -> str:
    """Send one confirmation for a committed paid order; never raises."""
    try:
        return _deliver(str(reference or ""))
    except Exception:
        LOGGER.warning("Order confirmation email could not be prepared")
        return "error"
