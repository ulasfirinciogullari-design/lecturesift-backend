"""Purchases, paid entitlements, output history, refunds, and account lifecycle.

The module is provider-neutral. PayTR callbacks are validated in ``paytr.py`` and
then reduced to idempotent purchase events here. Raw card data is never accepted
or stored by LectureSift.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    delete,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from . import config
from .billing import PLAN_BY_CODE, REGIONAL_PRICES, SUPPORTED_CURRENCIES
from .billing_service import (
    ENGINE,
    MANUAL_ORDERS,
    METADATA,
    SUBSCRIPTIONS,
    USERS,
    USER_PROFILES,
    BillingAuthenticationError,
    BillingError,
    init_billing_database,
    utcnow,
)


PURCHASES = Table(
    "lecturesift_purchases",
    METADATA,
    Column("reference", String(40), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("provider", String(24), nullable=False),
    Column("plan_code", String(32), nullable=False),
    Column("interval", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("amount_minor", Integer, nullable=False),
    Column("status", String(24), nullable=False, index=True),
    Column("provider_reference", String(120), nullable=True, unique=True),
    Column("failure_code", String(80), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("paid_at", DateTime(timezone=True), nullable=True),
)

PAYMENT_EVENTS = Table(
    "lecturesift_payment_events",
    METADATA,
    Column("event_key", String(64), primary_key=True),
    Column("provider", String(24), nullable=False),
    Column("purchase_reference", String(40), nullable=False, index=True),
    Column("status", String(24), nullable=False),
    Column("amount_minor", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

USER_ENTITLEMENTS = Table(
    "lecturesift_user_entitlements",
    METADATA,
    Column("user_id", String(36), ForeignKey("billing_users.id"), primary_key=True),
    Column("download_until", DateTime(timezone=True), nullable=True),
    Column("visual_translation_until", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

SUBSCRIPTION_CONTROLS = Table(
    "lecturesift_subscription_controls",
    METADATA,
    Column("subscription_id", String(36), ForeignKey("billing_subscriptions.id"), primary_key=True),
    Column("provider", String(24), nullable=False),
    Column("provider_subscription_id", String(160), nullable=True),
    Column("renewal_mode", String(32), nullable=False),
    Column("cancel_at_period_end", Boolean, nullable=False, default=False),
    Column("canceled_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

BILLING_DOCUMENTS = Table(
    "lecturesift_billing_documents",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("purchase_reference", String(40), nullable=False, index=True),
    Column("document_type", String(24), nullable=False),
    Column("document_number", String(48), nullable=False, unique=True),
    Column("currency", String(3), nullable=False),
    Column("amount_minor", Integer, nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
)

REFUND_REQUESTS = Table(
    "lecturesift_refund_requests",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("purchase_reference", String(40), nullable=False, index=True),
    Column("reason", Text, nullable=False),
    Column("status", String(24), nullable=False, index=True),
    Column("provider_reference", String(160), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

JOB_HISTORY = Table(
    "lecturesift_job_history",
    METADATA,
    Column("job_id", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("title", String(240), nullable=False),
    Column("source_type", String(32), nullable=False),
    Column("job_type", String(32), nullable=False),
    Column("output_language", String(12), nullable=False),
    Column("status", String(24), nullable=False, index=True),
    Column("plan_code", String(32), nullable=False),
    Column("download_entitled", Boolean, nullable=False, default=False),
    Column("visual_translation_requested", Boolean, nullable=False, default=False),
    Column("media_minutes", Integer, nullable=False, default=0),
    Column("remote_download_key", String(512), nullable=True),
    Column("output_size_bytes", Integer, nullable=True),
    Column("retention_until", DateTime(timezone=True), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
)

ACCOUNT_DELETIONS = Table(
    "lecturesift_account_deletions",
    METADATA,
    Column("user_id", String(36), primary_key=True),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)

_COMMERCE_INITIALIZED = False


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def init_commerce_database() -> None:
    global _COMMERCE_INITIALIZED
    init_billing_database()
    if _COMMERCE_INITIALIZED:
        return
    for table in (
        PURCHASES,
        PAYMENT_EVENTS,
        USER_ENTITLEMENTS,
        SUBSCRIPTION_CONTROLS,
        BILLING_DOCUMENTS,
        REFUND_REQUESTS,
        JOB_HISTORY,
        ACCOUNT_DELETIONS,
    ):
        table.create(bind=ENGINE, checkfirst=True)
    _COMMERCE_INITIALIZED = True


def _active_subscription(connection, user_id: str, now: datetime):
    return connection.execute(
        select(SUBSCRIPTIONS)
        .where(
            SUBSCRIPTIONS.c.user_id == user_id,
            SUBSCRIPTIONS.c.status == "active",
            SUBSCRIPTIONS.c.ends_at > now,
        )
        .order_by(SUBSCRIPTIONS.c.ends_at.desc())
        .limit(1)
    ).first()


def _plan_price(plan_code: str, interval: str, currency: str) -> int:
    plan = PLAN_BY_CODE.get(plan_code)
    if not plan or plan.code in {"free", "guest", "business"}:
        raise BillingError("Bu plan çevrim içi satın alma için kullanılamıyor.")
    valid = {"one_time"} if plan.kind == "one_time" else {"monthly", "annual"}
    if interval not in valid:
        raise BillingError("Geçersiz ödeme dönemi.")
    selected = currency.upper()
    if selected not in SUPPORTED_CURRENCIES:
        raise BillingError("Seçilen para birimi desteklenmiyor.")
    amount = REGIONAL_PRICES.get(plan_code, {}).get(selected)
    if amount is None:
        raise BillingError("Bu plan için seçilen para biriminde fiyat tanımlı değil.")
    return int(amount * (10 if interval == "annual" else 1))


def _public_purchase(row) -> dict[str, Any]:
    return {
        "reference": row.reference,
        "provider": row.provider,
        "plan_code": row.plan_code,
        "interval": row.interval,
        "currency": row.currency,
        "amount_minor": int(row.amount_minor),
        "status": row.status,
        "failure_code": row.failure_code,
        "created_at": _as_utc(row.created_at).isoformat(),
        "paid_at": _as_utc(row.paid_at).isoformat() if row.paid_at else None,
    }


def create_purchase(user_id: str, plan_code: str, interval: str, currency: str = "TRY", provider: str = "paytr") -> dict[str, Any]:
    init_commerce_database()
    amount_minor = _plan_price(plan_code, interval, currency)
    now = utcnow()
    reference = f"LSP{now:%y%m%d}{secrets.token_hex(5).upper()}"
    with ENGINE.begin() as connection:
        if not connection.execute(select(USERS.c.id).where(USERS.c.id == user_id)).first():
            raise BillingAuthenticationError("Hesap bulunamadı.")
        connection.execute(
            PURCHASES.insert().values(
                reference=reference,
                user_id=user_id,
                provider=provider,
                plan_code=plan_code,
                interval=interval,
                currency=currency.upper(),
                amount_minor=amount_minor,
                status="created",
                provider_reference=None,
                failure_code=None,
                created_at=now,
                updated_at=now,
                paid_at=None,
            )
        )
    return {
        "reference": reference,
        "provider": provider,
        "plan_code": plan_code,
        "interval": interval,
        "currency": currency.upper(),
        "amount_minor": amount_minor,
        "status": "created",
    }


def purchase_for_reference(reference: str, *, user_id: str | None = None) -> dict[str, Any]:
    init_commerce_database()
    with ENGINE.connect() as connection:
        query = select(PURCHASES).where(PURCHASES.c.reference == reference)
        if user_id:
            query = query.where(PURCHASES.c.user_id == user_id)
        row = connection.execute(query).first()
    if not row:
        raise BillingError("Ödeme kaydı bulunamadı.")
    return {**_public_purchase(row), "user_id": row.user_id}


def mark_purchase_failed(reference: str, failure_code: str = "payment_failed") -> None:
    if not reference:
        return
    init_commerce_database()
    with ENGINE.begin() as connection:
        connection.execute(
            update(PURCHASES)
            .where(PURCHASES.c.reference == reference, PURCHASES.c.status != "paid")
            .values(status="failed", failure_code=failure_code[:80], updated_at=utcnow())
        )


def _extend_user_entitlement(connection, user_id: str, plan_code: str, now: datetime) -> datetime:
    plan = PLAN_BY_CODE[plan_code]
    days = max(30, int(plan.output_retention_days or plan.history_days or 30))
    until = now + timedelta(days=days)
    row = connection.execute(select(USER_ENTITLEMENTS).where(USER_ENTITLEMENTS.c.user_id == user_id)).first()
    existing_download = _as_utc(row.download_until) if row and row.download_until else None
    existing_visual = _as_utc(row.visual_translation_until) if row and row.visual_translation_until else None
    download_until = max(existing_download or now, until) if plan.download_enabled else existing_download
    visual_until = max(existing_visual or now, until) if plan.visual_translation else existing_visual
    values = {"download_until": download_until, "visual_translation_until": visual_until, "updated_at": now}
    if row:
        connection.execute(update(USER_ENTITLEMENTS).where(USER_ENTITLEMENTS.c.user_id == user_id).values(**values))
    else:
        connection.execute(USER_ENTITLEMENTS.insert().values(user_id=user_id, **values))
    return until


def _upgrade_recent_jobs(connection, user_id: str, until: datetime) -> None:
    rows = connection.execute(select(JOB_HISTORY).where(JOB_HISTORY.c.user_id == user_id, JOB_HISTORY.c.deleted_at.is_(None))).all()
    for row in rows:
        current_until = _as_utc(row.retention_until) or utcnow()
        connection.execute(
            update(JOB_HISTORY)
            .where(JOB_HISTORY.c.job_id == row.job_id)
            .values(download_entitled=True, retention_until=max(current_until, until))
        )


def _grant_purchase_locked(connection, purchase, now: datetime) -> None:
    plan = PLAN_BY_CODE[purchase.plan_code]
    entitlement_until = _extend_user_entitlement(connection, purchase.user_id, purchase.plan_code, now)
    if plan.kind == "one_time":
        connection.execute(update(USERS).where(USERS.c.id == purchase.user_id).values(credit_minutes=USERS.c.credit_minutes + int(plan.minutes or 0)))
    else:
        connection.execute(update(SUBSCRIPTIONS).where(SUBSCRIPTIONS.c.user_id == purchase.user_id, SUBSCRIPTIONS.c.status == "active").values(status="superseded"))
        subscription_id = str(uuid.uuid4())
        period_days = 365 if purchase.interval == "annual" else 30
        connection.execute(
            SUBSCRIPTIONS.insert().values(
                id=subscription_id,
                user_id=purchase.user_id,
                plan_code=purchase.plan_code,
                interval=purchase.interval,
                status="active",
                starts_at=now,
                ends_at=now + timedelta(days=period_days),
                source_reference=purchase.reference,
                created_at=now,
            )
        )
        connection.execute(
            SUBSCRIPTION_CONTROLS.insert().values(
                subscription_id=subscription_id,
                provider=purchase.provider,
                provider_subscription_id=None,
                renewal_mode="automatic" if config.PAYTR_RECURRING_ENABLED else "manual_until_card_storage_permission",
                cancel_at_period_end=False,
                canceled_at=None,
                updated_at=now,
            )
        )
    _upgrade_recent_jobs(connection, purchase.user_id, entitlement_until)
    connection.execute(
        BILLING_DOCUMENTS.insert().values(
            id=str(uuid.uuid4()),
            user_id=purchase.user_id,
            purchase_reference=purchase.reference,
            document_type="payment_receipt",
            document_number=f"LSR-{now:%Y%m%d}-{secrets.token_hex(3).upper()}",
            currency=purchase.currency,
            amount_minor=int(purchase.amount_minor),
            issued_at=now,
        )
    )


def accept_payment_event(*, provider: str, reference: str, status: str, amount_minor: int | None, provider_reference: str = "", event_identity: str = "") -> dict[str, Any]:
    init_commerce_database()
    normalized_status = "paid" if status == "success" else "failed"
    event_material = event_identity or f"{provider}|{reference}|{normalized_status}|{amount_minor}|{provider_reference}"
    event_key = hashlib.sha256(event_material.encode("utf-8")).hexdigest()
    now = utcnow()
    with ENGINE.begin() as connection:
        if connection.execute(select(PAYMENT_EVENTS.c.event_key).where(PAYMENT_EVENTS.c.event_key == event_key)).first():
            purchase = connection.execute(select(PURCHASES).where(PURCHASES.c.reference == reference)).first()
            if not purchase:
                raise BillingError("Ödeme kaydı bulunamadı.")
            return {"duplicate": True, "purchase": _public_purchase(purchase)}
        purchase = connection.execute(select(PURCHASES).where(PURCHASES.c.reference == reference)).first()
        if not purchase:
            raise BillingError("Ödeme kaydı bulunamadı.")
        if amount_minor is not None and int(amount_minor) != int(purchase.amount_minor):
            connection.execute(update(PURCHASES).where(PURCHASES.c.reference == reference).values(status="review_required", failure_code="amount_mismatch", updated_at=now))
            connection.execute(PAYMENT_EVENTS.insert().values(event_key=event_key, provider=provider, purchase_reference=reference, status="review_required", amount_minor=amount_minor, created_at=now))
            return {"duplicate": False, "purchase": {**_public_purchase(purchase), "status": "review_required", "failure_code": "amount_mismatch"}}
        connection.execute(PAYMENT_EVENTS.insert().values(event_key=event_key, provider=provider, purchase_reference=reference, status=normalized_status, amount_minor=amount_minor, created_at=now))
        if normalized_status == "paid":
            if purchase.status != "paid":
                connection.execute(update(PURCHASES).where(PURCHASES.c.reference == reference).values(status="paid", provider_reference=provider_reference[:120] or None, failure_code=None, updated_at=now, paid_at=now))
                _grant_purchase_locked(connection, purchase, now)
        elif purchase.status != "paid":
            connection.execute(update(PURCHASES).where(PURCHASES.c.reference == reference).values(status="failed", provider_reference=provider_reference[:120] or None, failure_code="provider_failed", updated_at=now))
        current = connection.execute(select(PURCHASES).where(PURCHASES.c.reference == reference)).first()
    return {"duplicate": False, "purchase": _public_purchase(current)}


def record_manual_order_entitlement(reference: str) -> None:
    init_commerce_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        order = connection.execute(select(MANUAL_ORDERS).where(MANUAL_ORDERS.c.reference == reference, MANUAL_ORDERS.c.status == "paid")).first()
        if not order:
            return
        until = _extend_user_entitlement(connection, order.user_id, order.plan_code, now)
        _upgrade_recent_jobs(connection, order.user_id, until)
        existing = connection.execute(select(BILLING_DOCUMENTS.c.id).where(BILLING_DOCUMENTS.c.purchase_reference == reference)).first()
        if not existing:
            connection.execute(BILLING_DOCUMENTS.insert().values(id=str(uuid.uuid4()), user_id=order.user_id, purchase_reference=reference, document_type="payment_receipt", document_number=f"LSR-{now:%Y%m%d}-{secrets.token_hex(3).upper()}", currency=order.currency, amount_minor=int(order.amount_minor), issued_at=now))


def _paid_manual_order_exists(connection, user_id: str) -> bool:
    return connection.execute(select(MANUAL_ORDERS.c.reference).where(MANUAL_ORDERS.c.user_id == user_id, MANUAL_ORDERS.c.status == "paid").limit(1)).first() is not None


def commerce_entitlements(user_id: str) -> dict[str, Any]:
    init_commerce_database()
    now = utcnow()
    with ENGINE.connect() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        if not user:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        subscription = _active_subscription(connection, user_id, now)
        plan_code = subscription.plan_code if subscription else "free"
        plan = PLAN_BY_CODE.get(plan_code, PLAN_BY_CODE["free"])
        explicit = connection.execute(select(USER_ENTITLEMENTS).where(USER_ENTITLEMENTS.c.user_id == user_id)).first()
        download_until = _as_utc(explicit.download_until) if explicit and explicit.download_until else None
        visual_until = _as_utc(explicit.visual_translation_until) if explicit and explicit.visual_translation_until else None
        has_paid_order = _paid_manual_order_exists(connection, user_id)
    return {
        "plan_code": plan_code,
        "download_enabled": bool(plan.download_enabled or (download_until and download_until > now) or has_paid_order),
        "visual_translation": bool(plan.visual_translation or (visual_until and visual_until > now) or has_paid_order),
        "download_until": download_until.isoformat() if download_until else None,
        "visual_translation_until": visual_until.isoformat() if visual_until else None,
        "output_retention_days": max(1, int(plan.output_retention_days or plan.history_days or 1)),
        "automatic_renewal_available": bool(config.PAYTR_RECURRING_ENABLED),
    }


def require_download_access(user_id: str, job_id: str | None = None) -> dict[str, Any]:
    entitlement = commerce_entitlements(user_id)
    if job_id:
        init_commerce_database()
        with ENGINE.connect() as connection:
            job = connection.execute(select(JOB_HISTORY).where(JOB_HISTORY.c.job_id == job_id, JOB_HISTORY.c.user_id == user_id, JOB_HISTORY.c.deleted_at.is_(None))).first()
        if job and bool(job.download_entitled):
            return entitlement
    if not entitlement["download_enabled"]:
        raise BillingError("İndirme için Mini paket veya ücretli bir plan seçmen gerekiyor.")
    return entitlement


def require_visual_translation_access(user_id: str) -> dict[str, Any]:
    entitlement = commerce_entitlements(user_id)
    if not entitlement["visual_translation"]:
        raise BillingError("Görsel içindeki yazıları çevirme özelliği Mini paket ve ücretli planlara dahildir.")
    return entitlement


def register_job_history(user_id: str, job_id: str, *, source_type: str, job_type: str, output_language: str, visual_translation_requested: bool, title: str = "LectureSift dersi") -> None:
    init_commerce_database()
    now = utcnow()
    entitlement = commerce_entitlements(user_id)
    try:
        with ENGINE.begin() as connection:
            connection.execute(JOB_HISTORY.insert().values(job_id=job_id, user_id=user_id, title=title[:240], source_type=source_type[:32], job_type=job_type[:32], output_language=output_language[:12], status="queued", plan_code=entitlement["plan_code"], download_entitled=bool(entitlement["download_enabled"]), visual_translation_requested=bool(visual_translation_requested), media_minutes=0, remote_download_key=None, output_size_bytes=None, retention_until=now + timedelta(days=max(1, int(entitlement["output_retention_days"]))), created_at=now, completed_at=None, deleted_at=None))
    except IntegrityError:
        return


def complete_job_history(job_id: str, *, status: str, title: str = "", media_minutes: float = 0, remote_download_key: str = "", output_size_bytes: int | None = None) -> None:
    init_commerce_database()
    values: dict[str, Any] = {"status": status[:24], "media_minutes": max(0, int(round(media_minutes)))}
    if title:
        values["title"] = title[:240]
    if status == "done":
        values["completed_at"] = utcnow()
    if remote_download_key:
        values["remote_download_key"] = remote_download_key[:512]
    if output_size_bytes is not None:
        values["output_size_bytes"] = max(0, int(output_size_bytes))
    with ENGINE.begin() as connection:
        connection.execute(update(JOB_HISTORY).where(JOB_HISTORY.c.job_id == job_id).values(**values))


def attach_remote_output(job_id: str, key: str, size_bytes: int) -> None:
    complete_job_history(job_id, status="done", remote_download_key=key, output_size_bytes=size_bytes)


def list_job_history(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    init_commerce_database()
    with ENGINE.connect() as connection:
        rows = connection.execute(select(JOB_HISTORY).where(JOB_HISTORY.c.user_id == user_id, JOB_HISTORY.c.deleted_at.is_(None)).order_by(JOB_HISTORY.c.created_at.desc()).limit(max(1, min(int(limit), 100)))).all()
    return [{"job_id": row.job_id, "title": row.title, "source_type": row.source_type, "job_type": row.job_type, "output_language": row.output_language, "status": row.status, "plan_code": row.plan_code, "download_entitled": bool(row.download_entitled), "visual_translation_requested": bool(row.visual_translation_requested), "media_minutes": int(row.media_minutes or 0), "output_size_bytes": row.output_size_bytes, "retention_until": _as_utc(row.retention_until).isoformat(), "created_at": _as_utc(row.created_at).isoformat(), "completed_at": _as_utc(row.completed_at).isoformat() if row.completed_at else None} for row in rows]


def mark_job_deleted(user_id: str, job_id: str) -> str:
    init_commerce_database()
    with ENGINE.begin() as connection:
        row = connection.execute(select(JOB_HISTORY).where(JOB_HISTORY.c.job_id == job_id, JOB_HISTORY.c.user_id == user_id, JOB_HISTORY.c.deleted_at.is_(None))).first()
        if not row:
            raise BillingError("Ders kaydı bulunamadı.")
        connection.execute(update(JOB_HISTORY).where(JOB_HISTORY.c.job_id == job_id).values(status="deleted", deleted_at=utcnow()))
    return str(row.remote_download_key or "")


def expired_output_rows(limit: int = 200) -> list[dict[str, str]]:
    init_commerce_database()
    with ENGINE.connect() as connection:
        rows = connection.execute(select(JOB_HISTORY.c.job_id, JOB_HISTORY.c.remote_download_key).where(JOB_HISTORY.c.deleted_at.is_(None), JOB_HISTORY.c.retention_until <= utcnow(), JOB_HISTORY.c.remote_download_key.is_not(None)).limit(max(1, min(int(limit), 1000)))).all()
    return [{"job_id": row.job_id, "key": str(row.remote_download_key or "")} for row in rows if row.remote_download_key]


def mark_output_expired(job_id: str) -> None:
    init_commerce_database()
    with ENGINE.begin() as connection:
        connection.execute(update(JOB_HISTORY).where(JOB_HISTORY.c.job_id == job_id).values(status="expired", remote_download_key=None, output_size_bytes=None))


def list_purchases(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    init_commerce_database()
    with ENGINE.connect() as connection:
        rows = connection.execute(select(PURCHASES).where(PURCHASES.c.user_id == user_id).order_by(PURCHASES.c.created_at.desc()).limit(max(1, min(int(limit), 100)))).all()
    return [_public_purchase(row) for row in rows]


def list_billing_documents(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    init_commerce_database()
    with ENGINE.connect() as connection:
        rows = connection.execute(select(BILLING_DOCUMENTS).where(BILLING_DOCUMENTS.c.user_id == user_id).order_by(BILLING_DOCUMENTS.c.issued_at.desc()).limit(max(1, min(int(limit), 100)))).all()
    return [{"id": row.id, "purchase_reference": row.purchase_reference, "document_type": row.document_type, "document_number": row.document_number, "currency": row.currency, "amount_minor": int(row.amount_minor), "issued_at": _as_utc(row.issued_at).isoformat(), "official_invoice": False, "notice": "Bu kayıt ödeme makbuzudur; resmî e-fatura/e-arşiv belgesi değildir."} for row in rows]


def set_cancel_at_period_end(user_id: str, cancel: bool) -> dict[str, Any]:
    init_commerce_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        subscription = _active_subscription(connection, user_id, now)
        if not subscription:
            raise BillingError("Aktif abonelik bulunamadı.")
        control = connection.execute(select(SUBSCRIPTION_CONTROLS).where(SUBSCRIPTION_CONTROLS.c.subscription_id == subscription.id)).first()
        values = {"cancel_at_period_end": bool(cancel), "canceled_at": now if cancel else None, "updated_at": now}
        if control:
            connection.execute(update(SUBSCRIPTION_CONTROLS).where(SUBSCRIPTION_CONTROLS.c.subscription_id == subscription.id).values(**values))
        else:
            connection.execute(SUBSCRIPTION_CONTROLS.insert().values(subscription_id=subscription.id, provider="manual", provider_subscription_id=None, renewal_mode="manual", **values))
    return {"subscription_id": subscription.id, "cancel_at_period_end": bool(cancel), "ends_at": _as_utc(subscription.ends_at).isoformat()}


def subscription_status(user_id: str) -> dict[str, Any] | None:
    init_commerce_database()
    with ENGINE.connect() as connection:
        subscription = _active_subscription(connection, user_id, utcnow())
        if not subscription:
            return None
        control = connection.execute(select(SUBSCRIPTION_CONTROLS).where(SUBSCRIPTION_CONTROLS.c.subscription_id == subscription.id)).first()
    return {"id": subscription.id, "plan_code": subscription.plan_code, "interval": subscription.interval, "status": subscription.status, "starts_at": _as_utc(subscription.starts_at).isoformat(), "ends_at": _as_utc(subscription.ends_at).isoformat(), "renewal_mode": control.renewal_mode if control else "manual", "cancel_at_period_end": bool(control.cancel_at_period_end) if control else False, "automatic_renewal_available": bool(config.PAYTR_RECURRING_ENABLED)}


def request_refund(user_id: str, purchase_reference: str, reason: str) -> dict[str, Any]:
    init_commerce_database()
    cleaned = " ".join(str(reason or "").split())
    if len(cleaned) < 10 or len(cleaned) > 1000:
        raise BillingError("İade nedenini 10 ile 1000 karakter arasında yaz.")
    now = utcnow()
    with ENGINE.begin() as connection:
        purchase = connection.execute(select(PURCHASES).where(PURCHASES.c.reference == purchase_reference, PURCHASES.c.user_id == user_id, PURCHASES.c.status == "paid")).first()
        if not purchase:
            raise BillingError("İadeye uygun ödeme kaydı bulunamadı.")
        existing = connection.execute(select(REFUND_REQUESTS.c.id).where(REFUND_REQUESTS.c.user_id == user_id, REFUND_REQUESTS.c.purchase_reference == purchase_reference, REFUND_REQUESTS.c.status.in_(("pending", "provider_pending")))).first()
        if existing:
            raise BillingError("Bu ödeme için açık bir iade talebi zaten var.")
        refund_id = str(uuid.uuid4())
        connection.execute(REFUND_REQUESTS.insert().values(id=refund_id, user_id=user_id, purchase_reference=purchase_reference, reason=cleaned, status="pending", provider_reference=None, created_at=now, updated_at=now))
    return {"id": refund_id, "purchase_reference": purchase_reference, "status": "pending", "created_at": now.isoformat()}


def list_refunds(user_id: str) -> list[dict[str, Any]]:
    init_commerce_database()
    with ENGINE.connect() as connection:
        rows = connection.execute(select(REFUND_REQUESTS).where(REFUND_REQUESTS.c.user_id == user_id).order_by(REFUND_REQUESTS.c.created_at.desc())).all()
    return [{"id": row.id, "purchase_reference": row.purchase_reference, "reason": row.reason, "status": row.status, "created_at": _as_utc(row.created_at).isoformat(), "updated_at": _as_utc(row.updated_at).isoformat()} for row in rows]


def admin_refunds(status: str = "pending") -> list[dict[str, Any]]:
    init_commerce_database()
    with ENGINE.connect() as connection:
        query = select(REFUND_REQUESTS, USERS.c.email).join(USERS, USERS.c.id == REFUND_REQUESTS.c.user_id).order_by(REFUND_REQUESTS.c.created_at.asc())
        if status:
            query = query.where(REFUND_REQUESTS.c.status == status)
        rows = connection.execute(query).all()
    return [dict(row._mapping) for row in rows]


def refund_for_admin(refund_id: str) -> dict[str, Any]:
    init_commerce_database()
    with ENGINE.connect() as connection:
        row = connection.execute(select(REFUND_REQUESTS).where(REFUND_REQUESTS.c.id == refund_id)).first()
        if not row:
            raise BillingError("İade talebi bulunamadı.")
        purchase = connection.execute(select(PURCHASES).where(PURCHASES.c.reference == row.purchase_reference)).first()
    if not purchase:
        raise BillingError("İadeye bağlı ödeme kaydı bulunamadı.")
    return {"refund": dict(row._mapping), "purchase": {**_public_purchase(purchase), "user_id": purchase.user_id}}


def update_refund_status(refund_id: str, status: str, provider_reference: str = "") -> dict[str, Any]:
    allowed = {"pending", "provider_pending", "refunded", "rejected"}
    if status not in allowed:
        raise BillingError("Geçersiz iade durumu.")
    init_commerce_database()
    with ENGINE.begin() as connection:
        row = connection.execute(select(REFUND_REQUESTS).where(REFUND_REQUESTS.c.id == refund_id)).first()
        if not row:
            raise BillingError("İade talebi bulunamadı.")
        connection.execute(update(REFUND_REQUESTS).where(REFUND_REQUESTS.c.id == refund_id).values(status=status, provider_reference=provider_reference[:160] or row.provider_reference, updated_at=utcnow()))
    return {"id": refund_id, "status": status}


def preview_result_for_user(user_id: str, job_id: str, result: dict) -> dict:
    try:
        require_download_access(user_id, job_id)
        return {**result, "download_locked": False, "preview_limited": False}
    except BillingError:
        def clip(value: object, length: int) -> str:
            text = str(value or "")
            return text if len(text) <= length else text[:length].rstrip() + "…"
        preview = dict(result)
        preview["summary"] = clip(result.get("summary"), 1600)
        preview["key_points"] = list(result.get("key_points") or [])[:6]
        preview["important_terms"] = list(result.get("important_terms") or [])[:6]
        preview["notes"] = [{**item, "content": clip(item.get("content"), 650), "bullets": list(item.get("bullets") or [])[:4]} for item in list(result.get("notes") or [])[:3] if isinstance(item, dict)]
        preview["exam_focus"] = list(result.get("exam_focus") or [])[:3]
        preview["quiz"] = [{"question": item.get("question", ""), "options": list(item.get("options") or []), "answer_locked": True} for item in list(result.get("quiz") or [])[:3] if isinstance(item, dict)]
        preview["flashcards"] = [{"front": item.get("front", ""), "back": "Ücretli pakette açılır."} for item in list(result.get("flashcards") or [])[:5] if isinstance(item, dict)]
        preview["transcript_original"] = clip(result.get("transcript_original"), 3500)
        preview["transcript_translated"] = clip(result.get("transcript_translated"), 3500)
        preview["transcript"] = clip(result.get("transcript"), 3500)
        preview["slides"] = list(result.get("slides") or [])[:3]
        preview["artifacts"] = []
        preview["download_locked"] = True
        preview["preview_limited"] = True
        preview["unlock_plan"] = "mini"
        preview["preview_message"] = "Tam dosyaları indirmek ve tüm sonucu açmak için Mini paket veya ücretli plan seç."
        return preview


def account_commerce_status(user_id: str) -> dict[str, Any]:
    return {"entitlements": commerce_entitlements(user_id), "subscription": subscription_status(user_id), "purchases": list_purchases(user_id), "documents": list_billing_documents(user_id), "refunds": list_refunds(user_id), "jobs": list_job_history(user_id)}


def request_account_deletion(user_id: str) -> dict[str, Any]:
    init_commerce_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        row = connection.execute(select(ACCOUNT_DELETIONS).where(ACCOUNT_DELETIONS.c.user_id == user_id)).first()
        if row:
            connection.execute(update(ACCOUNT_DELETIONS).where(ACCOUNT_DELETIONS.c.user_id == user_id).values(requested_at=now, completed_at=None))
        else:
            connection.execute(ACCOUNT_DELETIONS.insert().values(user_id=user_id, requested_at=now, completed_at=None))
    return {"ok": True, "status": "scheduled", "requested_at": now.isoformat(), "grace_days": int(config.ACCOUNT_DELETION_GRACE_DAYS)}


def cancel_account_deletion(user_id: str) -> None:
    init_commerce_database()
    with ENGINE.begin() as connection:
        connection.execute(delete(ACCOUNT_DELETIONS).where(ACCOUNT_DELETIONS.c.user_id == user_id))


def due_account_deletions(limit: int = 100) -> list[str]:
    init_commerce_database()
    cutoff = utcnow() - timedelta(days=int(config.ACCOUNT_DELETION_GRACE_DAYS))
    with ENGINE.connect() as connection:
        rows = connection.execute(select(ACCOUNT_DELETIONS.c.user_id).where(ACCOUNT_DELETIONS.c.completed_at.is_(None), ACCOUNT_DELETIONS.c.requested_at <= cutoff).limit(max(1, min(int(limit), 500)))).all()
    return [row.user_id for row in rows]


def anonymize_account(user_id: str) -> list[str]:
    init_commerce_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        if not user:
            return []
        keys = [str(row.remote_download_key) for row in connection.execute(select(JOB_HISTORY.c.remote_download_key).where(JOB_HISTORY.c.user_id == user_id, JOB_HISTORY.c.remote_download_key.is_not(None))).all() if row.remote_download_key]
        connection.execute(update(JOB_HISTORY).where(JOB_HISTORY.c.user_id == user_id).values(status="deleted", deleted_at=now, remote_download_key=None))
        connection.execute(delete(USER_ENTITLEMENTS).where(USER_ENTITLEMENTS.c.user_id == user_id))
        connection.execute(update(SUBSCRIPTIONS).where(SUBSCRIPTIONS.c.user_id == user_id, SUBSCRIPTIONS.c.status == "active").values(status="canceled"))
        anonymous_email = f"deleted-{hashlib.sha256(user_id.encode()).hexdigest()[:24]}@deleted.lecturesift.invalid"
        connection.execute(update(USERS).where(USERS.c.id == user_id).values(email=anonymous_email, password_salt=secrets.token_bytes(16).hex(), password_hash=hashlib.sha256(secrets.token_bytes(48)).hexdigest(), credit_minutes=0))
        connection.execute(update(USER_PROFILES).where(USER_PROFILES.c.user_id == user_id).values(first_name="Silinmiş", last_name="Hesap", phone=None, session_version=USER_PROFILES.c.session_version + 1, updated_at=now))
        connection.execute(update(ACCOUNT_DELETIONS).where(ACCOUNT_DELETIONS.c.user_id == user_id).values(completed_at=now))
    return keys
