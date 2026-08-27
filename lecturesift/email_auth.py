"""Resend-backed email verification for LectureSift billing accounts.

This module installs replacement register/login routes on the production FastAPI
application and keeps verification state in the same persistent SQL database as
billing. Existing accounts without a verification row remain valid for backward
compatibility; newly registered accounts require a one-time email code whenever
EMAIL_VERIFICATION_REQUIRED is enabled.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, delete, select, update
from sqlalchemy.exc import IntegrityError

from . import config
from .billing_service import (
    ENGINE,
    METADATA,
    USERS,
    BillingAuthenticationError,
    BillingConfigurationError,
    BillingError,
    _hash_password,
    account_status,
    init_billing_database,
    issue_session,
    login_user,
    register_user,
    utcnow,
)


class EmailDeliveryError(RuntimeError):
    """Raised when Resend did not accept a transactional email."""


class EmailVerificationError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 400, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after


EMAIL_VERIFICATIONS = Table(
    "billing_email_verifications",
    METADATA,
    Column("user_id", String(36), ForeignKey("billing_users.id"), primary_key=True),
    Column("code_hash", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("last_sent_at", DateTime(timezone=True), nullable=True),
    Column("window_started_at", DateTime(timezone=True), nullable=False),
    Column("send_count", Integer, nullable=False, default=0),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("verified_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

_EMAIL_INIT_LOCK = threading.Lock()
_EMAIL_INITIALIZED = False
router = APIRouter(tags=["billing-email"])


class EmailAuthRequest(BaseModel):
    email: str
    password: str
    language: str = "tr"


class VerifyEmailRequest(BaseModel):
    email: str
    code: str


class ResendVerificationRequest(BaseModel):
    email: str
    language: str = "tr"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_email(value: str) -> str:
    email = value.strip().casefold()
    local, separator, domain = email.partition("@")
    if not separator or email.count("@") != 1 or not local or not domain or len(email) > 320 or " " in email:
        raise EmailVerificationError("Geçerli bir e-posta adresi gir.", code="LS-AUTH-01")
    return email


def _validate_password(password: str) -> None:
    if len(password) < 10:
        raise EmailVerificationError("Parola en az 10 karakter olmalı.", code="LS-AUTH-01")


def _verification_secret() -> bytes:
    explicit = config.EMAIL_VERIFICATION_SECRET.strip().encode("utf-8")
    if explicit:
        return explicit
    session_secret = config.BILLING_SESSION_SECRET.strip().encode("utf-8")
    if session_secret:
        return hmac.new(session_secret, b"lecturesift-email-verification-v1", hashlib.sha256).digest()
    return hashlib.sha256((config.DATABASE_URL + "|lecturesift-email-verification-v1").encode("utf-8")).digest()


def _hash_code(user_id: str, code: str) -> str:
    return hmac.new(_verification_secret(), f"{user_id}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _email_required() -> bool:
    return bool(config.EMAIL_VERIFICATION_REQUIRED)


def email_delivery_available() -> bool:
    return bool(config.RESEND_API_KEY.strip() and config.RESEND_FROM_EMAIL.strip())


def init_email_verification_database() -> None:
    global _EMAIL_INITIALIZED
    init_billing_database()
    if _EMAIL_INITIALIZED:
        return
    with _EMAIL_INIT_LOCK:
        if not _EMAIL_INITIALIZED:
            EMAIL_VERIFICATIONS.create(bind=ENGINE, checkfirst=True)
            _EMAIL_INITIALIZED = True


def _email_copy(code: str, language: str) -> tuple[str, str, str]:
    ttl_minutes = max(1, int(config.EMAIL_VERIFICATION_TTL_SECONDS) // 60)
    digit_cells = "".join(
        (
            '<td style="width:16.666%;padding:0 2px;text-align:center;'
            'font-family:Arial,sans-serif;font-size:30px;line-height:1.2;'
            f'font-weight:900;color:#ffffff">{html.escape(digit)}</td>'
        )
        for digit in code
    )
    if language.casefold().startswith("tr"):
        subject = "LectureSift e-posta doğrulama kodun"
        text = (
            f"LectureSift doğrulama kodun: {code}\n\n"
            f"Bu kod {ttl_minutes} dakika geçerlidir. Bu işlemi sen başlatmadıysan e-postayı yok say."
        )
        heading = "E-posta adresini doğrula"
        intro = "LectureSift hesabını tamamlamak için aşağıdaki 6 haneli kodu kullan."
        expiry = f"Kod {ttl_minutes} dakika boyunca geçerlidir ve yalnızca bir kez kullanılabilir."
        warning = "Bu kodu kimseyle paylaşma. LectureSift ekibi senden bu kodu istemez."
    else:
        subject = "Your LectureSift email verification code"
        text = (
            f"Your LectureSift verification code is: {code}\n\n"
            f"This code expires in {ttl_minutes} minutes. Ignore this email if you did not start this request."
        )
        heading = "Verify your email address"
        intro = "Use the 6-digit code below to finish creating your LectureSift account."
        expiry = f"The code is valid for {ttl_minutes} minutes and can be used only once."
        warning = "Do not share this code. The LectureSift team will never ask you for it."
    body = f"""<!doctype html>
