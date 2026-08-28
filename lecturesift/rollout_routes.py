"""FastAPI routes for the complete LectureSift product rollout."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel

from . import config
from .billing_service import BillingAuthenticationError, BillingConfigurationError, BillingError, authenticate_session
from .jobs import JOBS
from .mailer import EmailDeliveryError
from .queue import worker_health
from .security import RATE_LIMITER, RateLimitExceeded
from .rollout_service import (
    claim_instagram_reward,
    close_user_account,
    create_or_resume_guest,
    decide_admin_order,
    decide_instagram_reward,
    export_account_data,
    instagram_reward_for_user,
    is_guest_user,
    list_admin_orders,
    list_admin_rewards,
    request_email_change,
    issue_rewarded_ad_session,
    redeem_rewarded_ad_session,
    rewarded_ads_for_user,
    update_profile,
    verify_email_change,
)
from .storage import STORAGE


router = APIRouter(tags=["product-rollout"])


class GuestSessionRequest(BaseModel):
    device_id: str


class ProfileRequest(BaseModel):
    first_name: str
    last_name: str
    phone: str = ""


class EmailChangeRequest(BaseModel):
    email: str


class EmailChangeVerifyRequest(BaseModel):
    code: str = ""
    token: str = ""


class CloseAccountRequest(BaseModel):
    current_password: str
    email_confirmation: str


class InstagramRewardRequest(BaseModel):
    handle: str


class RewardedAdClaimRequest(BaseModel):
    session_id: str
    claim_token: str


class DecisionRequest(BaseModel):
    approve: bool


def _user(authorization: str | None = Header(None)) -> dict:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not token:
        raise HTTPException(401, detail={"code": "LS-BILL-01", "message": "Devam etmek için giriş yap."})
    try:
        return authenticate_session(token)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except BillingAuthenticationError as exc:
        raise HTTPException(401, detail={"code": "LS-BILL-02", "message": str(exc)}) from exc


def _admin(authorization: str | None = Header(None)) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    if not config.BILLING_ADMIN_TOKEN and not config.BILLING_ADMIN_EMAILS:
        raise HTTPException(503, detail={"code": "LS-BILL-03", "message": "Admin paneli henüz etkin değil."})
    if scheme.casefold() == "bearer" and token:
        if config.BILLING_ADMIN_TOKEN and hmac.compare_digest(token, config.BILLING_ADMIN_TOKEN):
            return
        try:
            user = authenticate_session(token)
            if user["email"].casefold() in config.BILLING_ADMIN_EMAILS:
                return
        except (BillingAuthenticationError, BillingConfigurationError):
            pass
    raise HTTPException(401, detail={"code": "LS-BILL-04", "message": "Admin yetkisi gerekli."})


def _billing_failure(exc: Exception, code: str = "LS-BILL-22") -> None:
    status = 503 if isinstance(exc, (BillingConfigurationError, EmailDeliveryError)) else 400
    raise HTTPException(status, detail={"code": code, "message": str(exc)}) from exc


@router.get("/rollout/health")
def rollout_health() -> dict:
    queue = JOBS.redis_health()
    storage = STORAGE.health()
    worker = worker_health() if queue["connected"] else {
        "configured": bool(config.CELERY_BROKER_URL),
        "reachable": False,
        "workers": 0,
    }
    return {
        "ok": True,
        "guest_trial_minutes": config.GUEST_TRIAL_MAX_MINUTES,
        "instagram_bonus_minutes": config.INSTAGRAM_BONUS_MINUTES,
        "contact_email": config.CONTACT_EMAIL,
        "durable_queue_configured": bool(config.CELERY_BROKER_URL),
        "durable_processing_required": config.REQUIRE_DURABLE_PROCESSING,
        "object_storage_configured": bool(
            config.S3_ENDPOINT_URL
            and config.S3_BUCKET
            and config.S3_ACCESS_KEY_ID
            and config.S3_SECRET_ACCESS_KEY
        ),
        "queue": queue,
        "storage": storage,
        "worker": worker,
        "durable_processing_ready": bool(
            config.CELERY_BROKER_URL
            and queue["connected"]
            and storage["connected"]
            and worker["reachable"]
        ),
    }


@router.post("/billing/guest-session")
def billing_guest_session(payload: GuestSessionRequest, request: Request) -> dict:
    device_id = payload.device_id.strip()
    if len(device_id) < 8 or len(device_id) > 200:
        raise HTTPException(400, detail={"code": "LS-GUEST-01", "message": "Misafir cihaz kimliği geçersiz."})
    try:
        RATE_LIMITER.check(
            "guest-session",
            device_id,
            limit=12,
            window_seconds=60 * 60,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            429,
            detail={"code": "LS-SEC-01", "message": str(exc)},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    user_agent = request.headers.get("user-agent", "unknown")[:300]
    # Keep the trial tied to this browser/device even when the user changes Wi-Fi or mobile network.
    fingerprint = hashlib.sha256(f"{device_id}|{user_agent}".encode("utf-8")).hexdigest()
    try:
        result = create_or_resume_guest(fingerprint)
    except (BillingError, BillingAuthenticationError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-GUEST-02")
    return {
        "ok": True,
        "guest": True,
        "trial_minutes": config.GUEST_TRIAL_MAX_MINUTES,
        **result,
    }


@router.get("/billing/me/rollout")
def billing_rollout_account(user: dict = Depends(_user)) -> dict:
    return {
        "ok": True,
        "guest": is_guest_user(user["id"]),
        "instagram_reward": instagram_reward_for_user(user["id"]),
        "rewarded_ads": rewarded_ads_for_user(user["id"]),
        "contact_email": config.CONTACT_EMAIL,
    }


@router.get("/billing/me/export")
def billing_export_account(user: dict = Depends(_user)) -> dict:
    if is_guest_user(user["id"]):
        raise HTTPException(403, detail={"code": "LS-GUEST-03", "message": "Veri dışa aktarma için hesap oluştur."})
    try:
        exported = export_account_data(user["id"])
    except (BillingError, BillingAuthenticationError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-BILL-26")
    jobs = []
    for item in JOBS.list_for_user(user["id"], limit=100):
        public = JOBS.public(str(item.get("job_id", "")))
        if public:
            jobs.append(public)
    exported["jobs"] = jobs
    return {"ok": True, "export": exported}


@router.post("/billing/me/close-account")
def billing_close_account(payload: CloseAccountRequest, user: dict = Depends(_user)) -> dict:
    if is_guest_user(user["id"]):
        raise HTTPException(403, detail={"code": "LS-GUEST-03", "message": "Misafir oturumu hesap kapatma gerektirmez."})
    try:
        result = close_user_account(
            user["id"],
            payload.current_password,
            payload.email_confirmation,
        )
    except (BillingError, BillingAuthenticationError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-BILL-27")
    cleanup = JOBS.delete_for_user(user["id"])
    return {"ok": True, **result, "deleted_jobs": cleanup["jobs"]}


@router.patch("/billing/me/profile")
def billing_update_profile(payload: ProfileRequest, user: dict = Depends(_user)) -> dict:
    if is_guest_user(user["id"]):
        raise HTTPException(403, detail={"code": "LS-GUEST-03", "message": "Profili düzenlemek için ücretsiz hesap oluştur."})
    try:
        account = update_profile(user["id"], payload.first_name, payload.last_name, payload.phone)
    except (BillingError, BillingAuthenticationError) as exc:
        _billing_failure(exc)
    return {"ok": True, "message": "Profil bilgilerin kaydedildi.", "account": account}


@router.post("/billing/me/email-change")
def billing_request_email_change(payload: EmailChangeRequest, user: dict = Depends(_user)) -> dict:
    if is_guest_user(user["id"]):
        raise HTTPException(403, detail={"code": "LS-GUEST-03", "message": "E-posta değiştirmek için hesap oluştur."})
    try:
        result = request_email_change(user["id"], payload.email)
    except (BillingError, BillingAuthenticationError, BillingConfigurationError, EmailDeliveryError) as exc:
        _billing_failure(exc, "LS-BILL-23")
    result.pop("token", None)
    return {**result, "message": "Yeni e-posta adresine altı haneli doğrulama kodu gönderildi."}


@router.post("/billing/me/email-change/verify")
def billing_verify_email_change(payload: EmailChangeVerifyRequest, user: dict = Depends(_user)) -> dict:
    try:
        result = verify_email_change(user["id"], payload.code, payload.token)
    except (BillingError, BillingAuthenticationError) as exc:
        _billing_failure(exc, "LS-BILL-24")
    return {"ok": True, "message": "E-posta adresin değiştirildi.", **result}


@router.get("/billing/instagram-reward")
def billing_instagram_reward(user: dict = Depends(_user)) -> dict:
    return {
        "ok": True,
        "minutes": config.INSTAGRAM_BONUS_MINUTES,
        "reward": instagram_reward_for_user(user["id"]),
    }


@router.post("/billing/instagram-reward")
def billing_claim_instagram_reward(payload: InstagramRewardRequest, user: dict = Depends(_user)) -> dict:
    if is_guest_user(user["id"]):
        raise HTTPException(403, detail={"code": "LS-GUEST-03", "message": "Instagram bonusu için ücretsiz hesap oluştur."})
    try:
        reward = claim_instagram_reward(user["id"], payload.handle)
    except BillingError as exc:
        _billing_failure(exc, "LS-IG-BONUS-01")
    return {
        "ok": True,
        "message": "Takip talebin doğrulama sırasına alındı. Onaylanınca dakika bakiyene eklenecek.",
        "reward": reward,
    }


@router.get("/billing/rewarded-ads")
def billing_rewarded_ads(user: dict = Depends(_user)) -> dict:
    return {"ok": True, "rewarded_ads": rewarded_ads_for_user(user["id"])}


@router.post("/billing/rewarded-ads/session")
def billing_rewarded_ad_session(user: dict = Depends(_user)) -> dict:
    try:
        RATE_LIMITER.check("rewarded-ad-session", user["id"], limit=20, window_seconds=24 * 60 * 60)
        session = issue_rewarded_ad_session(user["id"])
    except RateLimitExceeded as exc:
        raise HTTPException(
            429,
            detail={"code": "LS-ADS-03", "message": str(exc)},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADS-01")
    return {"ok": True, "session": session}


@router.post("/billing/rewarded-ads/claim")
def billing_rewarded_ad_claim(payload: RewardedAdClaimRequest, user: dict = Depends(_user)) -> dict:
    try:
        result = redeem_rewarded_ad_session(user["id"], payload.session_id, payload.claim_token)
    except (BillingError, BillingAuthenticationError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADS-02")
    return {"ok": True, "message": f"{result['minutes_added']} dakika hesabına eklendi.", **result}


@router.get("/admin/manual-orders", dependencies=[Depends(_admin)])
def admin_manual_orders(status: str = Query("pending")) -> dict:
    return {"ok": True, "orders": list_admin_orders(status.strip())}


@router.post("/admin/manual-orders/{reference}/decision", dependencies=[Depends(_admin)])
def admin_manual_order_decision(reference: str, payload: DecisionRequest) -> dict:
    try:
        result = decide_admin_order(reference, payload.approve)
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-BILL-09")
    return {"ok": True, "result": result}


@router.get("/admin/instagram-rewards", dependencies=[Depends(_admin)])
def admin_instagram_rewards(status: str = Query("pending_verification")) -> dict:
    return {"ok": True, "rewards": list_admin_rewards(status.strip())}


@router.post("/admin/instagram-rewards/{reward_id}/decision", dependencies=[Depends(_admin)])
def admin_instagram_reward_decision(reward_id: str, payload: DecisionRequest) -> dict:
    try:
        result = decide_instagram_reward(reward_id, payload.approve)
    except BillingError as exc:
        _billing_failure(exc, "LS-IG-BONUS-02")
    return {"ok": True, "result": result}


def install_rollout_routes(app: FastAPI) -> None:
    if any(getattr(route, "path", None) == "/rollout/health" for route in app.routes):
        return
    app.include_router(router)
