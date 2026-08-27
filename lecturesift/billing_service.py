"""Accounts, entitlements, and manual bank-transfer orders.

Bank details are supplied only through runtime environment variables. They are
never written to the database, logs, or repository.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from . import config
from .billing import PLAN_BY_CODE


class BillingError(Exception):
    pass


class BillingAuthenticationError(BillingError):
    pass


class BillingConfigurationError(BillingError):
    pass


def _database_url() -> str:
    value = config.DATABASE_URL
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


_ENGINE_OPTIONS = {"pool_pre_ping": True}
if _database_url().startswith("sqlite:"):
    _ENGINE_OPTIONS["connect_args"] = {"check_same_thread": False}

ENGINE = create_engine(_database_url(), **_ENGINE_OPTIONS)
METADATA = MetaData()

USERS = Table(
    "billing_users",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("password_salt", String(64), nullable=False),
    Column("password_hash", String(64), nullable=False),
    Column("credit_minutes", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

SUBSCRIPTIONS = Table(
    "billing_subscriptions",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("plan_code", String(32), nullable=False),
    Column("interval", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("starts_at", DateTime(timezone=True), nullable=False),
    Column("ends_at", DateTime(timezone=True), nullable=False),
    Column("source_reference", String(40), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

MANUAL_ORDERS = Table(
    "billing_manual_orders",
    METADATA,
    Column("reference", String(40), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("plan_code", String(32), nullable=False),
    Column("interval", String(16), nullable=False),
    Column("amount_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

USAGE_EVENTS = Table(
    "billing_usage_events",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("job_id", String(64), nullable=False, unique=True),
    Column("plan_code", String(32), nullable=False),
    Column("minutes", Integer, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def init_billing_database() -> None:
    global _INITIALIZED
    if os.getenv("RENDER") and _database_url().startswith("sqlite:"):
        raise BillingConfigurationError("Kalıcı abonelik veritabanı henüz bağlanmamış.")
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if not _INITIALIZED:
            METADATA.create_all(ENGINE)
            _INITIALIZED = True


def billing_database_health() -> dict:
    init_billing_database()
    backend = ENGINE.url.get_backend_name()
    return {"connected": True, "persistent": backend == "postgresql", "backend": backend}


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return digest.hex()


def _public_user(row) -> dict:
    return {"id": row.id, "email": row.email}


def register_user(email: str, password: str) -> dict:
    init_billing_database()
    normalized = email.strip().casefold()
    if "@" not in normalized or len(normalized) > 320:
        raise BillingError("Geçerli bir e-posta adresi gir.")
    if len(password) < 10:
        raise BillingError("Parola en az 10 karakter olmalı.")
    salt = secrets.token_bytes(16)
    values = {
        "id": str(uuid.uuid4()),
        "email": normalized,
        "password_salt": salt.hex(),
        "password_hash": _hash_password(password, salt),
        "credit_minutes": 0,
        "created_at": utcnow(),
    }
    try:
        with ENGINE.begin() as connection:
            connection.execute(USERS.insert().values(**values))
    except IntegrityError as exc:
        raise BillingError("Bu e-posta adresiyle daha önce hesap oluşturulmuş.") from exc
    return {"user": {"id": values["id"], "email": normalized}, "token": issue_session(values["id"], normalized)}


def login_user(email: str, password: str) -> dict:
    init_billing_database()
    normalized = email.strip().casefold()
    with ENGINE.connect() as connection:
        row = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
    if not row:
        raise BillingAuthenticationError("E-posta veya parola hatalı.")
    candidate = _hash_password(password, bytes.fromhex(row.password_salt))
    if not hmac.compare_digest(candidate, row.password_hash):
        raise BillingAuthenticationError("E-posta veya parola hatalı.")
    return {"user": _public_user(row), "token": issue_session(row.id, row.email)}


def _token_secret() -> bytes:
    explicit = config.BILLING_SESSION_SECRET.encode("utf-8")
    if explicit:
        return explicit
    return hashlib.sha256((config.DATABASE_URL + "|lecturesift-session-v1").encode("utf-8")).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_session(user_id: str, email: str) -> str:
    payload = json.dumps(
        {"sub": user_id, "email": email, "exp": int((utcnow() + timedelta(days=14)).timestamp())},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    body = _b64encode(payload)
    signature = _b64encode(hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def authenticate_session(token: str) -> dict:
    try:
        body, signature = token.split(".", 1)
        expected = _b64encode(hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(_b64decode(body))
        if int(payload["exp"]) <= int(utcnow().timestamp()):
            raise ValueError("expired")
        user_id = str(payload["sub"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BillingAuthenticationError("Oturum geçersiz veya süresi dolmuş.") from exc
    init_billing_database()
    with ENGINE.connect() as connection:
        row = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
    if not row:
        raise BillingAuthenticationError("Hesap bulunamadı.")
    return _public_user(row)


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


def account_status(user_id: str) -> dict:
    init_billing_database()
    now = utcnow()
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    with ENGINE.connect() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        if not user:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        subscription = _active_subscription(connection, user_id, now)
        plan_code = subscription.plan_code if subscription else "free"
        plan = PLAN_BY_CODE[plan_code]
        period_start = subscription.starts_at if subscription else month_start
        used = connection.execute(
            select(func.coalesce(func.sum(USAGE_EVENTS.c.minutes), 0)).where(
                USAGE_EVENTS.c.user_id == user_id,
                USAGE_EVENTS.c.occurred_at >= period_start,
                USAGE_EVENTS.c.plan_code == plan_code,
            )
        ).scalar_one()
        orders = connection.execute(
            select(MANUAL_ORDERS)
            .where(MANUAL_ORDERS.c.user_id == user_id)
            .order_by(MANUAL_ORDERS.c.created_at.desc())
            .limit(10)
        ).all()
    base_remaining = None if plan.minutes is None else max(0, int(plan.minutes) - int(used))
    credit_minutes = int(user.credit_minutes)
    remaining = None if base_remaining is None else base_remaining + credit_minutes
    return {
        "user": _public_user(user),
        "plan": plan.public(),
        "subscription": (
            {
                "status": subscription.status,
                "interval": subscription.interval,
                "starts_at": subscription.starts_at.isoformat(),
                "ends_at": subscription.ends_at.isoformat(),
            }
            if subscription
            else None
        ),
        "used_minutes": int(used),
        "credit_minutes": credit_minutes,
        "remaining_minutes": remaining,
        "can_create_job": remaining is None or remaining > 0,
        "manual_orders": [
            {
                "reference": order.reference,
                "plan_code": order.plan_code,
                "interval": order.interval,
                "amount_minor": order.amount_minor,
                "currency": order.currency,
                "status": order.status,
                "created_at": order.created_at.isoformat(),
            }
            for order in orders
        ],
    }


def require_job_entitlement(user_id: str) -> dict:
    status = account_status(user_id)
    if not status["can_create_job"]:
        raise BillingError("Aylık kullanım hakkın doldu. Yeni bir plan veya dakika paketi seç.")
    return status


def record_usage(user_id: str, job_id: str, duration_seconds: float) -> None:
    minutes = max(1, int(math.ceil(max(0.0, duration_seconds) / 60)))
    now = utcnow()
    init_billing_database()
    try:
        with ENGINE.begin() as connection:
            user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
            if not user:
                return
            subscription = _active_subscription(connection, user_id, now)
            plan_code = subscription.plan_code if subscription else "free"
            connection.execute(
                USAGE_EVENTS.insert().values(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    job_id=job_id,
                    plan_code=plan_code,
                    minutes=minutes,
                    occurred_at=now,
                )
            )
            if not subscription:
                month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
                used = connection.execute(
                    select(func.coalesce(func.sum(USAGE_EVENTS.c.minutes), 0)).where(
                        USAGE_EVENTS.c.user_id == user_id,
                        USAGE_EVENTS.c.plan_code == "free",
                        USAGE_EVENTS.c.occurred_at >= month_start,
                    )
                ).scalar_one()
                overflow = min(minutes, max(0, int(used) - int(PLAN_BY_CODE["free"].minutes or 0)))
                if overflow:
                    connection.execute(
                        update(USERS)
                        .where(USERS.c.id == user_id)
                        .values(credit_minutes=max(0, int(user.credit_minutes) - overflow))
                    )
    except IntegrityError:
        # Job usage is idempotent; a retried completion must not charge twice.
        return


def bank_transfer_available() -> bool:
    return all(
        (
            config.BILLING_BANK_IBAN,
            config.BILLING_BANK_ACCOUNT_HOLDER,
            config.BILLING_SUPPORT_EMAIL,
        )
    )


def create_manual_order(user_id: str, plan_code: str, interval: str) -> dict:
    if not bank_transfer_available():
        raise BillingConfigurationError("Havale ödeme bilgileri henüz etkinleştirilmemiş.")
    plan = PLAN_BY_CODE.get(plan_code)
    if not plan or plan.code in {"free", "business"} or plan.try_amount_minor is None:
        raise BillingError("Bu plan havale ile satın alınamıyor.")
    valid_intervals = {"one_time"} if plan.kind == "one_time" else {"monthly", "annual"}
    if interval not in valid_intervals:
        raise BillingError("Geçersiz ödeme dönemi.")
    amount_minor = plan.try_amount_minor * (10 if interval == "annual" else 1)
    reference = f"LS-{utcnow():%y%m%d}-{secrets.token_hex(3).upper()}"
    now = utcnow()
    init_billing_database()
    with ENGINE.begin() as connection:
        connection.execute(
            MANUAL_ORDERS.insert().values(
                reference=reference,
                user_id=user_id,
                plan_code=plan_code,
                interval=interval,
                amount_minor=amount_minor,
                currency="TRY",
                status="pending",
                created_at=now,
                updated_at=now,
            )
        )
    return {
        "reference": reference,
        "status": "pending",
        "plan_code": plan_code,
        "interval": interval,
        "amount_minor": amount_minor,
        "currency": "TRY",
        "bank": {
            "iban": config.BILLING_BANK_IBAN,
            "account_holder": config.BILLING_BANK_ACCOUNT_HOLDER,
            "bank_name": config.BILLING_BANK_NAME or None,
        },
        "support_email": config.BILLING_SUPPORT_EMAIL,
        "instruction": "Havale açıklamasına yalnızca sipariş referansını yaz.",
    }


def approve_manual_order(reference: str) -> dict:
    init_billing_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        order = connection.execute(
            select(MANUAL_ORDERS).where(MANUAL_ORDERS.c.reference == reference)
        ).first()
        if not order:
            raise BillingError("Sipariş bulunamadı.")
        if order.status != "paid":
            plan = PLAN_BY_CODE[order.plan_code]
            connection.execute(
                update(MANUAL_ORDERS)
                .where(MANUAL_ORDERS.c.reference == reference)
                .values(status="paid", updated_at=now)
            )
            if plan.kind == "one_time":
                connection.execute(
                    update(USERS)
                    .where(USERS.c.id == order.user_id)
                    .values(credit_minutes=USERS.c.credit_minutes + int(plan.minutes or 0))
                )
            else:
                days = 365 if order.interval == "annual" else 30
                connection.execute(
                    SUBSCRIPTIONS.insert().values(
                        id=str(uuid.uuid4()),
                        user_id=order.user_id,
                        plan_code=order.plan_code,
                        interval=order.interval,
                        status="active",
                        starts_at=now,
                        ends_at=now + timedelta(days=days),
                        source_reference=reference,
                        created_at=now,
                    )
                )
    return account_status(order.user_id)
