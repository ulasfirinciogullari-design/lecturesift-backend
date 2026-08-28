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
from .billing import PLAN_BY_CODE, REGIONAL_PRICES


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

USER_PROFILES = Table(
    "billing_user_profiles",
    METADATA,
    Column("user_id", String(36), ForeignKey("billing_users.id"), primary_key=True),
    Column("first_name", String(80), nullable=False),
    Column("last_name", String(80), nullable=False),
    Column("phone", String(32), nullable=True),
    Column("country_code", String(2), nullable=False, default="TR"),
    Column("email_verified_at", DateTime(timezone=True), nullable=True),
    Column("phone_verified_at", DateTime(timezone=True), nullable=True),
    Column("session_version", Integer, nullable=False, default=1),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

USER_PREFERENCES = Table(
    "billing_user_preferences",
    METADATA,
    Column("user_id", String(36), ForeignKey("billing_users.id"), primary_key=True),
    Column("preferred_language", String(8), nullable=False, default="tr"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

AUTH_TOKENS = Table(
    "billing_auth_tokens",
    METADATA,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("purpose", String(32), nullable=False, index=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
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

PAYMENT_ORDERS = Table(
    "billing_payment_orders",
    METADATA,
    Column("reference", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("provider", String(24), nullable=False),
    Column("plan_code", String(32), nullable=False),
    Column("interval", String(16), nullable=False),
    Column("amount_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String(24), nullable=False),
    Column("provider_amount_minor", Integer, nullable=True),
    Column("failure_code", String(32), nullable=True),
    Column("failure_message", String(240), nullable=True),
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


def _profile_for(connection, user_id: str):
    return connection.execute(
        select(USER_PROFILES).where(USER_PROFILES.c.user_id == user_id)
    ).first()


def _public_user(row, profile=None, preference=None) -> dict:
    # Accounts created before verification was introduced have no profile row;
    # keep those existing accounts usable and treat them as verified.
    first_name = profile.first_name if profile else ""
    last_name = profile.last_name if profile else ""
    return {
        "id": row.id,
        "email": row.email,
        "first_name": first_name,
        "last_name": last_name,
        "name": " ".join(part for part in (first_name, last_name) if part),
        "phone": profile.phone if profile else None,
        "country_code": profile.country_code if profile else None,
        "email_verified": profile.email_verified_at is not None if profile else True,
        "phone_verified": profile.phone_verified_at is not None if profile else False,
        "preferred_language": preference.preferred_language if preference else "tr",
    }


def _normalize_name(value: str, label: str) -> str:
    normalized = " ".join(value.strip().split())
    if len(normalized) < 2 or len(normalized) > 80:
        raise BillingError(f"{label} 2 ile 80 karakter arasında olmalı.")
    return normalized


def _normalize_phone(value: str) -> str | None:
    normalized = "".join(char for char in value.strip() if char.isdigit() or char == "+")
    if not normalized:
        return None
    if normalized.count("+") > 1 or ("+" in normalized and not normalized.startswith("+")):
        raise BillingError("Geçerli bir telefon numarası gir.")
    digit_count = sum(char.isdigit() for char in normalized)
    if digit_count < 7 or digit_count > 15:
        raise BillingError("Telefon numarası 7 ile 15 rakam arasında olmalı.")
    return normalized[:32]


def _validate_password(password: str) -> None:
    if len(password) < 10 or not any(char.isupper() for char in password) or not any(char.islower() for char in password) or not any(char.isdigit() for char in password):
        raise BillingError("Parola en az 10 karakter, bir büyük harf, bir küçük harf ve bir rakam içermeli.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _store_auth_token(
    connection,
    user_id: str,
    purpose: str,
    token: str,
    expires_at: datetime,
) -> None:
    connection.execute(
        AUTH_TOKENS.insert().values(
            token_hash=_token_hash(token),
            user_id=user_id,
            purpose=purpose,
            expires_at=expires_at,
            consumed_at=None,
            created_at=utcnow(),
        )
    )


def _create_auth_token(connection, user_id: str, purpose: str, ttl: timedelta) -> tuple[str, datetime]:
    now = utcnow()
    connection.execute(
        update(AUTH_TOKENS)
        .where(
            AUTH_TOKENS.c.user_id == user_id,
            AUTH_TOKENS.c.purpose == purpose,
            AUTH_TOKENS.c.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    token = secrets.token_urlsafe(32)
    expires_at = now + ttl
    _store_auth_token(connection, user_id, purpose, token, expires_at)
    return token, expires_at


def register_user(
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    phone: str = "",
    country_code: str = "TR",
) -> dict:
    init_billing_database()
    normalized = email.strip().casefold()
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1] or len(normalized) > 320:
        raise BillingError("Geçerli bir e-posta adresi gir.")
    _validate_password(password)
    selected_first_name = _normalize_name(first_name, "Ad")
    selected_last_name = _normalize_name(last_name, "Soyad")
    selected_phone = _normalize_phone(phone)
    selected_country = country_code.strip().upper()[:2] if len(country_code.strip()) >= 2 else "TR"
    salt = secrets.token_bytes(16)
    now = utcnow()
    values = {
        "id": str(uuid.uuid4()),
        "email": normalized,
        "password_salt": salt.hex(),
        "password_hash": _hash_password(password, salt),
        "credit_minutes": 0,
        "created_at": now,
    }
    try:
        with ENGINE.begin() as connection:
            connection.execute(USERS.insert().values(**values))
            connection.execute(
                USER_PROFILES.insert().values(
                    user_id=values["id"],
                    first_name=selected_first_name,
                    last_name=selected_last_name,
                    phone=selected_phone,
                    country_code=selected_country,
                    email_verified_at=None,
                    phone_verified_at=None,
                    session_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            verification_token, expires_at = _create_auth_token(
                connection, values["id"], "verify_email", timedelta(hours=24)
            )
            verification_code = f"{secrets.randbelow(1_000_000):06d}"
            _store_auth_token(
                connection,
                values["id"],
                "verify_email_code",
                verification_code,
                expires_at,
            )
    except IntegrityError as exc:
        raise BillingError("Bu e-posta adresiyle daha önce hesap oluşturulmuş.") from exc
    user = {
        "id": values["id"],
        "email": normalized,
        "first_name": selected_first_name,
        "last_name": selected_last_name,
        "name": f"{selected_first_name} {selected_last_name}",
        "phone": selected_phone,
        "country_code": selected_country,
        "email_verified": False,
        "phone_verified": False,
    }
    return {
        "user": user,
        "verification_token": verification_token,
        "verification_code": verification_code,
        "expires_at": expires_at,
    }


def login_user(email: str, password: str) -> dict:
    init_billing_database()
    normalized = email.strip().casefold()
    with ENGINE.connect() as connection:
        row = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
        profile = _profile_for(connection, row.id) if row else None
    if not row:
        raise BillingAuthenticationError("E-posta veya parola hatalı.")
    candidate = _hash_password(password, bytes.fromhex(row.password_salt))
    if not hmac.compare_digest(candidate, row.password_hash):
        raise BillingAuthenticationError("E-posta veya parola hatalı.")
    if profile and profile.email_verified_at is None:
        raise BillingAuthenticationError("E-posta adresini doğruladıktan sonra giriş yapabilirsin.")
    session_version = int(profile.session_version) if profile else 1
    return {
        "user": _public_user(row, profile),
        "token": issue_session(row.id, row.email, session_version),
    }


def _token_secret() -> bytes:
    explicit = config.BILLING_SESSION_SECRET.encode("utf-8")
    if explicit:
        return explicit
    return hashlib.sha256((config.DATABASE_URL + "|lecturesift-session-v1").encode("utf-8")).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_session(user_id: str, email: str, session_version: int = 1) -> str:
    payload = json.dumps(
        {
            "sub": user_id,
            "email": email,
            "ver": session_version,
            "exp": int((utcnow() + timedelta(days=14)).timestamp()),
        },
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
        profile = _profile_for(connection, user_id) if row else None
    if not row:
        raise BillingAuthenticationError("Hesap bulunamadı.")
    if profile and int(payload.get("ver", 1)) != int(profile.session_version):
        raise BillingAuthenticationError("Oturum geçersiz veya süresi dolmuş.")
    if profile and profile.email_verified_at is None:
        raise BillingAuthenticationError("E-posta doğrulaması gerekiyor.")
    return _public_user(row, profile)


def _token_is_expired(value: datetime) -> bool:
    candidate = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return candidate <= utcnow()


def verify_email(token: str) -> dict:
    init_billing_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        auth_token = connection.execute(
            select(AUTH_TOKENS).where(
                AUTH_TOKENS.c.token_hash == _token_hash(token),
                AUTH_TOKENS.c.purpose == "verify_email",
            )
        ).first()
        if not auth_token or auth_token.consumed_at is not None or _token_is_expired(auth_token.expires_at):
            raise BillingAuthenticationError("Doğrulama bağlantısı geçersiz veya süresi dolmuş.")
        connection.execute(
            update(AUTH_TOKENS)
            .where(
                AUTH_TOKENS.c.user_id == auth_token.user_id,
                AUTH_TOKENS.c.purpose.in_(("verify_email", "verify_email_code")),
                AUTH_TOKENS.c.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == auth_token.user_id)
            .values(email_verified_at=now, updated_at=now)
        )
        user = connection.execute(select(USERS).where(USERS.c.id == auth_token.user_id)).first()
        profile = _profile_for(connection, auth_token.user_id)
    if not user or not profile:
        raise BillingAuthenticationError("Hesap bulunamadı.")
    return {
        "user": _public_user(user, profile),
        "token": issue_session(user.id, user.email, int(profile.session_version)),
    }


def verify_email_code(email: str, code: str) -> dict:
    init_billing_database()
    normalized = email.strip().casefold()
    normalized_code = "".join(char for char in code if char.isdigit())
    if len(normalized_code) != 6:
        raise BillingAuthenticationError("Altı haneli doğrulama kodunu gir.")
    now = utcnow()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
        auth_token = (
            connection.execute(
                select(AUTH_TOKENS).where(
                    AUTH_TOKENS.c.user_id == user.id,
                    AUTH_TOKENS.c.token_hash == _token_hash(normalized_code),
                    AUTH_TOKENS.c.purpose == "verify_email_code",
                )
            ).first()
            if user
            else None
        )
        if not user or not auth_token or auth_token.consumed_at is not None or _token_is_expired(auth_token.expires_at):
            raise BillingAuthenticationError("Doğrulama kodu geçersiz veya süresi dolmuş.")
        profile = _profile_for(connection, user.id)
        if not profile or profile.email_verified_at is not None:
            raise BillingAuthenticationError("Bu e-posta adresi zaten doğrulanmış.")
        connection.execute(
            update(AUTH_TOKENS)
            .where(
                AUTH_TOKENS.c.user_id == user.id,
                AUTH_TOKENS.c.purpose.in_(("verify_email", "verify_email_code")),
                AUTH_TOKENS.c.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user.id)
            .values(email_verified_at=now, updated_at=now)
        )
        profile = _profile_for(connection, user.id)
    return {
        "user": _public_user(user, profile),
        "token": issue_session(user.id, user.email, int(profile.session_version)),
    }


def create_verification_token(email: str) -> dict | None:
    init_billing_database()
    normalized = email.strip().casefold()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
        if not user:
            return None
        profile = _profile_for(connection, user.id)
        if not profile or profile.email_verified_at is not None:
            return None
        token, expires_at = _create_auth_token(connection, user.id, "verify_email", timedelta(hours=24))
        code = f"{secrets.randbelow(1_000_000):06d}"
        connection.execute(
            update(AUTH_TOKENS)
            .where(
                AUTH_TOKENS.c.user_id == user.id,
                AUTH_TOKENS.c.purpose == "verify_email_code",
                AUTH_TOKENS.c.consumed_at.is_(None),
            )
            .values(consumed_at=utcnow())
        )
        _store_auth_token(connection, user.id, "verify_email_code", code, expires_at)
    return {"email": user.email, "token": token, "code": code, "expires_at": expires_at}


def create_password_reset_token(email: str) -> dict | None:
    init_billing_database()
    normalized = email.strip().casefold()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
        if not user:
            return None
        profile = _profile_for(connection, user.id)
        if profile and profile.email_verified_at is None:
            return None
        token, expires_at = _create_auth_token(connection, user.id, "reset_password", timedelta(minutes=45))
    return {"email": user.email, "token": token, "expires_at": expires_at}


def reset_password(token: str, new_password: str) -> None:
    _validate_password(new_password)
    init_billing_database()
    now = utcnow()
    salt = secrets.token_bytes(16)
    with ENGINE.begin() as connection:
        auth_token = connection.execute(
            select(AUTH_TOKENS).where(
                AUTH_TOKENS.c.token_hash == _token_hash(token),
                AUTH_TOKENS.c.purpose == "reset_password",
            )
        ).first()
        if not auth_token or auth_token.consumed_at is not None or _token_is_expired(auth_token.expires_at):
            raise BillingAuthenticationError("Şifre yenileme bağlantısı geçersiz veya süresi dolmuş.")
        connection.execute(
            update(USERS)
            .where(USERS.c.id == auth_token.user_id)
            .values(password_salt=salt.hex(), password_hash=_hash_password(new_password, salt))
        )
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == auth_token.user_id)
            .values(session_version=USER_PROFILES.c.session_version + 1, updated_at=now)
        )
        connection.execute(
            update(AUTH_TOKENS)
            .where(AUTH_TOKENS.c.token_hash == auth_token.token_hash)
            .values(consumed_at=now)
        )


def logout_user(user_id: str) -> None:
    """Invalidate all active sessions issued for a verified account."""
    init_billing_database()
    with ENGINE.begin() as connection:
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user_id)
            .values(session_version=USER_PROFILES.c.session_version + 1, updated_at=utcnow())
        )


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


def _public_manual_order(order) -> dict:
    return {
        "reference": order.reference,
        "order_number": order.reference,
        "plan_code": order.plan_code,
        "interval": order.interval,
        "amount_minor": order.amount_minor,
        "currency": order.currency,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
        "bank": (
            {
                "iban": config.BILLING_BANK_IBAN,
                "account_holder": config.BILLING_BANK_ACCOUNT_HOLDER,
                "bank_name": config.BILLING_BANK_NAME or None,
            }
            if order.status == "pending" and bank_transfer_available()
            else None
        ),
    }


def _public_payment_order(order) -> dict:
    return {
        "reference": order.reference,
        "order_number": order.reference,
        "provider": order.provider,
        "plan_code": order.plan_code,
        "interval": order.interval,
        "amount_minor": order.amount_minor,
        "provider_amount_minor": order.provider_amount_minor,
        "currency": order.currency,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
    }


def account_status(user_id: str) -> dict:
    init_billing_database()
    now = utcnow()
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    with ENGINE.connect() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        if not user:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        profile = _profile_for(connection, user_id)
        preference = connection.execute(
            select(USER_PREFERENCES).where(USER_PREFERENCES.c.user_id == user_id)
        ).first()
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
        payment_orders = connection.execute(
            select(PAYMENT_ORDERS)
            .where(PAYMENT_ORDERS.c.user_id == user_id)
            .order_by(PAYMENT_ORDERS.c.created_at.desc())
            .limit(10)
        ).all()
    base_remaining = None if plan.minutes is None else max(0, int(plan.minutes) - int(used))
    credit_minutes = int(user.credit_minutes)
    remaining = None if base_remaining is None else base_remaining + credit_minutes
    return {
        "user": _public_user(user, profile, preference),
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
        "manual_orders": [_public_manual_order(order) for order in orders],
        "payment_orders": [_public_payment_order(order) for order in payment_orders],
    }


def update_account_preferences(user_id: str, country_code: str, preferred_language: str) -> dict:
    country = country_code.strip().upper()
    language = preferred_language.strip().lower().replace("_", "-")
    supported_languages = {"tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"}
    if len(country) != 2 or not country.isalpha():
        raise BillingError("Geçerli bir ülke seç.")
    if language not in supported_languages:
        raise BillingError("Seçilen arayüz dili desteklenmiyor.")
    init_billing_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        profile = _profile_for(connection, user_id)
        if not profile:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user_id)
            .values(country_code=country, updated_at=now)
        )
        preference = connection.execute(
            select(USER_PREFERENCES).where(USER_PREFERENCES.c.user_id == user_id)
        ).first()
        if preference:
            connection.execute(
                update(USER_PREFERENCES)
                .where(USER_PREFERENCES.c.user_id == user_id)
                .values(preferred_language=language, updated_at=now)
            )
        else:
            connection.execute(
                USER_PREFERENCES.insert().values(
                    user_id=user_id,
                    preferred_language=language,
                    updated_at=now,
                )
            )
    return account_status(user_id)


def update_account_profile(
    user_id: str,
    first_name: str,
    last_name: str,
    phone: str = "",
) -> dict:
    selected_first_name = _normalize_name(first_name, "Ad")
    selected_last_name = _normalize_name(last_name, "Soyad")
    selected_phone = _normalize_phone(phone)
    init_billing_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        profile = _profile_for(connection, user_id)
        if not profile:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user_id)
            .values(
                first_name=selected_first_name,
                last_name=selected_last_name,
                phone=selected_phone,
                phone_verified_at=(
                    profile.phone_verified_at if selected_phone == profile.phone else None
                ),
                updated_at=now,
            )
        )
    return account_status(user_id)


def change_account_password(
    user_id: str,
    current_password: str,
    new_password: str,
) -> dict:
    _validate_password(new_password)
    init_billing_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        profile = _profile_for(connection, user_id) if user else None
        if not user or not profile:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        candidate = _hash_password(current_password, bytes.fromhex(user.password_salt))
        if not hmac.compare_digest(candidate, user.password_hash):
            raise BillingAuthenticationError("Mevcut parola hatalı.")
        salt = secrets.token_bytes(16)
        next_session_version = int(profile.session_version) + 1
        connection.execute(
            update(USERS)
            .where(USERS.c.id == user_id)
            .values(password_salt=salt.hex(), password_hash=_hash_password(new_password, salt))
        )
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user_id)
            .values(session_version=next_session_version, updated_at=now)
        )
    return {
        "token": issue_session(user.id, user.email, next_session_version),
        "account": account_status(user_id),
    }


def require_job_entitlement(user_id: str) -> dict:
    status = account_status(user_id)
    if not status["can_create_job"]:
        raise BillingError("Aylık kullanım hakkın doldu. Yeni bir plan veya dakika paketi seç.")
    return status


def require_duration_entitlement(user_id: str, duration_seconds: float) -> dict:
    """Reject media that cannot fit in the user's currently available minutes."""
    status = require_job_entitlement(user_id)
    required_minutes = max(1, int(math.ceil(max(0.0, duration_seconds) / 60)))
    remaining = status["remaining_minutes"]
    if remaining is not None and required_minutes > int(remaining):
        raise BillingError(
            f"Bu kaynak yaklaşık {required_minutes} dakika; hesabında {int(remaining)} dakika kaldı. "
            "Daha kısa bir kaynak yükle veya dakika hakkını artır."
        )
    return status


def validate_job_features(
    user_id: str,
    *,
    quiz_count: int,
    flashcard_count: int,
    output_formats: list[str],
    summary_style: str,
) -> dict:
    status = require_job_entitlement(user_id)
    plan = PLAN_BY_CODE[status["plan"]["code"]]
    if plan.quiz_questions is not None and quiz_count > plan.quiz_questions:
        raise BillingError(f"Planın en fazla {plan.quiz_questions} quiz sorusuna izin veriyor.")
    if plan.flashcards is not None and flashcard_count > plan.flashcards:
        raise BillingError(f"Planın en fazla {plan.flashcards} bilgi kartına izin veriyor.")
    unsupported_formats = set(output_formats) - set(plan.export_formats)
    if unsupported_formats:
        raise BillingError("Seçtiğin çıktı biçimlerinden biri mevcut planına dahil değil.")
    if summary_style not in plan.summary_profiles:
        raise BillingError("Seçtiğin özet profili mevcut planına dahil değil.")
    return status


def record_usage(user_id: str, job_id: str, duration_seconds: float) -> None:
    minutes = max(1, int(math.ceil(max(0.0, duration_seconds) / 60)))
    now = utcnow()
    init_billing_database()
    try:
        with ENGINE.begin() as connection:
            user = connection.execute(
                select(USERS).where(USERS.c.id == user_id).with_for_update()
            ).first()
            if not user:
                return
            subscription = _active_subscription(connection, user_id, now)
            plan_code = subscription.plan_code if subscription else "free"
            plan = PLAN_BY_CODE[plan_code]
            period_start = (
                subscription.starts_at
                if subscription
                else datetime(now.year, now.month, 1, tzinfo=timezone.utc)
            )
            used_before = connection.execute(
                select(func.coalesce(func.sum(USAGE_EVENTS.c.minutes), 0)).where(
                    USAGE_EVENTS.c.user_id == user_id,
                    USAGE_EVENTS.c.plan_code == plan_code,
                    USAGE_EVENTS.c.occurred_at >= period_start,
                )
            ).scalar_one()
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
            if plan.minutes is not None and user.credit_minutes:
                plan_minutes_left = max(0, int(plan.minutes) - int(used_before))
                credit_spend = min(int(user.credit_minutes), max(0, minutes - plan_minutes_left))
                if credit_spend:
                    connection.execute(
                        update(USERS)
                        .where(USERS.c.id == user_id)
                        .values(credit_minutes=max(0, int(user.credit_minutes) - credit_spend))
                    )
    except IntegrityError:
        # Job usage is idempotent; a retried completion must not charge twice.
        return


def _activate_purchase(
    connection,
    *,
    user_id: str,
    plan_code: str,
    interval: str,
    reference: str,
    now: datetime,
) -> None:
    plan = PLAN_BY_CODE[plan_code]
    if plan.kind == "one_time":
        connection.execute(
            update(USERS)
            .where(USERS.c.id == user_id)
            .values(credit_minutes=USERS.c.credit_minutes + int(plan.minutes or 0))
        )
        return
    connection.execute(
        update(SUBSCRIPTIONS)
        .where(
            SUBSCRIPTIONS.c.user_id == user_id,
            SUBSCRIPTIONS.c.status == "active",
        )
        .values(status="replaced")
    )
    days = 365 if interval == "annual" else 30
    connection.execute(
        SUBSCRIPTIONS.insert().values(
            id=str(uuid.uuid4()),
            user_id=user_id,
            plan_code=plan_code,
            interval=interval,
            status="active",
            starts_at=now,
            ends_at=now + timedelta(days=days),
            source_reference=reference,
            created_at=now,
        )
    )


def create_payment_order(
    user_id: str,
    provider: str,
    plan_code: str,
    interval: str,
    currency: str,
) -> dict:
    selected_provider = provider.strip().lower()
    selected_currency = currency.strip().upper()
    plan = PLAN_BY_CODE.get(plan_code)
    if not plan or plan.code in {"free", "business"}:
        raise BillingError("Bu plan çevrimiçi ödeme ile satın alınamıyor.")
    valid_intervals = {"one_time"} if plan.kind == "one_time" else {"monthly", "annual"}
    if interval not in valid_intervals:
        raise BillingError("Geçersiz ödeme dönemi.")
    amount_minor = REGIONAL_PRICES.get(plan_code, {}).get(selected_currency)
    if amount_minor is None:
        raise BillingError("Bu para birimi çevrimiçi ödeme için desteklenmiyor.")
    if interval == "annual":
        amount_minor *= 10
    if not selected_provider or len(selected_provider) > 24:
        raise BillingError("Geçersiz ödeme sağlayıcısı.")
    reference = f"LS{utcnow():%Y%m%d}{secrets.token_hex(6).upper()}"
    now = utcnow()
    init_billing_database()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS.c.id).where(USERS.c.id == user_id)).first()
        if not user:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        connection.execute(
            PAYMENT_ORDERS.insert().values(
                reference=reference,
                user_id=user_id,
                provider=selected_provider,
                plan_code=plan_code,
                interval=interval,
                amount_minor=int(amount_minor),
                currency=selected_currency,
                status="created",
                provider_amount_minor=None,
                failure_code=None,
                failure_message=None,
                created_at=now,
                updated_at=now,
            )
        )
    return payment_order(reference)


