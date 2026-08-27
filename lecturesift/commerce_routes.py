"""FastAPI routes for PayTR checkout, billing history, refunds, and output lifecycle."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from . import config
from .billing_service import BillingAuthenticationError, BillingConfigurationError, BillingError, authenticate_session
from .commerce import account_commerce_status, accept_payment_event, admin_refunds, cancel_account_deletion, create_purchase, mark_job_deleted, mark_purchase_failed, purchase_for_reference, refund_for_admin, request_account_deletion, request_refund, set_cancel_at_period_end, update_refund_status
from .jobs import JOBS
from .paytr import PayTRConfigurationError, PayTRRequestError, create_iframe_checkout, public_status as paytr_status, request_refund as paytr_request_refund, validate_callback
from .storage import STORAGE


router = APIRouter(tags=["commerce"])


class CheckoutRequest(BaseModel):
    plan_code: str
    interval: str = "monthly"
    currency: str = "TRY"


class SubscriptionControlRequest(BaseModel):
    cancel_at_period_end: bool


class RefundRequest(BaseModel):
    purchase_reference: str
    reason: str


class AdminRefundRequest(BaseModel):
    approve: bool
    amount_minor: int | None = None


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
    if not config.BILLING_ADMIN_TOKEN:
        raise HTTPException(503, detail={"code": "LS-BILL-03", "message": "Admin paneli etkin değil."})
    if scheme.casefold() != "bearer" or not hmac.compare_digest(token, config.BILLING_ADMIN_TOKEN):
        raise HTTPException(401, detail={"code": "LS-BILL-04", "message": "Admin yetkisi gerekli."})


def _legal_identity_ready() -> bool:
    return all((config.LEGAL_ENTITY_NAME, config.LEGAL_ADDRESS, config.LEGAL_TAX_ID, config.LEGAL_EMAIL))


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "127.0.0.1")


@router.get("/billing/commerce/health")
def commerce_health() -> dict[str, Any]:
    return {
        "ok": True,
        "paytr": paytr_status(),
        "live_sales_enabled": bool(config.LIVE_SALES_ENABLED),
        "legal_identity_configured": _legal_identity_ready(),
        "persistent_output_storage": bool(STORAGE.remote),
        "storage_policy": "final_zip_only",
        "transient_sources_deleted_after_processing": True,
    }


@router.get("/legal/config")
def legal_config() -> dict[str, Any]:
    return {
        "configured": _legal_identity_ready(),
        "entity_name": config.LEGAL_ENTITY_NAME,
        "address": config.LEGAL_ADDRESS,
        "tax_id": config.LEGAL_TAX_ID,
        "registry_id": config.LEGAL_REGISTRY_ID,
        "email": config.LEGAL_EMAIL,
        "phone": config.LEGAL_PHONE,
        "sales_enabled": bool(config.LIVE_SALES_ENABLED and _legal_identity_ready()),
    }


@router.get("/billing/commerce")
def commerce_account(user: dict = Depends(_user)) -> dict[str, Any]:
    return {"ok": True, **account_commerce_status(user["id"])}


@router.post("/billing/paytr/checkout")
def paytr_checkout(payload: CheckoutRequest, request: Request, user: dict = Depends(_user)) -> dict[str, Any]:
    if not config.PAYTR_TEST_MODE:
        if not config.LIVE_SALES_ENABLED:
            raise HTTPException(503, detail={"code": "LS-PAY-01", "message": "Canlı kartlı satış henüz açılmadı."})
        if not _legal_identity_ready():
            raise HTTPException(503, detail={"code": "LS-PAY-02", "message": "Canlı satış kimlik bilgileri tamamlanmadan ödeme alınamaz."})
    purchase: dict[str, Any] = {}
    try:
        purchase = create_purchase(user["id"], payload.plan_code, payload.interval, payload.currency, provider="paytr")
        checkout = create_iframe_checkout(
            merchant_oid=purchase["reference"],
            user_ip=_client_ip(request),
            email=user.get("email") or "",
            payment_amount=purchase["amount_minor"],
            currency=purchase["currency"],
            product_name=f"LectureSift {payload.plan_code} {payload.interval}",
            user_name=user.get("name") or user.get("email") or "LectureSift Kullanıcısı",
            user_address=f"{user.get('country_code') or 'TR'} dijital hizmet",
            user_phone=user.get("phone") or "0000000000",
            language=user.get("preferred_language") or "tr",
        )
    except (BillingError, BillingAuthenticationError) as exc:
        raise HTTPException(400, detail={"code": "LS-PAY-03", "message": str(exc)}) from exc
    except PayTRConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-PAY-04", "message": str(exc)}) from exc
    except PayTRRequestError as exc:
        mark_purchase_failed(purchase.get("reference", ""), exc.code)
        raise HTTPException(502, detail={"code": "LS-PAY-05", "message": str(exc), "provider_code": exc.code}) from exc
    return {"ok": True, "purchase": purchase, "iframe": {"token": checkout.token, "url": checkout.iframe_url}}


@router.post("/billing/paytr/callback", response_class=PlainTextResponse)
async def paytr_callback(request: Request) -> PlainTextResponse:
    values = dict(await request.form())
    try:
        callback = validate_callback(values)
        accept_payment_event(
            provider="paytr",
            reference=callback["merchant_oid"],
            status=callback["status"],
            amount_minor=callback["amount_minor"],
            provider_reference=callback["provider_reference"],
            event_identity=callback["event_identity"],
        )
        if callback["status"] != "success":
            mark_purchase_failed(callback["merchant_oid"], callback["failure_code"] or "provider_failed")
    except (PayTRConfigurationError, PayTRRequestError, BillingError) as exc:
        raise HTTPException(400, detail={"code": "LS-PAY-06", "message": "Ödeme bildirimi doğrulanamadı."}) from exc
    return PlainTextResponse("OK", media_type="text/plain")


@router.get("/billing/purchases/{reference}")
def purchase_status(reference: str, user: dict = Depends(_user)) -> dict[str, Any]:
    try:
        purchase = purchase_for_reference(reference, user_id=user["id"])
    except BillingError as exc:
        raise HTTPException(404, detail={"code": "LS-PAY-07", "message": str(exc)}) from exc
    return {"ok": True, "purchase": purchase}


@router.patch("/billing/subscription")
def control_subscription(payload: SubscriptionControlRequest, user: dict = Depends(_user)) -> dict[str, Any]:
    try:
        value = set_cancel_at_period_end(user["id"], payload.cancel_at_period_end)
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-PAY-08", "message": str(exc)}) from exc
    return {"ok": True, "subscription": value}


@router.post("/billing/refunds")
def create_refund(payload: RefundRequest, user: dict = Depends(_user)) -> dict[str, Any]:
    try:
        refund = request_refund(user["id"], payload.purchase_reference, payload.reason)
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-PAY-09", "message": str(exc)}) from exc
    return {"ok": True, "refund": refund}


@router.get("/admin/refunds", dependencies=[Depends(_admin)])
def list_refunds(status: str = Query("pending")) -> dict[str, Any]:
    return {"ok": True, "refunds": admin_refunds(status.strip())}


@router.post("/admin/refunds/{refund_id}/decision", dependencies=[Depends(_admin)])
def decide_refund(refund_id: str, payload: AdminRefundRequest) -> dict[str, Any]:
    try:
        bundle = refund_for_admin(refund_id)
        if not payload.approve:
            return {"ok": True, "refund": update_refund_status(refund_id, "rejected")}
        purchase = bundle["purchase"]
        amount_minor = int(payload.amount_minor) if payload.amount_minor is not None else int(purchase["amount_minor"])
        if amount_minor <= 0 or amount_minor > int(purchase["amount_minor"]):
            raise BillingError("İade tutarı ödeme tutarını aşamaz.")
        update_refund_status(refund_id, "provider_pending")
        provider = paytr_request_refund(merchant_oid=purchase["reference"], amount_minor=amount_minor, reference_no=refund_id.replace("-", ""))
        result = update_refund_status(refund_id, "refunded", provider_reference=provider.get("reference_no") or "")
    except (BillingError, PayTRConfigurationError, PayTRRequestError) as exc:
        try:
            update_refund_status(refund_id, "pending")
        except Exception:
            pass
        raise HTTPException(502, detail={"code": "LS-PAY-10", "message": str(exc)}) from exc
    return {"ok": True, "refund": result, "provider": provider}


@router.delete("/billing/jobs/{job_id}")
def delete_job(job_id: str, user: dict = Depends(_user)) -> dict[str, Any]:
    try:
        key = mark_job_deleted(user["id"], job_id)
    except BillingError as exc:
        raise HTTPException(404, detail={"code": "LS-JOB-04", "message": str(exc)}) from exc
    if key:
        STORAGE.delete_key(key)
    STORAGE.delete_transient_job(job_id)
    JOBS.remove(job_id)
    return {"ok": True, "job_id": job_id, "deleted": True}


@router.post("/billing/account-deletion")
def schedule_account_deletion(user: dict = Depends(_user)) -> dict[str, Any]:
    return request_account_deletion(user["id"])


@router.delete("/billing/account-deletion")
def undo_account_deletion(user: dict = Depends(_user)) -> dict[str, Any]:
    cancel_account_deletion(user["id"])
    return {"ok": True, "status": "canceled"}


def install_commerce_routes(app: FastAPI) -> None:
    if any(getattr(route, "path", None) == "/billing/paytr/callback" for route in app.routes):
        return
    app.include_router(router)
