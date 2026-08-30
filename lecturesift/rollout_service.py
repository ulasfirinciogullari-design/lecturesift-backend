"""Persistent product features layered on top of LectureSift billing.

This module intentionally reuses the existing billing database and session
system. No banking, admin, or email credentials are stored in source control.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import ipaddress
import math
import secrets
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Table, and_, delete, exists, func, literal, or_, select, union_all, update
from sqlalchemy.exc import IntegrityError

from . import config
from .billing import PLAN_BY_CODE, Plan
from .billing_service import (
    ENGINE,
    AUTH_TOKENS,
    MANUAL_ORDERS,
    METADATA,
    PAYMENT_ORDERS,
    PAYMENT_CONSENTS,
    SUBSCRIPTIONS,
    USAGE_EVENTS,
    USERS,
    USER_PREFERENCES,
    USER_PROFILES,
    BillingAuthenticationError,
    BillingConfigurationError,
    BillingError,
    _hash_password,
    account_status,
    approve_manual_order,
    reject_manual_order,
    init_billing_database,
    issue_session,
    utcnow,
)
from .mailer import EmailDeliveryError, email_delivery_configured, send_transactional_email


GUEST_TRIALS = Table(
    "lecturesift_guest_trials",
    METADATA,
    Column("fingerprint_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, unique=True),
    Column("job_id", String(64), nullable=True, unique=True),
    Column("media_minutes", Float, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
)

INSTAGRAM_REWARDS = Table(
    "lecturesift_instagram_rewards",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, unique=True),
    Column("handle", String(100), nullable=False, unique=True),
    Column("status", String(24), nullable=False),
    Column("minutes", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

REWARDED_AD_CLAIMS = Table(
    "lecturesift_rewarded_ad_claims",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("status", String(16), nullable=False),
    Column("minutes", Integer, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("redeemed_at", DateTime(timezone=True), nullable=True),
)

EMAIL_CHANGE_REQUESTS = Table(
    "lecturesift_email_change_requests",
    METADATA,
    Column("user_id", String(36), ForeignKey("billing_users.id"), primary_key=True),
    Column("new_email", String(320), nullable=False, unique=True),
    Column("code_hash", String(64), nullable=False),
    Column("token_hash", String(64), nullable=False),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

RUNTIME_METRICS = Table(
    "lecturesift_runtime_metrics",
    METADATA,
    Column("job_id", String(64), primary_key=True),
    Column("media_minutes", Float, nullable=False),
    Column("elapsed_seconds", Float, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

ADMIN_CREDIT_EVENTS = Table(
    "lecturesift_admin_credit_events",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("minutes_delta", Integer, nullable=False),
    Column("balance_before", Integer, nullable=False),
    Column("balance_after", Integer, nullable=False),
    Column("reason", String(240), nullable=False),
    Column("actor", String(80), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

ADMIN_ACCOUNT_EVENTS = Table(
    "lecturesift_admin_account_events",
    METADATA,
    Column("id", String(36), primary_key=True),
    # Intentionally not a foreign key: the audit record must survive account
    # anonymisation so an operator can explain privileged changes later.
    Column("subject_user_id", String(36), nullable=False, index=True),
    Column("subject_email", String(320), nullable=False),
    Column("action", String(48), nullable=False, index=True),
    Column("summary", String(500), nullable=False),
    Column("actor", String(80), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

ACCOUNT_ACTIVITY = Table(
    "lecturesift_account_activity",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("event_type", String(32), nullable=False, index=True),
    # Only a salted fingerprint and a masked network are retained. Full IP
    # addresses are deliberately not persisted in the application database.
    Column("ip_hash", String(64), nullable=False),
    Column("ip_network", String(64), nullable=False),
    Column("user_agent", String(240), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
)

REFUND_REQUESTS = Table(
    "lecturesift_refund_requests",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=False, index=True),
    Column("order_reference", String(64), nullable=False, index=True),
    Column("provider", String(24), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("status", String(32), nullable=False),
    Column("admin_note", String(500), nullable=True),
    Column("reviewed_by", String(80), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

CONTACT_MESSAGES = Table(
    "lecturesift_contact_messages",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("email", String(320), nullable=False, index=True),
    Column("topic", String(100), nullable=False),
    Column("message", String(4000), nullable=False),
    Column("order_reference", String(64), nullable=True),
    Column("status", String(20), nullable=False),
    Column("email_notified", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


# Guest accounts must never fall back to the 60-minute registered free plan.
# They receive a hidden, persistent five-minute plan instead.
PLAN_BY_CODE.setdefault(
    "guest",
    Plan(
        "guest",
        "guest",
        max(1, int(round(config.GUEST_TRIAL_MAX_MINUTES))),
        ("pdf",),
        "standard",
        1,
        10,
        20,
        ("short", "standard"),
        1,
        max_files_per_job=1,
        max_media_upload_mb=25,
        max_document_upload_mb=10,
        max_minutes_per_job=max(1, int(round(config.GUEST_TRIAL_MAX_MINUTES))),
        max_document_pages=10,
        max_ocr_pages=5,
    ),
)

CONTACT_REPLIES = Table(
    "lecturesift_contact_replies",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column(
        "contact_message_id",
        String(36),
        ForeignKey("lecturesift_contact_messages.id"),
        nullable=False,
        index=True,
    ),
    Column("direction", String(12), nullable=False),
    Column("body", String(4000), nullable=False),
    Column("sender", String(320), nullable=False),
    Column("delivery_status", String(20), nullable=False),
    Column("provider_message_id", String(180), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("sent_at", DateTime(timezone=True), nullable=True),
)


def init_rollout_database() -> None:
    init_billing_database()
    for table in (
        GUEST_TRIALS,
        INSTAGRAM_REWARDS,
        REWARDED_AD_CLAIMS,
        EMAIL_CHANGE_REQUESTS,
        RUNTIME_METRICS,
        ADMIN_CREDIT_EVENTS,
        ADMIN_ACCOUNT_EVENTS,
        ACCOUNT_ACTIVITY,
        REFUND_REQUESTS,
        CONTACT_MESSAGES,
        CONTACT_REPLIES,
    ):
        table.create(bind=ENGINE, checkfirst=True)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _normalize_email(value: str) -> str:
    email = value.strip().casefold()
    local, separator, domain = email.partition("@")
    if not separator or email.count("@") != 1 or not local or not domain or "." not in domain or len(email) > 320:
        raise BillingError("Geçerli bir e-posta adresi gir.")
    return email


def _normalize_name(value: str, label: str, *, optional: bool = False) -> str:
    normalized = " ".join(value.strip().split())
    if optional and not normalized:
        return ""
    if len(normalized) < 2 or len(normalized) > 80:
        raise BillingError(f"{label} 2 ile 80 karakter arasında olmalı.")
    return normalized


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def export_account_data(user_id: str) -> dict[str, Any]:
    """Return a portable, secret-free snapshot of one user's stored account data."""
    init_rollout_database()
    account = account_status(user_id)
    with ENGINE.connect() as connection:
        subscriptions = connection.execute(
            select(SUBSCRIPTIONS)
            .where(SUBSCRIPTIONS.c.user_id == user_id)
            .order_by(SUBSCRIPTIONS.c.created_at.desc())
        ).all()
        usage = connection.execute(
            select(USAGE_EVENTS)
            .where(USAGE_EVENTS.c.user_id == user_id)
            .order_by(USAGE_EVENTS.c.occurred_at.desc())
        ).all()
        reward = connection.execute(
            select(INSTAGRAM_REWARDS).where(INSTAGRAM_REWARDS.c.user_id == user_id)
        ).first()
        rewarded_claims = connection.execute(
            select(
                REWARDED_AD_CLAIMS.c.id,
                REWARDED_AD_CLAIMS.c.status,
                REWARDED_AD_CLAIMS.c.minutes,
                REWARDED_AD_CLAIMS.c.created_at,
                REWARDED_AD_CLAIMS.c.redeemed_at,
            )
            .where(REWARDED_AD_CLAIMS.c.user_id == user_id)
            .order_by(REWARDED_AD_CLAIMS.c.created_at.desc())
        ).all()
        credit_events = connection.execute(
            select(
                ADMIN_CREDIT_EVENTS.c.id,
                ADMIN_CREDIT_EVENTS.c.minutes_delta,
                ADMIN_CREDIT_EVENTS.c.balance_before,
                ADMIN_CREDIT_EVENTS.c.balance_after,
                ADMIN_CREDIT_EVENTS.c.reason,
                ADMIN_CREDIT_EVENTS.c.created_at,
            )
            .where(ADMIN_CREDIT_EVENTS.c.user_id == user_id)
            .order_by(ADMIN_CREDIT_EVENTS.c.created_at.desc())
        ).all()
        refund_requests = connection.execute(
            select(
                REFUND_REQUESTS.c.id,
                REFUND_REQUESTS.c.order_reference,
                REFUND_REQUESTS.c.provider,
                REFUND_REQUESTS.c.reason,
                REFUND_REQUESTS.c.status,
                REFUND_REQUESTS.c.admin_note,
                REFUND_REQUESTS.c.created_at,
                REFUND_REQUESTS.c.updated_at,
            )
            .where(REFUND_REQUESTS.c.user_id == user_id)
            .order_by(REFUND_REQUESTS.c.created_at.desc())
        ).all()
        payment_consents = connection.execute(
            select(
                PAYMENT_CONSENTS.c.order_reference,
                PAYMENT_CONSENTS.c.terms_version,
                PAYMENT_CONSENTS.c.privacy_version,
                PAYMENT_CONSENTS.c.terms_accepted,
                PAYMENT_CONSENTS.c.early_performance_requested,
                PAYMENT_CONSENTS.c.language,
                PAYMENT_CONSENTS.c.accepted_at,
            )
            .where(PAYMENT_CONSENTS.c.user_id == user_id)
            .order_by(PAYMENT_CONSENTS.c.accepted_at.desc())
        ).all()
    return {
        "generated_at": utcnow().isoformat(),
        "account": account,
        "subscriptions": [
            {
                "plan_code": row.plan_code,
                "interval": row.interval,
                "status": row.status,
                "starts_at": row.starts_at.isoformat(),
                "ends_at": row.ends_at.isoformat(),
                "created_at": row.created_at.isoformat(),
            }
            for row in subscriptions
        ],
        "usage": [
            {
                "job_id": row.job_id,
                "plan_code": row.plan_code,
                "minutes": row.minutes,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in usage
        ],
        "instagram_reward": (
            {
                "handle": reward.handle,
                "status": reward.status,
                "minutes": reward.minutes,
                "created_at": reward.created_at.isoformat(),
            }
            if reward
            else None
        ),
        "rewarded_ad_claims": [
            {
                "id": row.id,
                "status": row.status,
                "minutes": row.minutes,
                "created_at": row.created_at.isoformat(),
                "redeemed_at": row.redeemed_at.isoformat() if row.redeemed_at else None,
            }
            for row in rewarded_claims
        ],
        "credit_adjustments": [
            {
                "id": row.id,
                "minutes_delta": row.minutes_delta,
                "balance_before": row.balance_before,
                "balance_after": row.balance_after,
                "reason": row.reason,
                "created_at": row.created_at.isoformat(),
            }
            for row in credit_events
        ],
        "refund_requests": [
            {
                "id": row.id,
                "order_reference": row.order_reference,
                "provider": row.provider,
                "reason": row.reason,
                "status": row.status,
                "admin_note": row.admin_note,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in refund_requests
        ],
        "payment_consents": [
            {
                "order_reference": row.order_reference,
                "terms_version": row.terms_version,
                "privacy_version": row.privacy_version,
                "terms_accepted": bool(row.terms_accepted),
                "early_performance_requested": bool(row.early_performance_requested),
                "language": row.language,
                "accepted_at": row.accepted_at.isoformat(),
            }
            for row in payment_consents
        ],
    }


def close_user_account(
    user_id: str,
    current_password: str,
    email_confirmation: str,
) -> dict[str, str]:
    """Close access and anonymize identity while retaining required payment records."""
    init_rollout_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        profile = connection.execute(
            select(USER_PROFILES).where(USER_PROFILES.c.user_id == user_id)
        ).first()
        if not user or not profile:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        if email_confirmation.strip().casefold() != user.email.casefold():
            raise BillingAuthenticationError("Hesap e-posta adresi eşleşmiyor.")
        candidate = _hash_password(current_password, bytes.fromhex(user.password_salt))
        if not secrets.compare_digest(candidate, user.password_hash):
            raise BillingAuthenticationError("Mevcut parola hatalı.")

        anonymized_email = f"deleted+{uuid.uuid4().hex}@users.invalid"
        salt = secrets.token_bytes(16)
        connection.execute(
            update(USERS)
            .where(USERS.c.id == user_id)
            .values(
                email=anonymized_email,
                password_salt=salt.hex(),
                password_hash=_hash_password(secrets.token_urlsafe(48), salt),
                credit_minutes=0,
            )
        )
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user_id)
            .values(
                first_name="Deleted",
                last_name="User",
                phone=None,
                country_code="ZZ",
                email_verified_at=None,
                phone_verified_at=None,
                session_version=USER_PROFILES.c.session_version + 1,
                updated_at=now,
            )
        )
        connection.execute(delete(USER_PREFERENCES).where(USER_PREFERENCES.c.user_id == user_id))
        connection.execute(delete(AUTH_TOKENS).where(AUTH_TOKENS.c.user_id == user_id))
        connection.execute(delete(EMAIL_CHANGE_REQUESTS).where(EMAIL_CHANGE_REQUESTS.c.user_id == user_id))
        connection.execute(delete(INSTAGRAM_REWARDS).where(INSTAGRAM_REWARDS.c.user_id == user_id))
        connection.execute(delete(REWARDED_AD_CLAIMS).where(REWARDED_AD_CLAIMS.c.user_id == user_id))
        connection.execute(delete(GUEST_TRIALS).where(GUEST_TRIALS.c.user_id == user_id))
        connection.execute(
            update(SUBSCRIPTIONS)
            .where(
                SUBSCRIPTIONS.c.user_id == user_id,
                SUBSCRIPTIONS.c.status.in_(("active", "cancel_at_end")),
            )
            .values(status="cancelled")
        )
        connection.execute(
            update(MANUAL_ORDERS)
            .where(MANUAL_ORDERS.c.user_id == user_id, MANUAL_ORDERS.c.status == "pending")
            .values(status="cancelled", updated_at=now)
        )
        connection.execute(
            update(PAYMENT_ORDERS)
            .where(
                PAYMENT_ORDERS.c.user_id == user_id,
                PAYMENT_ORDERS.c.status.in_(("created", "token_failed")),
            )
            .values(status="cancelled", updated_at=now)
        )
    return {"closed_at": now.isoformat(), "status": "closed"}