def payment_order(reference: str) -> dict:
    init_billing_database()
    with ENGINE.connect() as connection:
        order = connection.execute(
            select(PAYMENT_ORDERS).where(PAYMENT_ORDERS.c.reference == reference)
        ).first()
    if not order:
        raise BillingError("Ödeme siparişi bulunamadı.")
    return _public_payment_order(order)


def mark_payment_order_token_failed(reference: str) -> None:
    init_billing_database()
    with ENGINE.begin() as connection:
        connection.execute(
            update(PAYMENT_ORDERS)
            .where(
                PAYMENT_ORDERS.c.reference == reference,
                PAYMENT_ORDERS.c.status == "created",
            )
            .values(status="token_failed", updated_at=utcnow())
        )


def complete_payment_order(
    reference: str,
    *,
    succeeded: bool,
    provider_amount_minor: int,
    failure_code: str = "",
    failure_message: str = "",
) -> dict:
    init_billing_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        order = connection.execute(
            select(PAYMENT_ORDERS)
            .where(PAYMENT_ORDERS.c.reference == reference)
            .with_for_update()
        ).first()
        if not order:
            raise BillingError("Ödeme siparişi bulunamadı.")
        if order.status in {"paid", "failed"}:
            return _public_payment_order(order)
        next_status = "paid" if succeeded else "failed"
        connection.execute(
            update(PAYMENT_ORDERS)
            .where(PAYMENT_ORDERS.c.reference == reference)
            .values(
                status=next_status,
                provider_amount_minor=max(0, int(provider_amount_minor)),
                failure_code=(failure_code or "")[:32] or None,
                failure_message=(failure_message or "")[:240] or None,
                updated_at=now,
            )
        )
        if succeeded:
            _activate_purchase(
                connection,
                user_id=order.user_id,
                plan_code=order.plan_code,
                interval=order.interval,
                reference=reference,
                now=now,
            )
    return payment_order(reference)


