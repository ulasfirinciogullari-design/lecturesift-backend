"""FastAPI routes for the complete LectureSift product rollout."""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from datetime import date

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel

from . import config
from .billing_service import BillingAuthenticationError, BillingConfigurationError, BillingError, authenticate_session
from .costs import cost_overview, delete_actual_cost, save_actual_cost
from .jobs import JOBS
from .mailer import EmailDeliveryError
from .queue import worker_health
from .security import RATE_LIMITER, RateLimitExceeded
from .rollout_service import (
    admin_close_user_account,
    admin_user_identity,
    admin_revoke_user_sessions,
    admin_set_user_subscription,
    admin_update_user,
    adjust_admin_credit,
    claim_instagram_reward,
    close_user_account,
    create_contact_message,
    create_refund_request,
    create_or_resume_guest,
    decide_admin_order,
    decide_instagram_reward,
    decide_refund_request,
    export_account_data,
    guest_trial_status,
    instagram_reward_for_user,
    is_guest_user,
    list_admin_orders,
    list_admin_orders_page,
    list_admin_user_activity,
    list_admin_users_page,
    list_admin_credit_events,
    list_admin_account_events,
    list_admin_refund_requests,
    list_admin_rewards,
    list_contact_messages,
    refund_requests_for_user,
    request_email_change,
    issue_rewarded_ad_session,
    redeem_rewarded_ad_session,
    rewarded_ads_for_user,
    update_profile,
    update_contact_message_status,
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


class RefundRequest(BaseModel):
    order_reference: str
    reason: str


class RefundDecisionRequest(BaseModel):
    action: str
    note: str = ""


class CreditAdjustmentRequest(BaseModel):
    minutes_delta: int
    reason: str


class ContactMessageRequest(BaseModel):
    name: str
    email: str
    topic: str
    message: str
    order_reference: str = ""


class ContactStatusRequest(BaseModel):
    status: str


class AdminUserUpdateRequest(BaseModel):
    email: str
    first_name: str
    last_name: str
    phone: str = ""
    country_code: str = "TR"
    preferred_language: str = "tr"
    email_verified: bool = True


class AdminSubscriptionRequest(BaseModel):
    plan_code: str
    interval: str = "monthly"
    duration_days: int = 30


class AdminAccountCloseRequest(BaseModel):
    confirmation_email: str
    reason: str


class AdminBulkUserRequest(BaseModel):
    user_ids: list[str]
    action: str
    confirmation: str = ""
    reason: str = ""
    minutes_delta: int = 0
    plan_code: str = "free"
    interval: str = "monthly"
    duration_days: int = 30


class AdminActualCostRequest(BaseModel):
    provider: str
    service: str
    period_start: date
    period_end: date
    currency: str
    subtotal_minor: int
    tax_minor: int = 0
    label: str
    source_reference: str


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


def _admin(authorization: str | None = Header(None)) -> dict:
    scheme, _, token = (authorization or "").partition(" ")
    if not config.ADMIN_ADMIN:
        raise HTTPException(503, detail={"code": "LS-BILL-03", "message": "Admin paneli henüz etkin değil."})
    if scheme.casefold() == "bearer" and token:
        if hmac.compare_digest(token, config.ADMIN_ADMIN):
            return {"actor": "admin_token"}
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
        "analytics_configured": bool(
            config.ANALYTICS_ENABLED and re.fullmatch(r"G-[A-Z0-9]+", config.GA_MEASUREMENT_ID)
        ),
        "google_ads_conversion_configured": bool(
            re.fullmatch(r"AW-[0-9]+", config.GOOGLE_ADS_ID)
            and config.GOOGLE_ADS_SIGNUP_LABEL
            and config.GOOGLE_ADS_PURCHASE_LABEL
        ),
        "display_ads_configured": bool(
            (config.DISPLAY_ADS_ENABLED and config.DISPLAY_AD_UNIT_PATH)
            or re.fullmatch(r"ca-pub-[0-9]+", config.ADSENSE_PUBLISHER_ID)
        ),
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
        "recovery": {
            "database_managed_backup_confirmed": config.DATABASE_RECOVERY_CONFIRMED,
            "object_retention_confirmed": config.OBJECT_RETENTION_CONFIRMED,
            "restore_drill_confirmed": config.RECOVERY_DRILL_CONFIRMED,
            "ready": bool(
                config.DATABASE_RECOVERY_CONFIRMED
                and config.OBJECT_RETENTION_CONFIRMED
                and config.RECOVERY_DRILL_CONFIRMED
            ),
        },
    }