<html lang="{html.escape(language[:2] or 'tr')}">
  <body style="margin:0;background:#061022;padding:16px;font-family:Inter,Arial,sans-serif;color:#f7f9ff">
    <div style="box-sizing:border-box;width:100%;max-width:560px;margin:0 auto;border:1px solid #203455;border-radius:22px;background:#0d1c38;padding:28px;box-shadow:0 24px 70px rgba(0,0,0,.28)">
      <div style="font-weight:850;font-size:22px;letter-spacing:-.5px">Lecture<span style="color:#9f87ff">Sift</span></div>
      <h1 style="margin:28px 0 10px;font-size:27px;line-height:1.15">{html.escape(heading)}</h1>
      <p style="margin:0;color:#aebbd1;font-size:15px;line-height:1.65">{html.escape(intro)}</p>
      <div style="box-sizing:border-box;width:100%;max-width:100%;margin:26px 0;padding:16px 8px;border:1px solid #31598c;border-radius:16px;background:#08162d;overflow:hidden">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;table-layout:fixed;border-collapse:collapse">
          <tr>{digit_cells}</tr>
        </table>
      </div>
      <p style="margin:0;color:#aebbd1;font-size:13px;line-height:1.6">{html.escape(expiry)}</p>
      <p style="margin:18px 0 0;padding-top:18px;border-top:1px solid #203455;color:#7286a6;font-size:12px;line-height:1.55">{html.escape(warning)}</p>
    </div>
  </body>