def bank_transfer_available() -> bool:
    return all(
        (
            config.BILLING_BANK_IBAN,
            config.BILLING_BANK_ACCOUNT_HOLDER,
            config.BILLING_SUPPORT_EMAIL,
        )
    )


def manual_transfer_details() -> dict:
    available = bank_transfer_available()
    return {
        "available": available,
        "requires_account": True,
        "activation": "manual_after_bank_confirmation",
        "bank": (
            {
                "iban": config.BILLING_BANK_IBAN,
                "account_holder": config.BILLING_BANK_ACCOUNT_HOLDER,
                "bank_name": config.BILLING_BANK_NAME or None,
            }
            if available
            else None
        ),
        "support_email": config.BILLING_SUPPORT_EMAIL or None,
    }


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
    reference = f"LS-{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"
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
        "order_number": reference,
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


def admin_billing_overview(limit: int = 100) -> dict:
    init_billing_database()
    safe_limit = max(1, min(int(limit), 250))
    now = utcnow()
    with ENGINE.connect() as connection:
        user_count = int(connection.execute(select(func.count()).select_from(USERS)).scalar_one())
        verified_count = int(
            connection.execute(
                select(func.count()).select_from(USER_PROFILES).where(
                    USER_PROFILES.c.email_verified_at.is_not(None)
                )
            ).scalar_one()
        )
        pending_count = int(
            connection.execute(
                select(func.count()).select_from(MANUAL_ORDERS).where(
                    MANUAL_ORDERS.c.status == "pending"
                )
            ).scalar_one()
        )
        pending_count += int(
            connection.execute(
                select(func.count()).select_from(PAYMENT_ORDERS).where(
                    PAYMENT_ORDERS.c.status == "created"
                )
            ).scalar_one()
        )
        active_subscription_count = int(
            connection.execute(
                select(func.count()).select_from(SUBSCRIPTIONS).where(
                    SUBSCRIPTIONS.c.status == "active",
                    SUBSCRIPTIONS.c.ends_at > now,
                )
            ).scalar_one()
        )
        order_rows = connection.execute(
            select(
                MANUAL_ORDERS,
                USERS.c.email.label("user_email"),
                USER_PROFILES.c.first_name.label("first_name"),
                USER_PROFILES.c.last_name.label("last_name"),
            )
            .join(USERS, USERS.c.id == MANUAL_ORDERS.c.user_id)
            .outerjoin(USER_PROFILES, USER_PROFILES.c.user_id == USERS.c.id)
            .order_by(MANUAL_ORDERS.c.created_at.desc())
            .limit(safe_limit)
        ).all()
        payment_order_rows = connection.execute(
            select(
                PAYMENT_ORDERS,
                USERS.c.email.label("user_email"),
                USER_PROFILES.c.first_name.label("first_name"),
                USER_PROFILES.c.last_name.label("last_name"),
            )
            .join(USERS, USERS.c.id == PAYMENT_ORDERS.c.user_id)
            .outerjoin(USER_PROFILES, USER_PROFILES.c.user_id == USERS.c.id)
            .order_by(PAYMENT_ORDERS.c.created_at.desc())
            .limit(safe_limit)
        ).all()
        user_rows = connection.execute(
            select(
                USERS.c.id,
                USERS.c.email,
                USERS.c.credit_minutes,
                USERS.c.created_at,
                USER_PROFILES.c.first_name,
                USER_PROFILES.c.last_name,
                USER_PROFILES.c.phone,
                USER_PROFILES.c.country_code,
                USER_PROFILES.c.email_verified_at,
            )
            .outerjoin(USER_PROFILES, USER_PROFILES.c.user_id == USERS.c.id)
            .order_by(USERS.c.created_at.desc())
            .limit(safe_limit)
        ).all()
    return {
        "counts": {
            "users": user_count,
            "verified_users": verified_count,
            "pending_orders": pending_count,
            "active_subscriptions": active_subscription_count,
        },
        "orders": sorted([
            {
                **_public_manual_order(row),
                "user": {
                    "email": row.user_email,
                    "name": " ".join(
                        part for part in (row.first_name, row.last_name) if part
                    ),
                },
            }
            for row in order_rows
        ] + [
            {
                **_public_payment_order(row),
                "user": {
                    "email": row.user_email,
                    "name": " ".join(
                        part for part in (row.first_name, row.last_name) if part
                    ),
                },
            }
            for row in payment_order_rows
        ], key=lambda item: item["created_at"], reverse=True)[:safe_limit],
        "users": [
            {
                "id": row.id,
                "email": row.email,
                "name": " ".join(part for part in (row.first_name, row.last_name) if part),
                "phone": row.phone,
                "country_code": row.country_code,
                "email_verified": row.email_verified_at is not None,
                "credit_minutes": int(row.credit_minutes),
                "created_at": row.created_at.isoformat(),
            }
            for row in user_rows
        ],
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
            connection.execute(
                update(MANUAL_ORDERS)
                .where(MANUAL_ORDERS.c.reference == reference)
                .values(status="paid", updated_at=now)
            )
            _activate_purchase(
                connection,
                user_id=order.user_id,
                plan_code=order.plan_code,
                interval=order.interval,
                reference=reference,
                now=now,
            )
    return account_status(order.user_id)