@router.get("/ads/config")
def ads_config() -> dict:
    configured = bool(config.DISPLAY_ADS_ENABLED and config.DISPLAY_AD_UNIT_PATH)
    return {
        "enabled": configured,
        "provider": "google_gpt" if configured else None,
        "banner_unit_path": config.DISPLAY_AD_UNIT_PATH if configured else None,
        "consent_required": True,
        "paid_plans_ad_free": True,
        "adsense_auto_ads": {
            "enabled": bool(re.fullmatch(r"ca-pub-[0-9]+", config.ADSENSE_PUBLISHER_ID)),
            "publisher_id": (
                config.ADSENSE_PUBLISHER_ID
                if re.fullmatch(r"ca-pub-[0-9]+", config.ADSENSE_PUBLISHER_ID)
                else None
            ),
        },
        "house_campaign": {
            "enabled": bool(
                config.SITE_BANNER_ENABLED
                and config.SITE_BANNER_TITLE
                and config.SITE_BANNER_TEXT
                and config.SITE_BANNER_CTA
                and config.SITE_BANNER_URL.startswith("/")
            ),
            "title": config.SITE_BANNER_TITLE,
            "text": config.SITE_BANNER_TEXT,
            "cta": config.SITE_BANNER_CTA,
            "url": config.SITE_BANNER_URL if config.SITE_BANNER_URL.startswith("/") else "/plans.html",
        },
    }


@router.get("/analytics/config")
def analytics_config() -> dict:
    configured = bool(
        config.ANALYTICS_ENABLED and re.fullmatch(r"G-[A-Z0-9]+", config.GA_MEASUREMENT_ID)
    )
    ads_configured = bool(re.fullmatch(r"AW-[0-9]+", config.GOOGLE_ADS_ID))
    return {
        "enabled": configured,
        "provider": "google_analytics_4" if configured else None,
        "measurement_id": config.GA_MEASUREMENT_ID if configured else None,
        "consent_required": True,
        "advertising_signals": False,
        "google_ads": {
            "enabled": ads_configured,
            "id": config.GOOGLE_ADS_ID if ads_configured else None,
            "signup_label": config.GOOGLE_ADS_SIGNUP_LABEL if ads_configured and config.GOOGLE_ADS_SIGNUP_LABEL else None,
            "purchase_label": config.GOOGLE_ADS_PURCHASE_LABEL if ads_configured and config.GOOGLE_ADS_PURCHASE_LABEL else None,
        },
    }