def create_or_resume_guest(fingerprint: str) -> dict[str, Any]:
    init_rollout_database()
    fingerprint_hash = _hash(fingerprint)
    now = utcnow()
    with ENGINE.begin() as connection:
        trial = connection.execute(
            select(GUEST_TRIALS).where(GUEST_TRIALS.c.fingerprint_hash == fingerprint_hash)
        ).first()
        if trial:
            user = connection.execute(select(USERS).where(USERS.c.id == trial.user_id)).first()
            profile = connection.execute(
                select(USER_PROFILES).where(USER_PROFILES.c.user_id == trial.user_id)
            ).first()
            if not user or not profile:
                raise BillingAuthenticationError("Misafir deneme kaydı kullanılamıyor.")
            connection.execute(
                update(GUEST_TRIALS)
                .where(GUEST_TRIALS.c.fingerprint_hash == fingerprint_hash)
                .values(last_seen_at=now)
            )
            return {
                "token": issue_session(user.id, user.email, int(profile.session_version)),
                "account": account_status(user.id),
                "resumed": True,
                "trial": {
                    "used": bool(trial.job_id),
                    "max_minutes": config.GUEST_TRIAL_MAX_MINUTES,
                    "remaining_minutes": 0 if trial.job_id else config.GUEST_TRIAL_MAX_MINUTES,
                    "job_id": trial.job_id,
                },
            }

        user_id = str(uuid.uuid4())
        guest_email = f"guest-{fingerprint_hash[:24]}@guest.lecturesift.invalid"
        salt = secrets.token_bytes(16)
        password = secrets.token_urlsafe(32)
        connection.execute(
            USERS.insert().values(
                id=user_id,
                email=guest_email,
                password_salt=salt.hex(),
                password_hash=_hash_password(password, salt),
                credit_minutes=0,
                created_at=now,
            )
        )
        connection.execute(
            USER_PROFILES.insert().values(
                user_id=user_id,
                first_name="Misafir",
                last_name="Kullanıcı",
                phone=None,
                country_code="TR",
                email_verified_at=now,
                phone_verified_at=None,
                session_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            USER_PREFERENCES.insert().values(user_id=user_id, preferred_language="tr", updated_at=now)
        )
        connection.execute(
            SUBSCRIPTIONS.insert().values(
                id=str(uuid.uuid4()),
                user_id=user_id,
                plan_code="guest",
                interval="trial",
                status="active",
                starts_at=now,
                # Keep the hidden guest plan active so an exhausted guest can
                # never fall back to the registered free plan.
                ends_at=now + timedelta(days=3650),
                source_reference=f"GUEST-{fingerprint_hash[:28].upper()}",
                created_at=now,
            )
        )
        connection.execute(
            GUEST_TRIALS.insert().values(
                fingerprint_hash=fingerprint_hash,
                user_id=user_id,
                job_id=None,
                media_minutes=None,
                created_at=now,
                last_seen_at=now,
            )
        )
    return {
        "token": issue_session(user_id, guest_email, 1),
        "account": account_status(user_id),
        "resumed": False,
        "trial": {
            "used": False,
            "max_minutes": config.GUEST_TRIAL_MAX_MINUTES,
            "remaining_minutes": config.GUEST_TRIAL_MAX_MINUTES,
            "job_id": None,
        },
    }


def is_guest_user(user_id: str) -> bool:
    init_rollout_database()
    with ENGINE.connect() as connection:
        return connection.execute(
            select(GUEST_TRIALS.c.user_id).where(GUEST_TRIALS.c.user_id == user_id)
        ).first() is not None


def guest_trial_status(user_id: str) -> dict[str, Any] | None:
    """Return the authoritative single-use trial state without exposing its fingerprint."""
    init_rollout_database()
    with ENGINE.connect() as connection:
        trial = connection.execute(
            select(GUEST_TRIALS).where(GUEST_TRIALS.c.user_id == user_id)
        ).first()
    if not trial:
        return None
    used = bool(trial.job_id)
    return {
        "used": used,
        "max_minutes": config.GUEST_TRIAL_MAX_MINUTES,
        "remaining_minutes": 0 if used else config.GUEST_TRIAL_MAX_MINUTES,
        "job_id": trial.job_id,
    }


def reserve_guest_job(user_id: str, job_id: str, media_minutes: float) -> None:
    """Enforce a single, short processing job for anonymous visitors."""
    init_rollout_database()
    if media_minutes > config.GUEST_TRIAL_MAX_MINUTES + 0.05:
        raise BillingError(
            f"Hesapsız deneme en fazla {config.GUEST_TRIAL_MAX_MINUTES:g} dakikalık bir kaynakla kullanılabilir."
        )
    with ENGINE.begin() as connection:
        trial = connection.execute(
            select(GUEST_TRIALS).where(GUEST_TRIALS.c.user_id == user_id)
        ).first()
        if not trial:
            return
        if trial.job_id and trial.job_id != job_id:
            raise BillingError("Hesapsız deneme hakkı daha önce kullanılmış.")
        connection.execute(
            update(GUEST_TRIALS)
            .where(GUEST_TRIALS.c.user_id == user_id)
            .values(job_id=job_id, media_minutes=media_minutes, last_seen_at=utcnow())
        )


def update_profile(user_id: str, first_name: str, last_name: str, phone: str) -> dict:
    init_rollout_database()
    first = _normalize_name(first_name, "Ad")
    last = _normalize_name(last_name, "Soyad")
    normalized_phone = "".join(char for char in phone.strip() if char.isdigit() or char == "+")[:32] or None
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        if not user:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        profile = connection.execute(
            select(USER_PROFILES).where(USER_PROFILES.c.user_id == user_id)
        ).first()
        if not profile:
            now = utcnow()
            connection.execute(
                USER_PROFILES.insert().values(
                    user_id=user_id,
                    first_name=first,
                    last_name=last,
                    phone=normalized_phone,
                    country_code="TR",
                    email_verified_at=now,
                    phone_verified_at=None,
                    session_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            connection.execute(
                update(USER_PROFILES)
                .where(USER_PROFILES.c.user_id == user_id)
                .values(first_name=first, last_name=last, phone=normalized_phone, updated_at=utcnow())
            )
    return account_status(user_id)


def _email_change_copy(code: str) -> tuple[str, str, str]:
    subject = "LectureSift yeni e-posta doğrulaması"
    safe = html.escape(code)
    text = f"LectureSift e-posta değişikliği kodun: {code}\nKod 15 dakika geçerlidir."
    body = (
        "<h1>Yeni e-posta adresini doğrula</h1>"
        "<p>LectureSift hesabındaki e-posta değişikliğini tamamlamak için aşağıdaki kodu kullan.</p>"
        f'<p style="font-size:30px;font-weight:800;letter-spacing:8px">{safe}</p>'
        "<p>Kod 15 dakika geçerlidir. Bu isteği sen yapmadıysan e-postayı yok say.</p>"
    )
    return subject, body, text


def request_email_change(user_id: str, new_email: str) -> dict:
    init_rollout_database()
    email = _normalize_email(new_email)
    if not email_delivery_configured():
        raise BillingConfigurationError("E-posta doğrulama hizmeti henüz etkinleştirilmemiş.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    token = secrets.token_urlsafe(32)
    now = utcnow()
    expires_at = now + timedelta(minutes=15)
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        if not user:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        if user.email == email:
            raise BillingError("Yeni e-posta mevcut e-posta adresinden farklı olmalı.")
        if connection.execute(select(USERS.c.id).where(USERS.c.email == email)).first():
            raise BillingError("Bu e-posta adresi başka bir hesapta kullanılıyor.")
        connection.execute(delete(EMAIL_CHANGE_REQUESTS).where(EMAIL_CHANGE_REQUESTS.c.user_id == user_id))
        connection.execute(
            EMAIL_CHANGE_REQUESTS.insert().values(
                user_id=user_id,
                new_email=email,
                code_hash=_hash(code),
                token_hash=_hash(token),
                attempt_count=0,
                expires_at=expires_at,
                created_at=now,
            )
        )
    subject, body, text = _email_change_copy(code)
    try:
        send_transactional_email(email, subject, body, text)
    except EmailDeliveryError:
        with ENGINE.begin() as connection:
            connection.execute(delete(EMAIL_CHANGE_REQUESTS).where(EMAIL_CHANGE_REQUESTS.c.user_id == user_id))
        raise
    return {"ok": True, "new_email": email, "expires_at": expires_at.isoformat(), "token": token}


def verify_email_change(user_id: str, code: str = "", token: str = "") -> dict:
    init_rollout_database()
    now = utcnow()
    normalized_code = "".join(char for char in code if char.isdigit())
    with ENGINE.begin() as connection:
        request = connection.execute(
            select(EMAIL_CHANGE_REQUESTS).where(EMAIL_CHANGE_REQUESTS.c.user_id == user_id)
        ).first()
        if not request or _as_utc(request.expires_at) <= now:
            raise BillingAuthenticationError("E-posta değiştirme doğrulaması geçersiz veya süresi dolmuş.")
        valid = (normalized_code and _hash(normalized_code) == request.code_hash) or (
            token and _hash(token) == request.token_hash
        )
        if not valid:
            attempts = int(request.attempt_count) + 1
            connection.execute(
                update(EMAIL_CHANGE_REQUESTS)
                .where(EMAIL_CHANGE_REQUESTS.c.user_id == user_id)
                .values(attempt_count=attempts)
            )
            if attempts >= 5:
                connection.execute(delete(EMAIL_CHANGE_REQUESTS).where(EMAIL_CHANGE_REQUESTS.c.user_id == user_id))
            raise BillingAuthenticationError("Doğrulama kodu geçersiz.")
        profile = connection.execute(
            select(USER_PROFILES).where(USER_PROFILES.c.user_id == user_id)
        ).first()
        if not profile:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        new_version = int(profile.session_version) + 1
        try:
            connection.execute(update(USERS).where(USERS.c.id == user_id).values(email=request.new_email))
        except IntegrityError as exc:
            raise BillingError("Bu e-posta adresi başka bir hesapta kullanılıyor.") from exc
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user_id)
            .values(session_version=new_version, updated_at=now)
        )
        connection.execute(delete(EMAIL_CHANGE_REQUESTS).where(EMAIL_CHANGE_REQUESTS.c.user_id == user_id))
        new_email = request.new_email
    return {
        "token": issue_session(user_id, new_email, new_version),
        "account": account_status(user_id),
    }


def claim_instagram_reward(user_id: str, handle: str) -> dict:
    init_rollout_database()
    normalized = handle.strip().lstrip("@").casefold()
    if len(normalized) < 2 or len(normalized) > 100 or any(char.isspace() for char in normalized):
        raise BillingError("Geçerli Instagram kullanıcı adını gir.")
    now = utcnow()
    try:
        with ENGINE.begin() as connection:
            connection.execute(
                INSTAGRAM_REWARDS.insert().values(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    handle=normalized,
                    status="pending_verification",
                    minutes=config.INSTAGRAM_BONUS_MINUTES,
                    created_at=now,
                    updated_at=now,
                )
            )
    except IntegrityError as exc:
        raise BillingError("Instagram bonusu için daha önce talep oluşturulmuş.") from exc
    return instagram_reward_for_user(user_id)


def instagram_reward_for_user(user_id: str) -> dict | None:
    init_rollout_database()
    with ENGINE.connect() as connection:
        row = connection.execute(
            select(INSTAGRAM_REWARDS).where(INSTAGRAM_REWARDS.c.user_id == user_id)
        ).first()
    return dict(row._mapping) if row else None


def rewarded_ads_for_user(user_id: str) -> dict[str, Any]:
    """Return a provider-neutral, privacy-safe rewarded-ad allowance."""
    init_rollout_database()
    account = account_status(user_id)
    plan = account.get("plan") or {}
    entitlements = plan.get("entitlements") or {}
    plan_ad_free = bool(entitlements.get("ad_free"))
    configured = bool(config.REWARDED_ADS_ENABLED and config.REWARDED_AD_UNIT_PATH)
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with ENGINE.begin() as connection:
        connection.execute(
            delete(REWARDED_AD_CLAIMS).where(
                REWARDED_AD_CLAIMS.c.user_id == user_id,
                (
                    (REWARDED_AD_CLAIMS.c.status == "issued")
                    & (REWARDED_AD_CLAIMS.c.expires_at < now)
                )
                | (
                    (REWARDED_AD_CLAIMS.c.status == "redeemed")
                    & (REWARDED_AD_CLAIMS.c.redeemed_at < now - timedelta(days=90))
                ),
            )
        )
        earned = connection.execute(
            select(func.coalesce(func.sum(REWARDED_AD_CLAIMS.c.minutes), 0)).where(
                REWARDED_AD_CLAIMS.c.user_id == user_id,
                REWARDED_AD_CLAIMS.c.status == "redeemed",
                REWARDED_AD_CLAIMS.c.redeemed_at >= day_start,
            )
        ).scalar_one()
    earned_today = int(earned or 0)
    remaining_today = max(0, config.REWARDED_AD_DAILY_LIMIT_MINUTES - earned_today)
    guest = is_guest_user(user_id)
    return {
        "configured": configured,
        "enabled": bool(
            configured
            and not guest
            and not plan_ad_free
            and remaining_today >= config.REWARDED_AD_MINUTES_PER_VIEW
        ),
        "provider": "google_gpt" if configured else None,
        "ad_unit_path": config.REWARDED_AD_UNIT_PATH if configured else None,
        "minutes_per_view": config.REWARDED_AD_MINUTES_PER_VIEW,
        "daily_limit_minutes": config.REWARDED_AD_DAILY_LIMIT_MINUTES,
        "earned_today": earned_today,
        "remaining_today": remaining_today,
        "plan_ad_free": plan_ad_free,
        "guest": guest,
    }


def issue_rewarded_ad_session(user_id: str) -> dict[str, Any]:
    state = rewarded_ads_for_user(user_id)
    if not state["configured"]:
        raise BillingConfigurationError("Ödüllü reklam özelliği henüz etkinleştirilmemiş.")
    if state["guest"]:
        raise BillingError("Dakika kazanmak için ücretsiz hesabını oluştur.")
    if state["plan_ad_free"]:
        raise BillingError("Mevcut planın reklamsız kullanım içeriyor.")
    if not state["enabled"]:
        raise BillingError("Bugünkü reklamla dakika kazanma sınırına ulaştın.")
    now = utcnow()
    token = secrets.token_urlsafe(32)
    session_id = str(uuid.uuid4())
    with ENGINE.begin() as connection:
        connection.execute(
            REWARDED_AD_CLAIMS.insert().values(
                id=session_id,
                user_id=user_id,
                token_hash=_hash(token),
                status="issued",
                minutes=config.REWARDED_AD_MINUTES_PER_VIEW,
                expires_at=now + timedelta(minutes=10),
                created_at=now,
                redeemed_at=None,
            )
        )
    return {
        "session_id": session_id,
        "claim_token": token,
        "expires_in_seconds": 10 * 60,
        "ad_unit_path": state["ad_unit_path"],
        "minutes": config.REWARDED_AD_MINUTES_PER_VIEW,
    }


def redeem_rewarded_ad_session(user_id: str, session_id: str, claim_token: str) -> dict[str, Any]:
    init_rollout_database()
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with ENGINE.begin() as connection:
        user = connection.execute(
            select(USERS.c.id).where(USERS.c.id == user_id).with_for_update()
        ).first()
        if not user:
            raise BillingAuthenticationError("Hesap bulunamadı.")
        claim = connection.execute(
            select(REWARDED_AD_CLAIMS).where(
                REWARDED_AD_CLAIMS.c.id == session_id,
                REWARDED_AD_CLAIMS.c.user_id == user_id,
            )
        ).first()
        if not claim or claim.status != "issued":
            raise BillingError("Bu reklam ödülü geçersiz veya daha önce kullanılmış.")
        if _as_utc(claim.expires_at) <= now:
            connection.execute(
                update(REWARDED_AD_CLAIMS)
                .where(REWARDED_AD_CLAIMS.c.id == session_id)
                .values(status="expired")
            )
            raise BillingError("Reklam ödülü oturumunun süresi doldu.")
        if not claim_token or not secrets.compare_digest(_hash(claim_token), claim.token_hash):
            raise BillingAuthenticationError("Reklam ödülü doğrulanamadı.")
        earned = int(connection.execute(
            select(func.coalesce(func.sum(REWARDED_AD_CLAIMS.c.minutes), 0)).where(
                REWARDED_AD_CLAIMS.c.user_id == user_id,
                REWARDED_AD_CLAIMS.c.status == "redeemed",
                REWARDED_AD_CLAIMS.c.redeemed_at >= day_start,
            )
        ).scalar_one() or 0)
        if earned + int(claim.minutes) > config.REWARDED_AD_DAILY_LIMIT_MINUTES:
            raise BillingError("Bugünkü reklamla dakika kazanma sınırına ulaştın.")
        changed = connection.execute(
            update(REWARDED_AD_CLAIMS)
            .where(
                REWARDED_AD_CLAIMS.c.id == session_id,
                REWARDED_AD_CLAIMS.c.status == "issued",
            )
            .values(status="redeemed", redeemed_at=now)
        )
        if changed.rowcount != 1:
            raise BillingError("Bu reklam ödülü daha önce kullanılmış.")
        connection.execute(
            update(USERS)
            .where(USERS.c.id == user_id)
            .values(credit_minutes=USERS.c.credit_minutes + int(claim.minutes))
        )
    return {
        "minutes_added": int(claim.minutes),
        "rewarded_ads": rewarded_ads_for_user(user_id),
        "account": account_status(user_id),
    }


def _public_refund_request(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "order_reference": row.order_reference,
        "provider": row.provider,
        "reason": row.reason,
        "status": row.status,
        "admin_note": row.admin_note,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def refund_requests_for_user(user_id: str) -> list[dict[str, Any]]:
    init_rollout_database()
    with ENGINE.connect() as connection:
        rows = connection.execute(
            select(REFUND_REQUESTS)
            .where(REFUND_REQUESTS.c.user_id == user_id)
            .order_by(REFUND_REQUESTS.c.created_at.desc())
        ).all()
    return [_public_refund_request(row) for row in rows]


def create_refund_request(user_id: str, order_reference: str, reason: str) -> dict[str, Any]:
    reference = order_reference.strip()
    normalized_reason = " ".join(reason.strip().split())
    if not reference or len(reference) > 64:
        raise BillingError("Geçerli bir sipariş numarası seç.")
    if len(normalized_reason) < 10 or len(normalized_reason) > 500:
        raise BillingError("İade nedenini 10 ile 500 karakter arasında açıkla.")
    init_rollout_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        manual_order = connection.execute(
            select(MANUAL_ORDERS).where(
                MANUAL_ORDERS.c.reference == reference,
                MANUAL_ORDERS.c.user_id == user_id,
            )
        ).first()
        payment_order_row = None if manual_order else connection.execute(
            select(PAYMENT_ORDERS).where(
                PAYMENT_ORDERS.c.reference == reference,
                PAYMENT_ORDERS.c.user_id == user_id,
            )
        ).first()
        order = manual_order or payment_order_row
        if not order:
            raise BillingError("Bu sipariş hesabında bulunamadı.")
        if order.status != "paid":
            raise BillingError("Yalnızca ödenmiş siparişler için iade talebi oluşturulabilir.")
        existing = connection.execute(
            select(REFUND_REQUESTS).where(
                REFUND_REQUESTS.c.user_id == user_id,
                REFUND_REQUESTS.c.order_reference == reference,
            )
        ).first()
        if existing:
            return _public_refund_request(existing)
        request_id = str(uuid.uuid4())
        provider = "bank_transfer" if manual_order else str(payment_order_row.provider)
        connection.execute(
            REFUND_REQUESTS.insert().values(
                id=request_id,
                user_id=user_id,
                order_reference=reference,
                provider=provider,
                reason=normalized_reason,
                status="requested",
                admin_note=None,
                reviewed_by=None,
                created_at=now,
                updated_at=now,
            )
        )
        created = connection.execute(
            select(REFUND_REQUESTS).where(REFUND_REQUESTS.c.id == request_id)
        ).first()
    return _public_refund_request(created)


def list_admin_refund_requests(status: str = "requested") -> list[dict[str, Any]]:
    init_rollout_database()
    with ENGINE.connect() as connection:
        query = (
            select(
                REFUND_REQUESTS,
                USERS.c.email.label("user_email"),
                USER_PROFILES.c.first_name,
                USER_PROFILES.c.last_name,
            )
            .join(USERS, USERS.c.id == REFUND_REQUESTS.c.user_id)
            .outerjoin(USER_PROFILES, USER_PROFILES.c.user_id == USERS.c.id)
            .order_by(REFUND_REQUESTS.c.created_at.desc())
        )
        if status:
            query = query.where(REFUND_REQUESTS.c.status == status)
        rows = connection.execute(query).all()
    return [
        {
            **_public_refund_request(row),
            "user": {
                "email": row.user_email,
                "name": " ".join(part for part in (row.first_name, row.last_name) if part),
            },
        }
        for row in rows
    ]


def decide_refund_request(request_id: str, action: str, note: str, actor: str) -> dict[str, Any]:
    selected_action = action.strip().lower()
    normalized_note = " ".join(note.strip().split())
    if selected_action not in {"approve", "reject", "complete"}:
        raise BillingError("Geçersiz iade işlemi.")
    if len(normalized_note) > 500:
        raise BillingError("Yönetici notu en fazla 500 karakter olabilir.")
    init_rollout_database()
    with ENGINE.begin() as connection:
        request = connection.execute(
            select(REFUND_REQUESTS)
            .where(REFUND_REQUESTS.c.id == request_id)
            .with_for_update()
        ).first()
        if not request:
            raise BillingError("İade talebi bulunamadı.")
        if selected_action == "complete":
            if request.status != "approved_pending_refund":
                raise BillingError("İade, tamamlandı olarak işaretlenmeden önce onaylanmalıdır.")
            next_status = "completed"
        else:
            if request.status != "requested":
                raise BillingError("Bu iade talebi daha önce incelenmiş.")
            next_status = "approved_pending_refund" if selected_action == "approve" else "rejected"
        connection.execute(
            update(REFUND_REQUESTS)
            .where(REFUND_REQUESTS.c.id == request_id)
            .values(
                status=next_status,
                admin_note=normalized_note or None,
                reviewed_by=actor[:80],
                updated_at=utcnow(),
            )
        )
        updated = connection.execute(
            select(REFUND_REQUESTS).where(REFUND_REQUESTS.c.id == request_id)
        ).first()
    return _public_refund_request(updated)


def _masked_ip_network(client_ip: str) -> str:
    try:
        address = ipaddress.ip_address((client_ip or "").strip())
    except ValueError:
        return "unknown"
    prefix = 24 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def record_account_activity(user_id: str, event_type: str, client_ip: str, user_agent: str) -> bool:
    """Record security activity without retaining a full IP address."""
    normalized_user_id = (user_id or "").strip()
    normalized_event = (event_type or "activity").strip().lower()[:32]
    if not normalized_user_id:
        return False
    secret = config.BILLING_SESSION_SECRET or config.DATABASE_URL or "lecturesift-activity"
    fingerprint = hashlib.sha256(f"{secret}|{client_ip}".encode("utf-8")).hexdigest()
    safe_agent = " ".join((user_agent or "unknown").replace("\x00", "").split())[:240] or "unknown"
    try:
        init_rollout_database()
        with ENGINE.begin() as connection:
            connection.execute(
                delete(ACCOUNT_ACTIVITY).where(
                    ACCOUNT_ACTIVITY.c.created_at
                    < utcnow() - timedelta(days=config.ACCOUNT_ACTIVITY_RETENTION_DAYS)
                )
            )
            connection.execute(
                ACCOUNT_ACTIVITY.insert().values(
                    id=str(uuid.uuid4()),
                    user_id=normalized_user_id,
                    event_type=normalized_event,
                    ip_hash=fingerprint,
                    ip_network=_masked_ip_network(client_ip),
                    user_agent=safe_agent,
                    created_at=utcnow(),
                )
            )
        return True
    except Exception:
        # Account access must not fail merely because a non-essential audit
        # insert is temporarily unavailable.
        return False


def list_admin_user_activity(user_id: str, limit: int = 30) -> list[dict[str, Any]]:
    init_rollout_database()
    safe_limit = max(1, min(int(limit), 100))
    with ENGINE.connect() as connection:
        exists_row = connection.execute(select(USERS.c.id).where(USERS.c.id == user_id)).first()
        if not exists_row:
            raise BillingError("Kullanıcı bulunamadı.")
        rows = connection.execute(
            select(ACCOUNT_ACTIVITY)
            .where(ACCOUNT_ACTIVITY.c.user_id == user_id)
            .order_by(ACCOUNT_ACTIVITY.c.created_at.desc())
            .limit(safe_limit)
        ).all()
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "ip_network": row.ip_network,
            "ip_fingerprint": row.ip_hash[:12],
            "user_agent": row.user_agent,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def _protected_admin_emails() -> set[str]:
    protected = {item.casefold() for item in config.BILLING_PROTECTED_EMAILS}
    if config.LEGAL_OPERATOR_EMAIL:
        protected.add(config.LEGAL_OPERATOR_EMAIL.casefold())
    return protected


def _latest_activity_map(connection, user_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not user_ids:
        return {}
    latest = (
        select(
            ACCOUNT_ACTIVITY.c.user_id,
            func.max(ACCOUNT_ACTIVITY.c.created_at).label("last_at"),
        )
        .where(ACCOUNT_ACTIVITY.c.user_id.in_(user_ids))
        .group_by(ACCOUNT_ACTIVITY.c.user_id)
        .subquery()
    )
    rows = connection.execute(
        select(ACCOUNT_ACTIVITY)
        .join(
            latest,
            and_(
                ACCOUNT_ACTIVITY.c.user_id == latest.c.user_id,
                ACCOUNT_ACTIVITY.c.created_at == latest.c.last_at,
            ),
        )
    ).all()
    return {
        row.user_id: {
            "event_type": row.event_type,
            "ip_network": row.ip_network,
            "ip_fingerprint": row.ip_hash[:12],
            "user_agent": row.user_agent,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    }


def list_admin_users_page(
    *,
    search: str = "",
    verification: str = "all",
    plan_code: str = "all",
    sort: str = "created_desc",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    init_billing_database()
    now = utcnow()
    safe_page = max(1, int(page))
    safe_page_size = max(10, min(int(page_size), 100))
    filters = [USERS.c.email.not_like("deleted+%@users.invalid")]
    needle = (search or "").strip().casefold()
    if needle:
        pattern = f"%{needle[:160]}%"
        filters.append(
            or_(
                func.lower(USERS.c.email).like(pattern),
                func.lower(func.coalesce(USER_PROFILES.c.first_name, "")).like(pattern),
                func.lower(func.coalesce(USER_PROFILES.c.last_name, "")).like(pattern),
                func.lower(func.coalesce(USER_PROFILES.c.phone, "")).like(pattern),
            )
        )
    if verification == "verified":
        filters.append(USER_PROFILES.c.email_verified_at.is_not(None))
    elif verification == "unverified":
        filters.append(USER_PROFILES.c.email_verified_at.is_(None))
    active_subscription = and_(
        SUBSCRIPTIONS.c.user_id == USERS.c.id,
        SUBSCRIPTIONS.c.status.in_(("active", "cancel_at_end")),
        SUBSCRIPTIONS.c.ends_at > now,
    )
    if plan_code == "free":
        filters.append(~exists(select(literal(1)).where(active_subscription)))
    elif plan_code and plan_code != "all":
        filters.append(
            exists(
                select(literal(1)).where(
                    active_subscription,
                    SUBSCRIPTIONS.c.plan_code == plan_code,
                )
            )
        )
    base = (
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
            USER_PROFILES.c.phone_verified_at,
            USER_PROFILES.c.updated_at,
            USER_PREFERENCES.c.preferred_language,
        )
        .select_from(
            USERS.outerjoin(USER_PROFILES, USER_PROFILES.c.user_id == USERS.c.id)
            .outerjoin(USER_PREFERENCES, USER_PREFERENCES.c.user_id == USERS.c.id)
        )
        .where(*filters)
    )
    sort_map = {
        "created_asc": USERS.c.created_at.asc(),
        "email_asc": func.lower(USERS.c.email).asc(),
        "email_desc": func.lower(USERS.c.email).desc(),
        "credit_desc": USERS.c.credit_minutes.desc(),
        "credit_asc": USERS.c.credit_minutes.asc(),
    }
    ordering = sort_map.get(sort, USERS.c.created_at.desc())
    with ENGINE.connect() as connection:
        total = int(connection.execute(select(func.count()).select_from(base.subquery())).scalar_one())
        rows = connection.execute(
            base.order_by(ordering).offset((safe_page - 1) * safe_page_size).limit(safe_page_size)
        ).all()
        user_ids = [row.id for row in rows]
        subscriptions = connection.execute(
            select(SUBSCRIPTIONS)
            .where(
                SUBSCRIPTIONS.c.user_id.in_(user_ids) if user_ids else literal(False),
                SUBSCRIPTIONS.c.status.in_(("active", "cancel_at_end")),
                SUBSCRIPTIONS.c.ends_at > now,
            )
            .order_by(SUBSCRIPTIONS.c.ends_at.desc())
        ).all()
        usage_rows = connection.execute(
            select(
                USAGE_EVENTS.c.user_id,
                func.coalesce(func.sum(USAGE_EVENTS.c.minutes), 0).label("total_minutes"),
            )
            .where(USAGE_EVENTS.c.user_id.in_(user_ids) if user_ids else literal(False))
            .group_by(USAGE_EVENTS.c.user_id)
        ).all()
        latest_activity = _latest_activity_map(connection, user_ids)
    subscriptions_by_user: dict[str, dict[str, Any]] = {}
    for item in subscriptions:
        subscriptions_by_user.setdefault(item.user_id, {
            "id": item.id,
            "plan_code": item.plan_code,
            "interval": item.interval,
            "status": item.status,
            "starts_at": item.starts_at.isoformat(),
            "ends_at": item.ends_at.isoformat(),
        })
    usage_by_user = {item.user_id: int(item.total_minutes or 0) for item in usage_rows}
    protected = _protected_admin_emails()
    items = []
    for row in rows:
        subscription = subscriptions_by_user.get(row.id)
        items.append({
            "id": row.id,
            "email": row.email,
            "first_name": row.first_name or "",
            "last_name": row.last_name or "",
            "name": " ".join(part for part in (row.first_name, row.last_name) if part),
            "phone": row.phone or "",
            "country_code": row.country_code or "",
            "preferred_language": row.preferred_language or "tr",
            "email_verified": row.email_verified_at is not None,
            "phone_verified": row.phone_verified_at is not None,
            "is_protected": row.email.casefold() in protected,
            "credit_minutes": int(row.credit_minutes or 0),
            "total_usage_minutes": usage_by_user.get(row.id, 0),
            "plan_code": subscription["plan_code"] if subscription else "free",
            "subscription": subscription,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat() if row.updated_at else row.created_at.isoformat(),
            "last_activity": latest_activity.get(row.id),
        })
    return {
        "items": items,
        "pagination": {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": max(1, math.ceil(total / safe_page_size)),
        },
    }


def list_admin_orders_page(
    *,
    search: str = "",
    status: str = "all",
    provider: str = "all",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    init_billing_database()
    safe_page = max(1, int(page))
    safe_page_size = max(10, min(int(page_size), 100))
    manual = select(
        MANUAL_ORDERS.c.reference.label("reference"),
        MANUAL_ORDERS.c.user_id.label("user_id"),
        literal("bank_transfer").label("provider"),
        MANUAL_ORDERS.c.plan_code.label("plan_code"),
        MANUAL_ORDERS.c.interval.label("interval"),
        MANUAL_ORDERS.c.amount_minor.label("amount_minor"),
        literal(None, type_=Integer).label("provider_amount_minor"),
        MANUAL_ORDERS.c.currency.label("currency"),
        MANUAL_ORDERS.c.status.label("status"),
        literal(None, type_=String(32)).label("failure_code"),
        literal(None, type_=String(240)).label("failure_message"),
        MANUAL_ORDERS.c.created_at.label("created_at"),
        MANUAL_ORDERS.c.updated_at.label("updated_at"),
    )
    card = select(
        PAYMENT_ORDERS.c.reference,
        PAYMENT_ORDERS.c.user_id,
        PAYMENT_ORDERS.c.provider,
        PAYMENT_ORDERS.c.plan_code,
        PAYMENT_ORDERS.c.interval,
        PAYMENT_ORDERS.c.amount_minor,
        PAYMENT_ORDERS.c.provider_amount_minor,
        PAYMENT_ORDERS.c.currency,
        PAYMENT_ORDERS.c.status,
        PAYMENT_ORDERS.c.failure_code,
        PAYMENT_ORDERS.c.failure_message,
        PAYMENT_ORDERS.c.created_at,
        PAYMENT_ORDERS.c.updated_at,
    )
    orders = union_all(manual, card).subquery("admin_orders")
    filters = []
    needle = (search or "").strip().casefold()
    if needle:
        pattern = f"%{needle[:160]}%"
        filters.append(
            or_(
                func.lower(orders.c.reference).like(pattern),
                func.lower(USERS.c.email).like(pattern),
                func.lower(func.coalesce(USER_PROFILES.c.first_name, "")).like(pattern),
                func.lower(func.coalesce(USER_PROFILES.c.last_name, "")).like(pattern),
            )
        )
    if status == "failed":
        filters.append(orders.c.status.in_(("failed", "token_failed", "cancelled")))
    elif status and status != "all":
        filters.append(orders.c.status == status)
    if provider == "card":
        filters.append(orders.c.provider != "bank_transfer")
    elif provider and provider != "all":
        filters.append(orders.c.provider == provider)
    base = (
        select(
            orders,
            USERS.c.email.label("user_email"),
            USER_PROFILES.c.first_name,
            USER_PROFILES.c.last_name,
            PAYMENT_CONSENTS.c.ip_hash.label("consent_ip_hash"),
            PAYMENT_CONSENTS.c.user_agent_hash.label("consent_user_agent_hash"),
            PAYMENT_CONSENTS.c.language.label("consent_language"),
            PAYMENT_CONSENTS.c.accepted_at.label("consent_accepted_at"),
        )
        .join(USERS, USERS.c.id == orders.c.user_id)
        .outerjoin(USER_PROFILES, USER_PROFILES.c.user_id == USERS.c.id)
        .outerjoin(PAYMENT_CONSENTS, PAYMENT_CONSENTS.c.order_reference == orders.c.reference)
        .where(*filters)
    )
    with ENGINE.connect() as connection:
        total = int(connection.execute(select(func.count()).select_from(base.subquery())).scalar_one())
        rows = connection.execute(
            base.order_by(orders.c.created_at.desc())
            .offset((safe_page - 1) * safe_page_size)
            .limit(safe_page_size)
        ).all()
        latest_activity = _latest_activity_map(connection, list({row.user_id for row in rows}))
    items = []
    for row in rows:
        activity = latest_activity.get(row.user_id)
        items.append({
            "reference": row.reference,
            "order_number": row.reference,
            "provider": row.provider,
            "payment_method": "bank_transfer" if row.provider == "bank_transfer" else "card",
            "plan_code": row.plan_code,
            "interval": row.interval,
            "amount_minor": int(row.amount_minor),
            "provider_amount_minor": row.provider_amount_minor,
            "currency": row.currency,
            "status": row.status,
            "failure_code": row.failure_code,
            "failure_message": row.failure_message,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "user": {
                "id": row.user_id,
                "email": row.user_email,
                "name": " ".join(part for part in (row.first_name, row.last_name) if part),
                "last_activity": activity,
            },
            "consent": {
                "accepted_at": row.consent_accepted_at.isoformat() if row.consent_accepted_at else None,
                "language": row.consent_language,
                "ip_fingerprint": row.consent_ip_hash[:12] if row.consent_ip_hash else None,
                "user_agent_fingerprint": row.consent_user_agent_hash[:12] if row.consent_user_agent_hash else None,
            },
        })
    return {
        "items": items,
        "pagination": {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": max(1, math.ceil(total / safe_page_size)),
        },
    }


def admin_user_identity(user_id: str) -> dict[str, Any]:
    init_billing_database()
    with ENGINE.connect() as connection:
        row = connection.execute(select(USERS.c.id, USERS.c.email).where(USERS.c.id == user_id)).first()
    if not row or row.email.startswith("deleted+"):
        raise BillingError("Kullanıcı bulunamadı.")
    return {
        "id": row.id,
        "email": row.email,
        "is_protected": row.email.casefold() in _protected_admin_emails(),
    }


def _record_admin_account_event(
    connection,
    *,
    user_id: str,
    email: str,
    action: str,
    summary: str,
    actor: str,
) -> None:
    connection.execute(
        ADMIN_ACCOUNT_EVENTS.insert().values(
            id=str(uuid.uuid4()),
            subject_user_id=user_id,
            subject_email=email[:320],
            action=action[:48],
            summary=summary[:500],
            actor=actor[:80],
            created_at=utcnow(),
        )
    )


def admin_update_user(
    user_id: str,
    *,
    email: str,
    first_name: str,
    last_name: str,
    phone: str,
    country_code: str,
    preferred_language: str,
    email_verified: bool,
    actor: str,
) -> dict[str, Any]:
    """Update identity fields while invalidating previously issued sessions."""
    init_rollout_database()
    normalized_email = _normalize_email(email)
    first = _normalize_name(first_name, "Ad")
    last = _normalize_name(last_name, "Soyad")
    normalized_phone = "".join(
        char for char in phone.strip() if char.isdigit() or char == "+"
    )[:32] or None
    country = country_code.strip().upper()
    language = preferred_language.strip().lower().replace("_", "-")
    supported_languages = {"tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"}
    if len(country) != 2 or not country.isalpha():
        raise BillingError("Geçerli bir ülke kodu gir.")
    if language not in supported_languages:
        raise BillingError("Seçilen arayüz dili desteklenmiyor.")
    if normalized_phone and not 7 <= sum(char.isdigit() for char in normalized_phone) <= 15:
        raise BillingError("Telefon numarası 7 ile 15 rakam arasında olmalı.")

    now = utcnow()
    try:
        with ENGINE.begin() as connection:
            user = connection.execute(
                select(USERS).where(USERS.c.id == user_id).with_for_update()
            ).first()
            profile = connection.execute(
                select(USER_PROFILES).where(USER_PROFILES.c.user_id == user_id)
            ).first()
            if not user or not profile:
                raise BillingError("Kullanıcı bulunamadı.")
            old_email = str(user.email)
            connection.execute(
                update(USERS).where(USERS.c.id == user_id).values(email=normalized_email)
            )
            connection.execute(
                update(USER_PROFILES)
                .where(USER_PROFILES.c.user_id == user_id)
                .values(
                    first_name=first,
                    last_name=last,
                    phone=normalized_phone,
                    country_code=country,
                    email_verified_at=now if email_verified else None,
                    session_version=USER_PROFILES.c.session_version + 1,
                    updated_at=now,
                )
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
            _record_admin_account_event(
                connection,
                user_id=user_id,
                email=normalized_email,
                action="user_updated",
                summary=(
                    f"Profil güncellendi; e-posta {old_email} -> {normalized_email}; "
                    f"doğrulama={'açık' if email_verified else 'kapalı'}"
                ),
                actor=actor,
            )
    except IntegrityError as exc:
        raise BillingError("Bu e-posta adresi başka bir hesap tarafından kullanılıyor.") from exc
    return account_status(user_id)


def admin_set_user_subscription(
    user_id: str,
    *,
    plan_code: str,
    interval: str,
    duration_days: int,
    actor: str,
) -> dict[str, Any]:
    """Replace the active entitlement with a time-bounded admin grant."""
    init_rollout_database()
    selected_plan = plan_code.strip().casefold()
    selected_interval = interval.strip().casefold()
    allowed_plans = {"free", "lite", "plus", "pro", "max", "business"}
    if selected_plan not in allowed_plans or selected_plan not in PLAN_BY_CODE:
        raise BillingError("Yönetilebilir bir plan seç.")
    if selected_interval not in {"monthly", "annual"}:
        raise BillingError("Abonelik dönemi aylık veya yıllık olmalı.")
    days = int(duration_days)
    if days < 1 or days > 3660:
        raise BillingError("Abonelik süresi 1 ile 3660 gün arasında olmalı.")

    now = utcnow()
    with ENGINE.begin() as connection:
        user = connection.execute(
            select(USERS).where(USERS.c.id == user_id).with_for_update()
        ).first()
        if not user:
            raise BillingError("Kullanıcı bulunamadı.")
        connection.execute(
            update(SUBSCRIPTIONS)
            .where(
                SUBSCRIPTIONS.c.user_id == user_id,
                SUBSCRIPTIONS.c.status.in_(("active", "cancel_at_end")),
            )
            .values(status="cancelled")
        )
        if selected_plan != "free":
            connection.execute(
                SUBSCRIPTIONS.insert().values(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    plan_code=selected_plan,
                    interval=selected_interval,
                    status="active",
                    starts_at=now,
                    ends_at=now + timedelta(days=days),
                    source_reference=f"ADMIN-{uuid.uuid4().hex.upper()}",
                    created_at=now,
                )
            )
        _record_admin_account_event(
            connection,
            user_id=user_id,
            email=user.email,
            action="subscription_changed",
            summary=(
                "Aktif abonelik kaldırıldı; ücretsiz plan etkin"
                if selected_plan == "free"
                else f"{selected_plan} planı {days} gün / {selected_interval} olarak atandı"
            ),
            actor=actor,
        )
    return account_status(user_id)


def admin_revoke_user_sessions(user_id: str, actor: str) -> dict[str, Any]:
    init_rollout_database()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.id == user_id)).first()
        if not user:
            raise BillingError("Kullanıcı bulunamadı.")
        result = connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user_id)
            .values(session_version=USER_PROFILES.c.session_version + 1, updated_at=utcnow())
        )
        if not result.rowcount:
            raise BillingError("Kullanıcı profili bulunamadı.")
        _record_admin_account_event(
            connection,
            user_id=user_id,
            email=user.email,
            action="sessions_revoked",
            summary="Tüm açık kullanıcı oturumları kapatıldı",
            actor=actor,
        )
    return {"user_id": user_id, "sessions_revoked": True}


def admin_close_user_account(
    user_id: str,
    *,
    confirmation_email: str,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    """Anonymise access while retaining legally required commerce records."""
    init_rollout_database()
    normalized_reason = " ".join(reason.strip().split())
    if len(normalized_reason) < 4 or len(normalized_reason) > 500:
        raise BillingError("Hesap kapatma nedenini 4 ile 500 karakter arasında yaz.")
    now = utcnow()
    with ENGINE.begin() as connection:
        user = connection.execute(
            select(USERS).where(USERS.c.id == user_id).with_for_update()
        ).first()
        profile = connection.execute(
            select(USER_PROFILES).where(USER_PROFILES.c.user_id == user_id)
        ).first()
        if not user or not profile:
            raise BillingError("Kullanıcı bulunamadı.")
        if confirmation_email.strip().casefold() != user.email.casefold():
            raise BillingError("Onay e-postası kullanıcı hesabıyla eşleşmiyor.")
        protected_admin_emails = set(config.BILLING_PROTECTED_EMAILS)
        if config.LEGAL_OPERATOR_EMAIL:
            protected_admin_emails.add(config.LEGAL_OPERATOR_EMAIL.casefold())
        if user.email.casefold() in protected_admin_emails:
            raise BillingError("Yönetici hesabı panelden kapatılamaz.")

        anonymized_audit_email = f"deleted-account-{user_id[:8]}@users.invalid"
        connection.execute(
            update(ADMIN_ACCOUNT_EVENTS)
            .where(ADMIN_ACCOUNT_EVENTS.c.subject_user_id == user_id)
            .values(
                subject_email=anonymized_audit_email,
                summary="Önceki yönetici işlemi hesap anonimleştirildikten sonra kimliksiz olarak saklandı",
            )
        )
        _record_admin_account_event(
            connection,
            user_id=user_id,
            email=anonymized_audit_email,
            action="account_closed",
            summary=f"Hesap kapatıldı ve kimlikten arındırıldı: {normalized_reason}",
            actor=actor,
        )
        anonymized_email = f"deleted+{uuid.uuid4().hex}@users.invalid"
        salt = secrets.token_bytes(16)
        connection.execute(
            update(USERS)
            .where(USERS.c.id == user_id)
            .values(
                email=anonymized_email,
                password_salt=salt.hex(),
                password_hash=_hash_password(secrets.token_urlsafe(48), salt),
                credit_minutes=0,
            )
        )
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == user_id)
            .values(
                first_name="Deleted",
                last_name="User",
                phone=None,
                country_code="ZZ",
                email_verified_at=None,
                phone_verified_at=None,
                session_version=USER_PROFILES.c.session_version + 1,
                updated_at=now,
            )
        )
        for table in (
            USER_PREFERENCES,
            AUTH_TOKENS,
            EMAIL_CHANGE_REQUESTS,
            INSTAGRAM_REWARDS,
            REWARDED_AD_CLAIMS,
            GUEST_TRIALS,
        ):
            connection.execute(delete(table).where(table.c.user_id == user_id))
        connection.execute(
            update(SUBSCRIPTIONS)
            .where(
                SUBSCRIPTIONS.c.user_id == user_id,
                SUBSCRIPTIONS.c.status.in_(("active", "cancel_at_end")),
            )
            .values(status="cancelled")
        )
        connection.execute(
            update(MANUAL_ORDERS)
            .where(MANUAL_ORDERS.c.user_id == user_id, MANUAL_ORDERS.c.status == "pending")
            .values(status="cancelled", updated_at=now)
        )
        connection.execute(
            update(PAYMENT_ORDERS)
            .where(
                PAYMENT_ORDERS.c.user_id == user_id,
                PAYMENT_ORDERS.c.status.in_(("created", "token_failed")),
            )
            .values(status="cancelled", updated_at=now)
        )
    return {"user_id": user_id, "status": "closed", "closed_at": now.isoformat()}


def list_admin_account_events(limit: int = 100) -> list[dict[str, Any]]:
    init_rollout_database()
    safe_limit = max(1, min(int(limit), 500))
    with ENGINE.connect() as connection:
        rows = connection.execute(
            select(ADMIN_ACCOUNT_EVENTS)
            .order_by(ADMIN_ACCOUNT_EVENTS.c.created_at.desc())
            .limit(safe_limit)
        ).all()
    return [
        {
            **dict(row._mapping),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def adjust_admin_credit(user_id: str, minutes_delta: int, reason: str, actor: str) -> dict[str, Any]:
    delta = int(minutes_delta)
    normalized_reason = " ".join(reason.strip().split())
    if delta == 0 or abs(delta) > 10_000:
        raise BillingError("Dakika değişikliği -10.000 ile 10.000 arasında ve sıfırdan farklı olmalı.")
    if len(normalized_reason) < 4 or len(normalized_reason) > 240:
        raise BillingError("İşlem nedenini 4 ile 240 karakter arasında yaz.")
    init_rollout_database()
    event_id = str(uuid.uuid4())
    now = utcnow()
    with ENGINE.begin() as connection:
        user = connection.execute(
            select(USERS).where(USERS.c.id == user_id).with_for_update()
        ).first()
        if not user:
            raise BillingError("Kullanıcı bulunamadı.")
        before = int(user.credit_minutes)
        after = before + delta
        if after < 0:
            raise BillingError("Kredi bakiyesi sıfırın altına düşürülemez.")
        connection.execute(
            update(USERS).where(USERS.c.id == user_id).values(credit_minutes=after)
        )
        connection.execute(
            ADMIN_CREDIT_EVENTS.insert().values(
                id=event_id,
                user_id=user_id,
                minutes_delta=delta,
                balance_before=before,
                balance_after=after,
                reason=normalized_reason,
                actor=actor[:80],
                created_at=now,
            )
        )
    return {
        "id": event_id,
        "user_id": user_id,
        "minutes_delta": delta,
        "balance_before": before,
        "balance_after": after,
        "reason": normalized_reason,
        "created_at": now.isoformat(),
    }


def list_admin_credit_events(limit: int = 100) -> list[dict[str, Any]]:
    init_rollout_database()
    safe_limit = max(1, min(int(limit), 250))
    with ENGINE.connect() as connection:
        rows = connection.execute(
            select(ADMIN_CREDIT_EVENTS, USERS.c.email)
            .join(USERS, USERS.c.id == ADMIN_CREDIT_EVENTS.c.user_id)
            .order_by(ADMIN_CREDIT_EVENTS.c.created_at.desc())
            .limit(safe_limit)
        ).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "email": row.email,
            "minutes_delta": row.minutes_delta,
            "balance_before": row.balance_before,
            "balance_after": row.balance_after,
            "reason": row.reason,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def _public_contact_message(row: Any) -> dict[str, Any]:
    values = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    for key, value in list(values.items()):
        if isinstance(value, datetime):
            values[key] = value.isoformat()
    values["email_notified"] = bool(values.get("email_notified"))
    values["reply_count"] = int(values.get("reply_count") or 0)
    return values


def _public_contact_reply(row: Any) -> dict[str, Any]:
    values = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    for key, value in list(values.items()):
        if isinstance(value, datetime):
            values[key] = value.isoformat()
    # Provider identifiers are operational metadata, not public conversation data.
    values.pop("provider_message_id", None)
    values["sender"] = "LectureSift Destek" if values.get("direction") == "admin" else "Kullanıcı"
    return values


def _support_reply_token(message_id: str) -> str:
    secret = config.BILLING_SESSION_SECRET or config.ADMIN_ADMIN
    if not secret:
        raise BillingConfigurationError("Güvenli destek yanıt bağlantısı henüz yapılandırılmamış.")
    digest = hmac.new(secret.encode("utf-8"), f"support:{message_id}".encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _verify_support_reply_token(message_id: str, token: str) -> None:
    expected = _support_reply_token(message_id)
    if not token or not secrets.compare_digest(expected, token.strip()):
        raise BillingAuthenticationError("Destek konuşması bağlantısı geçersiz.")


def get_contact_conversation(
    message_id: str,
    *,
    public_token: str | None = None,
) -> dict[str, Any]:
    init_rollout_database()
    if public_token is not None:
        _verify_support_reply_token(message_id, public_token)
    with ENGINE.connect() as connection:
        message = connection.execute(
            select(CONTACT_MESSAGES).where(CONTACT_MESSAGES.c.id == message_id)
        ).one_or_none()
        if message is None:
            raise BillingError("İletişim mesajı bulunamadı.")
        replies = connection.execute(
            select(CONTACT_REPLIES)
            .where(CONTACT_REPLIES.c.contact_message_id == message_id)
            .order_by(CONTACT_REPLIES.c.created_at.asc())
        ).all()
    return {
        "message": _public_contact_message(message),
        "replies": [_public_contact_reply(row) for row in replies],
    }


def reply_to_contact_message(message_id: str, body: str, actor: str) -> dict[str, Any]:
    normalized_body = body.strip()
    if len(normalized_body) < 2 or len(normalized_body) > 4000:
        raise BillingError("Yanıt 2 ile 4000 karakter arasında olmalı.")
    conversation = get_contact_conversation(message_id)
    message = conversation["message"]
    reply_id = str(uuid.uuid4())
    now = utcnow()
    with ENGINE.begin() as connection:
        connection.execute(
            CONTACT_REPLIES.insert().values(
                id=reply_id,
                contact_message_id=message_id,
                direction="admin",
                body=normalized_body,
                sender=actor[:320] or "admin",
                delivery_status="pending",
                provider_message_id=None,
                created_at=now,
                sent_at=None,
            )
        )

    token = _support_reply_token(message_id)
    conversation_url = (
        f"{config.FRONTEND_BASE_URL}/support.html#conversation={message_id}&token={token}"
    )
    subject = f"LectureSift destek yanıtı · {message['topic']} · #{message_id[:8]}"
    safe_body = html.escape(normalized_body).replace(chr(10), "<br>")
    try:
        provider_id = send_transactional_email(
            message["email"],
            subject,
            (
                f"<h1>Merhaba {html.escape(message['name'])},</h1>"
                f"<p>{safe_body}</p>"
                f'<p><a href="{html.escape(conversation_url)}">Konuşmayı görüntüle ve yanıtla</a></p>'
                "<p>Bu bağlantı yalnızca bu destek konuşmasına erişim sağlar; kimseyle paylaşma.</p>"
            ),
            (
                f"Merhaba {message['name']},\n\n{normalized_body}\n\n"
                f"Konuşmayı görüntüle ve yanıtla: {conversation_url}"
            ),
            reply_to=config.CONTACT_EMAIL,
        )
    except EmailDeliveryError:
        with ENGINE.begin() as connection:
            connection.execute(
                update(CONTACT_REPLIES)
                .where(CONTACT_REPLIES.c.id == reply_id)
                .values(delivery_status="failed")
            )
        raise

    sent_at = utcnow()
    with ENGINE.begin() as connection:
        connection.execute(
            update(CONTACT_REPLIES)
            .where(CONTACT_REPLIES.c.id == reply_id)
            .values(
                delivery_status="sent",
                provider_message_id=(provider_id or None),
                sent_at=sent_at,
            )
        )
        connection.execute(
            update(CONTACT_MESSAGES)
            .where(CONTACT_MESSAGES.c.id == message_id)
            .values(status="read", updated_at=sent_at)
        )
    return get_contact_conversation(message_id)


def add_public_contact_reply(message_id: str, token: str, body: str) -> dict[str, Any]:
    normalized_body = body.strip()
    if len(normalized_body) < 2 or len(normalized_body) > 4000:
        raise BillingError("Yanıt 2 ile 4000 karakter arasında olmalı.")
    conversation = get_contact_conversation(message_id, public_token=token)
    message = conversation["message"]
    reply_id = str(uuid.uuid4())
    now = utcnow()
    with ENGINE.begin() as connection:
        connection.execute(
            CONTACT_REPLIES.insert().values(
                id=reply_id,
                contact_message_id=message_id,
                direction="user",
                body=normalized_body,
                sender=message["email"],
                delivery_status="received",
                provider_message_id=None,
                created_at=now,
                sent_at=now,
            )
        )
        connection.execute(
            update(CONTACT_MESSAGES)
            .where(CONTACT_MESSAGES.c.id == message_id)
            .values(status="new", updated_at=now)
        )

    if email_delivery_configured() and config.CONTACT_EMAIL:
        try:
            send_transactional_email(
                config.CONTACT_EMAIL,
                f"LectureSift destek yanıtı: {message['topic']} · #{message_id[:8]}",
                (
                    f"<h1>Kullanıcı destek konuşmasına yanıt verdi</h1>"
                    f"<p><strong>Gönderen:</strong> {html.escape(message['name'])} "
                    f"&lt;{html.escape(message['email'])}&gt;</p>"
                    f"<p>{html.escape(normalized_body).replace(chr(10), '<br>')}</p>"
                ),
                f"Gönderen: {message['name']} <{message['email']}>\n\n{normalized_body}",
                reply_to=message["email"],
            )
        except EmailDeliveryError:
            # The reply is already durably available in the admin inbox.
            pass
    return get_contact_conversation(message_id, public_token=token)


def create_contact_message(
    name: str,
    email: str,
    topic: str,
    message: str,
    order_reference: str = "",
) -> dict[str, Any]:
    init_rollout_database()
    normalized_name = " ".join(name.strip().split())
    normalized_email = _normalize_email(email)
    normalized_topic = " ".join(topic.strip().split())
    normalized_message = message.strip()
    normalized_reference = " ".join(order_reference.strip().split())[:64] or None
    if len(normalized_name) < 2 or len(normalized_name) > 120:
        raise BillingError("Ad soyad 2 ile 120 karakter arasında olmalı.")
    if len(normalized_topic) < 2 or len(normalized_topic) > 100:
        raise BillingError("Geçerli bir konu seç.")
    if len(normalized_message) < 10 or len(normalized_message) > 4000:
        raise BillingError("Mesaj 10 ile 4000 karakter arasında olmalı.")

    message_id = str(uuid.uuid4())
    now = utcnow()
    with ENGINE.begin() as connection:
        connection.execute(
            CONTACT_MESSAGES.insert().values(
                id=message_id,
                name=normalized_name,
                email=normalized_email,
                topic=normalized_topic,
                message=normalized_message,
                order_reference=normalized_reference,
                status="new",
                email_notified=0,
                created_at=now,
                updated_at=now,
            )
        )

    notified = False
    if email_delivery_configured() and config.CONTACT_EMAIL:
        reference_line = f"\nSipariş referansı: {normalized_reference}" if normalized_reference else ""
        safe_reference = html.escape(normalized_reference or "—")
        try:
            send_transactional_email(
                config.CONTACT_EMAIL,
                f"LectureSift iletişim: {normalized_topic}",
                (
                    "<h1>Yeni iletişim formu mesajı</h1>"
                    f"<p><strong>Gönderen:</strong> {html.escape(normalized_name)} "
                    f"&lt;{html.escape(normalized_email)}&gt;</p>"
                    f"<p><strong>Konu:</strong> {html.escape(normalized_topic)}</p>"
                    f"<p><strong>Sipariş referansı:</strong> {safe_reference}</p>"
                    f"<p>{html.escape(normalized_message).replace(chr(10), '<br>')}</p>"
                ),
                (
                    f"Gönderen: {normalized_name} <{normalized_email}>\n"
                    f"Konu: {normalized_topic}{reference_line}\n\n{normalized_message}"
                ),
                reply_to=normalized_email,
            )
            notified = True
        except EmailDeliveryError:
            # The durable admin inbox remains the source of truth when email delivery is unavailable.
            notified = False
    if notified:
        with ENGINE.begin() as connection:
            connection.execute(
                update(CONTACT_MESSAGES)
                .where(CONTACT_MESSAGES.c.id == message_id)
                .values(email_notified=1, updated_at=utcnow())
            )
    with ENGINE.connect() as connection:
        row = connection.execute(
            select(CONTACT_MESSAGES).where(CONTACT_MESSAGES.c.id == message_id)
        ).one()
    return _public_contact_message(row)


def list_contact_messages(status: str = "", limit: int = 100) -> list[dict[str, Any]]:
    init_rollout_database()
    safe_limit = max(1, min(int(limit), 250))
    normalized_status = status.strip().casefold()
    if normalized_status and normalized_status not in {"new", "read", "resolved"}:
        raise BillingError("Geçersiz iletişim mesajı durumu.")
    with ENGINE.connect() as connection:
        reply_stats = (
            select(
                CONTACT_REPLIES.c.contact_message_id.label("message_id"),
                func.count(CONTACT_REPLIES.c.id).label("reply_count"),
                func.max(CONTACT_REPLIES.c.created_at).label("last_reply_at"),
            )
            .group_by(CONTACT_REPLIES.c.contact_message_id)
            .subquery()
        )
        query = (
            select(
                CONTACT_MESSAGES,
                func.coalesce(reply_stats.c.reply_count, 0).label("reply_count"),
                reply_stats.c.last_reply_at,
            )
            .outerjoin(reply_stats, reply_stats.c.message_id == CONTACT_MESSAGES.c.id)
            .order_by(CONTACT_MESSAGES.c.updated_at.desc())
            .limit(safe_limit)
        )
        if normalized_status:
            query = query.where(CONTACT_MESSAGES.c.status == normalized_status)
        rows = connection.execute(query).all()
    return [_public_contact_message(row) for row in rows]


def update_contact_message_status(message_id: str, status: str) -> dict[str, Any]:
    normalized_status = status.strip().casefold()
    if normalized_status not in {"new", "read", "resolved"}:
        raise BillingError("Geçersiz iletişim mesajı durumu.")
    init_rollout_database()
    with ENGINE.begin() as connection:
        result = connection.execute(
            update(CONTACT_MESSAGES)
            .where(CONTACT_MESSAGES.c.id == message_id)
            .values(status=normalized_status, updated_at=utcnow())
        )
        if not result.rowcount:
            raise BillingError("İletişim mesajı bulunamadı.")
        row = connection.execute(
            select(CONTACT_MESSAGES).where(CONTACT_MESSAGES.c.id == message_id)
        ).one()
    return _public_contact_message(row)


def list_admin_orders(status: str = "pending") -> list[dict]:
    init_rollout_database()
    with ENGINE.connect() as connection:
        query = (
            select(MANUAL_ORDERS, USERS.c.email)
            .join(USERS, USERS.c.id == MANUAL_ORDERS.c.user_id)
            .order_by(MANUAL_ORDERS.c.created_at.desc())
        )
        if status:
            query = query.where(MANUAL_ORDERS.c.status == status)
        rows = connection.execute(query).all()
    return [dict(row._mapping) for row in rows]


def decide_admin_order(reference: str, approve: bool) -> dict:
    if approve:
        return approve_manual_order(reference)
    return reject_manual_order(reference)


def list_admin_rewards(status: str = "pending_verification") -> list[dict]:
    init_rollout_database()
    with ENGINE.connect() as connection:
        query = (
            select(INSTAGRAM_REWARDS, USERS.c.email)
            .join(USERS, USERS.c.id == INSTAGRAM_REWARDS.c.user_id)
            .order_by(INSTAGRAM_REWARDS.c.created_at.desc())
        )
        if status:
            query = query.where(INSTAGRAM_REWARDS.c.status == status)
        rows = connection.execute(query).all()
    return [dict(row._mapping) for row in rows]


def decide_instagram_reward(reward_id: str, approve: bool) -> dict:
    init_rollout_database()
    with ENGINE.begin() as connection:
        reward = connection.execute(
            select(INSTAGRAM_REWARDS).where(INSTAGRAM_REWARDS.c.id == reward_id)
        ).first()
        if not reward:
            raise BillingError("Instagram bonus talebi bulunamadı.")
        if reward.status == "approved":
            return dict(reward._mapping)
        status = "approved" if approve else "rejected"
        connection.execute(
            update(INSTAGRAM_REWARDS)
            .where(INSTAGRAM_REWARDS.c.id == reward_id)
            .values(status=status, updated_at=utcnow())
        )
        if approve:
            connection.execute(
                update(USERS)
                .where(USERS.c.id == reward.user_id)
                .values(credit_minutes=USERS.c.credit_minutes + int(reward.minutes))
            )
    return {**dict(reward._mapping), "status": status}


def record_runtime(job_id: str, media_minutes: float, elapsed_seconds: float, size_bytes: int) -> None:
    if media_minutes <= 0 or elapsed_seconds <= 0:
        return
    init_rollout_database()
    try:
        with ENGINE.begin() as connection:
            connection.execute(
                RUNTIME_METRICS.insert().values(
                    job_id=job_id,
                    media_minutes=float(media_minutes),
                    elapsed_seconds=float(elapsed_seconds),
                    size_bytes=max(0, int(size_bytes)),
                    created_at=utcnow(),
                )
            )
    except IntegrityError:
        return


def estimate_eta_seconds(
    media_minutes: float,
    size_bytes: int = 0,
    *,
    job_type: str = "study_pack",
    summary_style: str = "standard",
    source_kind: str = "media",
) -> int:
    media_minutes = max(0.1, float(media_minutes or 0.1))
    init_rollout_database()
    with ENGINE.connect() as connection:
        rows = connection.execute(
            select(RUNTIME_METRICS.c.media_minutes, RUNTIME_METRICS.c.elapsed_seconds)
            .where(RUNTIME_METRICS.c.media_minutes > 0, RUNTIME_METRICS.c.elapsed_seconds > 0)
            .order_by(RUNTIME_METRICS.c.created_at.desc())
            .limit(80)
        ).all()
    ratios = [float(row.elapsed_seconds) / float(row.media_minutes) for row in rows]
    seconds_per_minute = statistics.median(ratios) if ratios else 30.0
    processing = 45.0 + media_minutes * max(8.0, min(seconds_per_minute, 180.0))
    type_multiplier = {
        "audio_export": 0.22,
        "download_video": 0.12,
    }.get(str(job_type or "study_pack"), 1.0)
    source_multiplier = 0.62 if source_kind == "document" else 1.0
    style_multiplier = {
        "short": 0.9,
        "detailed": 1.15,
        "exam": 1.08,
        "five_minute": 0.92,
    }.get(str(summary_style or "standard"), 1.0)
    processing *= type_multiplier * source_multiplier * style_multiplier
    size_overhead = max(0, int(size_bytes)) / (40 * 1024 * 1024)
    minimum = 15 if job_type == "download_video" else 20 if job_type == "audio_export" else 35
    return max(minimum, int(round(processing + size_overhead)))