</html>"""
    return subject, text, body


def _send_verification_email(recipient: str, code: str, language: str, idempotency_key: str) -> str:
    if not email_delivery_available():
        raise EmailDeliveryError("Resend e-posta servisi yapılandırılmamış.")
    subject, text_body, html_body = _email_copy(code, language)
    payload: dict = {
        "from": config.RESEND_FROM_EMAIL.strip(),
        "to": [recipient],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }
    if config.BILLING_SUPPORT_EMAIL.strip():
        payload["reply_to"] = config.BILLING_SUPPORT_EMAIL.strip()
    headers = {
        "Authorization": f"Bearer {config.RESEND_API_KEY.strip()}",
        "Content-Type": "application/json",
        "User-Agent": f"LectureSift/{config.APP_VERSION}",
        "Idempotency-Key": idempotency_key[:256],
    }
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = httpx.post("https://api.resend.com/emails", json=payload, headers=headers, timeout=12.0)
        except httpx.RequestError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)
                continue
            break
        if response.is_success:
            try:
                return str(response.json().get("id") or "accepted")
            except ValueError:
                return "accepted"
        if response.status_code == 429 or response.status_code >= 500:
            last_error = RuntimeError(f"Resend transient status {response.status_code}")
            if attempt == 0:
                time.sleep(0.4)
                continue
        else:
            last_error = RuntimeError(f"Resend rejected request with status {response.status_code}")
        break
    raise EmailDeliveryError("Doğrulama e-postası gönderilemedi.") from last_error


def _public_verification(email: str) -> dict:
    return {
        "verification_required": True,
        "email": email,
        "expires_in": int(config.EMAIL_VERIFICATION_TTL_SECONDS),
        "resend_after": int(config.EMAIL_VERIFICATION_RESEND_SECONDS),
    }


def register_with_email_verification(email: str, password: str, language: str = "tr") -> dict:
    if not _email_required():
        result = register_user(email, password)
        return {**result, "account": account_status(result["user"]["id"]), "verification_required": False}
    if not email_delivery_available():
        raise EmailVerificationError(
            "E-posta doğrulama servisi henüz etkinleştirilmemiş.",
            code="LS-AUTH-00",
            status_code=503,
        )
    normalized = _normalize_email(email)
    _validate_password(password)
    init_email_verification_database()
    now = utcnow()
    user_id = str(uuid.uuid4())
    code = _new_code()
    salt = secrets.token_bytes(16)
    user_values = {
        "id": user_id,
        "email": normalized,
        "password_salt": salt.hex(),
        "password_hash": _hash_password(password, salt),
        "credit_minutes": 0,
        "created_at": now,
    }
    verification_values = {
        "user_id": user_id,
        "code_hash": _hash_code(user_id, code),
        "expires_at": now + timedelta(seconds=int(config.EMAIL_VERIFICATION_TTL_SECONDS)),
        "last_sent_at": now,
        "window_started_at": now,
        "send_count": 1,
        "attempt_count": 0,
        "verified_at": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        with ENGINE.begin() as connection:
            existing = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
            if existing:
                pending = connection.execute(
                    select(EMAIL_VERIFICATIONS).where(EMAIL_VERIFICATIONS.c.user_id == existing.id)
                ).first()
                if pending and pending.verified_at is None:
                    raise EmailVerificationError(
                        "Bu e-posta için doğrulama bekliyor. Gelen kodu gir veya yeni kod iste.",
                        code="LS-AUTH-03",
                        status_code=409,
                    )
                raise EmailVerificationError(
                    "Bu e-posta adresiyle daha önce hesap oluşturulmuş.",
                    code="LS-AUTH-02",
                    status_code=409,
                )
            connection.execute(USERS.insert().values(**user_values))
            connection.execute(EMAIL_VERIFICATIONS.insert().values(**verification_values))
    except IntegrityError as exc:
        raise EmailVerificationError(
            "Bu e-posta adresiyle daha önce hesap oluşturulmuş.",
            code="LS-AUTH-02",
            status_code=409,
        ) from exc
    try:
        _send_verification_email(normalized, code, language, f"verify-register/{user_id}")
    except EmailDeliveryError as exc:
        with ENGINE.begin() as connection:
            connection.execute(delete(EMAIL_VERIFICATIONS).where(EMAIL_VERIFICATIONS.c.user_id == user_id))
            connection.execute(delete(USERS).where(USERS.c.id == user_id))
        raise EmailVerificationError(
            "Doğrulama e-postası gönderilemedi. Biraz sonra tekrar dene.",
            code="LS-AUTH-04",
            status_code=503,
        ) from exc
    return _public_verification(normalized)


def login_with_email_verification(email: str, password: str) -> dict:
    if not _email_required():
        result = login_user(email, password)
        return {**result, "account": account_status(result["user"]["id"])}
    normalized = _normalize_email(email)
    init_email_verification_database()
    with ENGINE.connect() as connection:
        user = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
        verification = (
            connection.execute(
                select(EMAIL_VERIFICATIONS).where(EMAIL_VERIFICATIONS.c.user_id == user.id)
            ).first()
            if user
            else None
        )
    if not user:
        raise EmailVerificationError("E-posta veya parola hatalı.", code="LS-AUTH-05", status_code=401)
    candidate = _hash_password(password, bytes.fromhex(user.password_salt))
    if not hmac.compare_digest(candidate, user.password_hash):
        raise EmailVerificationError("E-posta veya parola hatalı.", code="LS-AUTH-05", status_code=401)
    if verification and verification.verified_at is None:
        raise EmailVerificationError(
            "Giriş yapmadan önce e-posta adresini doğrula.",
            code="LS-AUTH-06",
            status_code=403,
        )
    token = issue_session(user.id, user.email)
    return {
        "user": {"id": user.id, "email": user.email},
        "token": token,
        "account": account_status(user.id),
    }


def verify_email_code(email: str, code: str) -> dict:
    normalized = _normalize_email(email)
    normalized_code = "".join(character for character in code if character.isdigit())
    if len(normalized_code) != 6:
        raise EmailVerificationError("6 haneli doğrulama kodunu gir.", code="LS-AUTH-07")
    init_email_verification_database()
    now = utcnow()
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
        verification = (
            connection.execute(
                select(EMAIL_VERIFICATIONS)
                .where(EMAIL_VERIFICATIONS.c.user_id == user.id)
                .with_for_update()
            ).first()
            if user
            else None
        )
        if not user or not verification:
            raise EmailVerificationError("Kod geçersiz veya süresi dolmuş.", code="LS-AUTH-07")
        if verification.verified_at is not None:
            raise EmailVerificationError("Bu e-posta adresi zaten doğrulanmış.", code="LS-AUTH-11", status_code=409)
        if (_as_utc(verification.expires_at) or now) <= now:
            raise EmailVerificationError("Kodun süresi dolmuş. Yeni bir kod iste.", code="LS-AUTH-07")
        if int(verification.attempt_count) >= int(config.EMAIL_VERIFICATION_MAX_ATTEMPTS):
            raise EmailVerificationError(
                "Çok fazla hatalı deneme yapıldı. Yeni bir kod iste.",
                code="LS-AUTH-10",
                status_code=429,
            )
        expected = _hash_code(user.id, normalized_code)
        failure: EmailVerificationError | None = None
        if not hmac.compare_digest(expected, verification.code_hash):
            attempts = int(verification.attempt_count) + 1
            connection.execute(
                update(EMAIL_VERIFICATIONS)
                .where(EMAIL_VERIFICATIONS.c.user_id == user.id)
                .values(attempt_count=attempts, updated_at=now)
            )
            failure = (
                EmailVerificationError(
                    "Çok fazla hatalı deneme yapıldı. Yeni bir kod iste.",
                    code="LS-AUTH-10",
                    status_code=429,
                )
                if attempts >= int(config.EMAIL_VERIFICATION_MAX_ATTEMPTS)
                else EmailVerificationError("Kod hatalı. Tekrar kontrol et.", code="LS-AUTH-08")
            )
            user_id = ""
            user_email = ""
        else:
            connection.execute(
                update(EMAIL_VERIFICATIONS)
                .where(EMAIL_VERIFICATIONS.c.user_id == user.id)
                .values(code_hash="", verified_at=now, attempt_count=0, updated_at=now)
            )
            user_id = user.id
            user_email = user.email
    if failure:
        raise failure
    token = issue_session(user_id, user_email)
    return {
        "user": {"id": user_id, "email": user_email},
        "token": token,
        "account": account_status(user_id),
    }


def resend_email_verification(email: str, language: str = "tr") -> dict:
    if not _email_required():
        return {"verification_required": False}
    if not email_delivery_available():
        raise EmailVerificationError(
            "E-posta doğrulama servisi henüz etkinleştirilmemiş.",
            code="LS-AUTH-00",
            status_code=503,
        )
    normalized = _normalize_email(email)
    init_email_verification_database()
    now = utcnow()
    new_code = _new_code()
    new_hash = ""
    restore: dict | None = None
    user_id = ""
    with ENGINE.begin() as connection:
        user = connection.execute(select(USERS).where(USERS.c.email == normalized)).first()
        verification = (
            connection.execute(
                select(EMAIL_VERIFICATIONS)
                .where(EMAIL_VERIFICATIONS.c.user_id == user.id)
                .with_for_update()
            ).first()
            if user
            else None
        )
        # Do not reveal whether an arbitrary email address has an account.
        if not user or not verification or verification.verified_at is not None:
            return {
                "verification_required": True,
                "email": normalized,
                "expires_in": int(config.EMAIL_VERIFICATION_TTL_SECONDS),
                "resend_after": int(config.EMAIL_VERIFICATION_RESEND_SECONDS),
            }
        last_sent = _as_utc(verification.last_sent_at)
        cooldown = int(config.EMAIL_VERIFICATION_RESEND_SECONDS)
        if last_sent and (now - last_sent).total_seconds() < cooldown:
            retry_after = max(1, cooldown - int((now - last_sent).total_seconds()))
            raise EmailVerificationError(
                f"Yeni kod istemeden önce {retry_after} saniye bekle.",
                code="LS-AUTH-09",
                status_code=429,
                retry_after=retry_after,
            )
        window_start = _as_utc(verification.window_started_at) or now
        send_count = int(verification.send_count)
        if now - window_start >= timedelta(hours=1):
            window_start = now
            send_count = 0
        if send_count >= int(config.EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR):
            retry_after = max(1, int((window_start + timedelta(hours=1) - now).total_seconds()))
            raise EmailVerificationError(
                "Çok fazla doğrulama kodu istendi. Daha sonra tekrar dene.",
                code="LS-AUTH-09",
                status_code=429,
                retry_after=retry_after,
            )
        user_id = user.id
        new_hash = _hash_code(user_id, new_code)
        restore = {
            "code_hash": verification.code_hash,
            "expires_at": verification.expires_at,
            "last_sent_at": verification.last_sent_at,
            "window_started_at": verification.window_started_at,
            "send_count": verification.send_count,
            "attempt_count": verification.attempt_count,
            "updated_at": verification.updated_at,
        }
        connection.execute(
            update(EMAIL_VERIFICATIONS)
            .where(EMAIL_VERIFICATIONS.c.user_id == user_id)
            .values(
                code_hash=new_hash,
                expires_at=now + timedelta(seconds=int(config.EMAIL_VERIFICATION_TTL_SECONDS)),
                last_sent_at=now,
                window_started_at=window_start,
                send_count=send_count + 1,
                attempt_count=0,
                updated_at=now,
            )
        )
    try:
        _send_verification_email(normalized, new_code, language, f"verify-resend/{user_id}/{uuid.uuid4().hex}")
    except EmailDeliveryError as exc:
        if restore:
            with ENGINE.begin() as connection:
                connection.execute(
                    update(EMAIL_VERIFICATIONS)
                    .where(
                        EMAIL_VERIFICATIONS.c.user_id == user_id,
                        EMAIL_VERIFICATIONS.c.code_hash == new_hash,
                    )
                    .values(**restore)
                )
        raise EmailVerificationError(
            "Doğrulama e-postası gönderilemedi. Biraz sonra tekrar dene.",
            code="LS-AUTH-04",
            status_code=503,
        ) from exc
    return _public_verification(normalized)


def _raise_http(exc: EmailVerificationError) -> None:
    detail: dict = {"code": exc.code, "message": str(exc)}
    if exc.retry_after is not None:
        detail["retry_after"] = exc.retry_after
    raise HTTPException(exc.status_code, detail=detail) from exc


def billing_register_verified(payload: EmailAuthRequest) -> dict:
    try:
        result = register_with_email_verification(payload.email, payload.password, payload.language)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except EmailVerificationError as exc:
        _raise_http(exc)
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-05", "message": str(exc)}) from exc
    return {"ok": True, **result}


def billing_login_verified(payload: EmailAuthRequest) -> dict:
    try:
        result = login_with_email_verification(payload.email, payload.password)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except EmailVerificationError as exc:
        _raise_http(exc)
    except BillingAuthenticationError as exc:
        raise HTTPException(401, detail={"code": "LS-BILL-06", "message": str(exc)}) from exc
    return {"ok": True, **result}


@router.post("/billing/verify-email")
def billing_verify_email(payload: VerifyEmailRequest) -> dict:
    try:
        result = verify_email_code(payload.email, payload.code)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except EmailVerificationError as exc:
        _raise_http(exc)
    return {"ok": True, **result}


@router.post("/billing/resend-verification")
def billing_resend_verification(payload: ResendVerificationRequest) -> dict:
    try:
        result = resend_email_verification(payload.email, payload.language)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except EmailVerificationError as exc:
        _raise_http(exc)
    return {"ok": True, **result}


@router.get("/billing/email/health")
def billing_email_health() -> dict:
    database_ready = False
    database_error: str | None = None
    try:
        init_email_verification_database()
        database_ready = True
    except BillingConfigurationError as exc:
        database_error = str(exc)
    return {
        "ok": database_ready and (email_delivery_available() or not _email_required()),
        "provider": "resend",
        "configured": email_delivery_available(),
        "verification_required": _email_required(),
        "sender": config.RESEND_FROM_EMAIL if email_delivery_available() else None,
        "database": {"ready": database_ready, "error": database_error},
    }


def install_email_auth(app: FastAPI) -> FastAPI:
    """Replace legacy register/login routes and add verification endpoints."""
    if getattr(app.state, "email_auth_installed", False):
        return app
    replaced_paths = {"/billing/register", "/billing/login"}
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) in replaced_paths
            and "POST" in (getattr(route, "methods", None) or set())
        )
    ]
    app.add_api_route("/billing/register", billing_register_verified, methods=["POST"], tags=["billing-email"])
    app.add_api_route("/billing/login", billing_login_verified, methods=["POST"], tags=["billing-email"])
    app.include_router(router)
    app.state.email_auth_installed = True
    app.openapi_schema = None
    return app