@router.post("/contact/messages")
def submit_contact_message(payload: ContactMessageRequest, request: Request) -> dict:
    client_key = request.client.host if request.client else "unknown"
    try:
        RATE_LIMITER.check("contact-message", client_key, limit=5, window_seconds=60 * 60)
        result = create_contact_message(
            payload.name,
            payload.email,
            payload.topic,
            payload.message,
            payload.order_reference,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            429,
            detail={"code": "LS-CONTACT-02", "message": str(exc)},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-CONTACT-01")
    return {
        "ok": True,
        "message": "Mesajın alındı. Destek ekibi en kısa sürede e-posta adresinden dönecek.",
        "reference": result["id"],
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
        "guest_trial": guest_trial_status(user["id"]),
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


@router.get("/billing/me/refund-requests")
def billing_refund_requests(user: dict = Depends(_user)) -> dict:
    if is_guest_user(user["id"]):
        raise HTTPException(403, detail={"code": "LS-GUEST-03", "message": "İade talebi için hesap oluştur."})
    return {"ok": True, "requests": refund_requests_for_user(user["id"])}


@router.post("/billing/me/refund-requests")
def billing_create_refund_request(payload: RefundRequest, user: dict = Depends(_user)) -> dict:
    if is_guest_user(user["id"]):
        raise HTTPException(403, detail={"code": "LS-GUEST-03", "message": "İade talebi için hesap oluştur."})
    try:
        result = create_refund_request(user["id"], payload.order_reference, payload.reason)
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-REFUND-01")
    return {
        "ok": True,
        "message": "İade talebin oluşturuldu. Sonucu hesabından takip edebilirsin.",
        "request": result,
    }


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


@router.get("/billing/admin/users")
def admin_users_page(
    search: str = Query("", max_length=160),
    verification: str = Query("all"),
    plan: str = Query("all"),
    sort: str = Query("created_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=100),
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    try:
        result = list_admin_users_page(
            search=search,
            verification=verification,
            plan_code=plan,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADMIN-06")
    return {"ok": True, "users": result["items"], "pagination": result["pagination"]}


@router.get("/billing/admin/orders")
def admin_orders_page(
    search: str = Query("", max_length=160),
    status: str = Query("all"),
    provider: str = Query("all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=100),
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    try:
        result = list_admin_orders_page(
            search=search,
            status=status,
            provider=provider,
            page=page,
            page_size=page_size,
        )
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADMIN-07")
    return {"ok": True, "orders": result["items"], "pagination": result["pagination"]}


@router.get("/billing/admin/users/{user_id}/activity")
def admin_user_activity(
    user_id: str,
    limit: int = Query(30, ge=1, le=100),
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    try:
        activity = list_admin_user_activity(user_id, limit)
    except BillingError as exc:
        _billing_failure(exc, "LS-ADMIN-09")
    return {
        "ok": True,
        "activity": activity,
        "privacy": {
            "full_ip_stored": False,
            "retention_days": config.ACCOUNT_ACTIVITY_RETENTION_DAYS,
        },
    }


def _bulk_confirmation(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold().strip()


@router.post("/billing/admin/users/bulk-action")
def admin_users_bulk_action(
    payload: AdminBulkUserRequest,
    admin: dict = Depends(_admin),
) -> dict:
    unique_ids = list(dict.fromkeys(item.strip() for item in payload.user_ids if item.strip()))
    if not unique_ids or len(unique_ids) > 100:
        raise HTTPException(400, detail={"code": "LS-ADMIN-08", "message": "Bir işlemde 1 ile 100 kullanıcı seç."})
    action = payload.action.strip().lower()
    if action not in {"credit", "subscription", "revoke_sessions", "delete"}:
        raise HTTPException(400, detail={"code": "LS-ADMIN-08", "message": "Geçersiz toplu işlem."})
    reason = payload.reason.strip()
    if action in {"credit", "delete"} and len(reason) < 4:
        raise HTTPException(400, detail={"code": "LS-ADMIN-08", "message": "İşlem nedenini en az dört karakterle yaz."})
    if action == "delete" and _bulk_confirmation(payload.confirmation) != "sil":
        raise HTTPException(400, detail={"code": "LS-ADMIN-08", "message": "Toplu hesap kapatma için SİL yaz."})
    if action == "credit" and (payload.minutes_delta == 0 or abs(payload.minutes_delta) > 10_000):
        raise HTTPException(400, detail={"code": "LS-ADMIN-08", "message": "Dakika değişimi -10.000 ile 10.000 arasında ve sıfırdan farklı olmalı."})
    results = []
    for user_id in unique_ids:
        try:
            identity = admin_user_identity(user_id)
            if action == "credit":
                result = adjust_admin_credit(user_id, payload.minutes_delta, reason, admin["actor"])
            elif action == "subscription":
                result = admin_set_user_subscription(
                    user_id,
                    plan_code=payload.plan_code,
                    interval=payload.interval,
                    duration_days=payload.duration_days,
                    actor=admin["actor"],
                )
            elif action == "revoke_sessions":
                result = admin_revoke_user_sessions(user_id, admin["actor"])
            else:
                result = admin_close_user_account(
                    user_id,
                    confirmation_email=identity["email"],
                    reason=reason,
                    actor=admin["actor"],
                )
                cleanup = JOBS.delete_for_user(user_id)
                result = {**result, "deleted_jobs": cleanup["jobs"]}
            results.append({"user_id": user_id, "email": identity["email"], "ok": True, "result": result})
        except (BillingError, BillingConfigurationError) as exc:
            results.append({"user_id": user_id, "ok": False, "message": str(exc)})
    succeeded = sum(1 for item in results if item["ok"])
    return {
        "ok": succeeded > 0,
        "message": f"{succeeded} kullanıcı için işlem tamamlandı; {len(results) - succeeded} işlem uygulanamadı.",
        "processed": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


@router.get("/billing/admin/refund-requests")
def admin_refund_requests(
    status: str = Query(""),
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    return {"ok": True, "requests": list_admin_refund_requests(status.strip())}


@router.post("/billing/admin/refund-requests/{request_id}/decision")
def admin_refund_request_decision(
    request_id: str,
    payload: RefundDecisionRequest,
    admin: dict = Depends(_admin),
) -> dict:
    try:
        result = decide_refund_request(request_id, payload.action, payload.note, admin["actor"])
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-REFUND-02")
    return {"ok": True, "result": result}


@router.get("/billing/admin/credit-events")
def admin_credit_events(limit: int = Query(100, ge=1, le=250), admin: dict = Depends(_admin)) -> dict:
    del admin
    return {"ok": True, "events": list_admin_credit_events(limit)}


@router.get("/billing/admin/jobs")
def admin_processing_jobs(
    limit: int = Query(100, ge=1, le=250),
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    jobs = JOBS.list_for_admin(limit)
    counts: dict[str, int] = {}
    for item in jobs:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "ok": True,
        "counts": counts,
        "jobs": jobs,
        "worker": worker_health(),
    }


@router.get("/billing/admin/costs")
def admin_costs(
    days: int = Query(30, ge=1, le=3660),
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    return {"ok": True, **cost_overview(days=days, limit=limit)}


@router.post("/billing/admin/costs/actuals")
def admin_save_actual_cost(
    payload: AdminActualCostRequest,
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    try:
        values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        result = save_actual_cost(**values)
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "LS-COST-01", "message": str(exc)}) from exc
    return {"ok": True, **result, "message": "Fatura/mutabakat gideri kaydedildi."}


@router.delete("/billing/admin/costs/actuals/{actual_id}")
def admin_delete_actual_cost(
    actual_id: str,
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    if not delete_actual_cost(actual_id):
        raise HTTPException(404, detail={"code": "LS-COST-02", "message": "Gider kaydı bulunamadı."})
    return {"ok": True, "message": "Fatura/mutabakat gideri silindi."}


@router.get("/billing/admin/contact-messages")
def admin_contact_messages(
    status: str = Query(""),
    limit: int = Query(100, ge=1, le=250),
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    try:
        messages = list_contact_messages(status, limit)
    except BillingError as exc:
        _billing_failure(exc, "LS-CONTACT-03")
    return {"ok": True, "messages": messages}


@router.post("/billing/admin/contact-messages/{message_id}/status")
def admin_contact_message_status(
    message_id: str,
    payload: ContactStatusRequest,
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    try:
        result = update_contact_message_status(message_id, payload.status)
    except BillingError as exc:
        _billing_failure(exc, "LS-CONTACT-04")
    return {"ok": True, "message": result}


@router.post("/billing/admin/users/{user_id}/credit-adjustment")
def admin_credit_adjustment(
    user_id: str,
    payload: CreditAdjustmentRequest,
    admin: dict = Depends(_admin),
) -> dict:
    try:
        result = adjust_admin_credit(user_id, payload.minutes_delta, payload.reason, admin["actor"])
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADMIN-01")
    return {"ok": True, "message": "Dakika bakiyesi güncellendi ve işlem kaydedildi.", "event": result}


@router.patch("/billing/admin/users/{user_id}")
def admin_user_update(
    user_id: str,
    payload: AdminUserUpdateRequest,
    admin: dict = Depends(_admin),
) -> dict:
    try:
        account = admin_update_user(
            user_id,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
            country_code=payload.country_code,
            preferred_language=payload.preferred_language,
            email_verified=payload.email_verified,
            actor=admin["actor"],
        )
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADMIN-02")
    return {"ok": True, "message": "Kullanıcı profili güncellendi; eski oturumlar kapatıldı.", "account": account}


@router.post("/billing/admin/users/{user_id}/subscription")
def admin_user_subscription(
    user_id: str,
    payload: AdminSubscriptionRequest,
    admin: dict = Depends(_admin),
) -> dict:
    try:
        account = admin_set_user_subscription(
            user_id,
            plan_code=payload.plan_code,
            interval=payload.interval,
            duration_days=payload.duration_days,
            actor=admin["actor"],
        )
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADMIN-03")
    return {"ok": True, "message": "Kullanıcının abonelik hakları güncellendi.", "account": account}


@router.post("/billing/admin/users/{user_id}/revoke-sessions")
def admin_user_revoke_sessions(user_id: str, admin: dict = Depends(_admin)) -> dict:
    try:
        result = admin_revoke_user_sessions(user_id, admin["actor"])
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADMIN-04")
    return {"ok": True, "message": "Kullanıcının tüm açık oturumları kapatıldı.", **result}


@router.delete("/billing/admin/users/{user_id}")
def admin_user_close(
    user_id: str,
    payload: AdminAccountCloseRequest,
    admin: dict = Depends(_admin),
) -> dict:
    try:
        result = admin_close_user_account(
            user_id,
            confirmation_email=payload.confirmation_email,
            reason=payload.reason,
            actor=admin["actor"],
        )
    except (BillingError, BillingConfigurationError) as exc:
        _billing_failure(exc, "LS-ADMIN-05")
    cleanup = JOBS.delete_for_user(user_id)
    return {
        "ok": True,
        "message": "Hesap kapatıldı, kimlik bilgileri anonimleştirildi ve ders dosyaları silindi.",
        "deleted_jobs": cleanup["jobs"],
        **result,
    }


@router.get("/billing/admin/account-events")
def admin_account_events(
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(_admin),
) -> dict:
    del admin
    return {"ok": True, "events": list_admin_account_events(limit)}


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
