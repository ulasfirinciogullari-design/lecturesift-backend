"""Persistent product features layered on top of LectureSift billing.

This module intentionally reuses the existing billing database and session
system. No banking, admin, or email credentials are stored in source control.
"""

from __future__ import annotations

import hashlib
import html
import secrets
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Table, delete, select, update
from sqlalchemy.exc import IntegrityError

from . import config
from .billing import PLAN_BY_CODE, Plan
from .billing_service import (
    ENGINE,
    MANUAL_ORDERS,
    METADATA,
    SUBSCRIPTIONS,
    USERS,
    USER_PREFERENCES,
    USER_PROFILES,
    BillingAuthenticationError,
    BillingConfigurationError,
    BillingError,
    _hash_password,
    account_status,
    approve_manual_order,
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
    ),
)


def init_rollout_database() -> None:
    init_billing_database()
    for table in (GUEST_TRIALS, INSTAGRAM_REWARDS, EMAIL_CHANGE_REQUESTS, RUNTIME_METRICS):
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
    }


def is_guest_user(user_id: str) -> bool:
    init_rollout_database()
    with ENGINE.connect() as connection:
        return connection.execute(
            select(GUEST_TRIALS.c.user_id).where(GUEST_TRIALS.c.user_id == user_id)
        ).first() is not None


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
        profile = connection.execute(
            select(USER_PROFILES).where(USER_PROFILES.c.user_id == user_id)
        ).first()
        if not profile:
            raise BillingAuthenticationError("Hesap bulunamadı.")
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
    init_rollout_database()
    with ENGINE.begin() as connection:
        row = connection.execute(
            select(MANUAL_ORDERS).where(MANUAL_ORDERS.c.reference == reference)
        ).first()
        if not row:
            raise BillingError("Sipariş bulunamadı.")
        if row.status == "paid":
            raise BillingError("Onaylanmış sipariş reddedilemez.")
        connection.execute(
            update(MANUAL_ORDERS)
            .where(MANUAL_ORDERS.c.reference == reference)
            .values(status="rejected", updated_at=utcnow())
        )
    return {"reference": reference, "status": "rejected"}


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


def estimate_eta_seconds(media_minutes: float, size_bytes: int = 0) -> int:
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
    size_overhead = max(0, int(size_bytes)) / (40 * 1024 * 1024)
    return max(45, int(round(processing + size_overhead)))
