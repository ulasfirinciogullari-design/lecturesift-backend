import hmac
import ipaddress
import json
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, HttpUrl

from . import config
from .billing import public_catalog, public_providers
from .ai import answer_lesson_question
from .billing_service import (
    BillingAuthenticationError,
    BillingConfigurationError,
    BillingError,
    account_status,
    admin_billing_overview,
    approve_manual_order,
    authenticate_session,
    billing_database_health,
    cancel_active_subscription,
    change_account_password,
    commerce_identity,
    create_password_reset_token,
    create_verification_token,
    create_manual_order,
    login_user,
    logout_user,
    manual_transfer_details,
    record_payment_consent,
    register_user,
    require_download_entitlement,
    reset_password,
    update_account_preferences,
    update_account_profile,
    validate_job_features,
    verify_email,
    verify_email_code,
)
from .config import (
    APP_VERSION,
    BILLING_ADMIN_TOKEN,
    FRONTEND_BASE_URL,
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_ACCOUNT_ID,
    INSTAGRAM_ADMIN_TOKEN,
    INSTAGRAM_APP_SECRET,
    INSTAGRAM_GRAPH_API_VERSION,
    MAX_SOURCE_FILES,
    MAX_VIDEO_BYTES,
    OPENAI_API_KEY,
    VIDEO_EXTENSIONS,
    WORK_DIR,
)
from .errors import LectureSiftError, normalize_error
from .daily_social import render_daily_image, render_daily_reel, render_daily_reel_cover
from .instagram import InstagramAPIError, InstagramClient, InstagramConfigurationError
from .jobs import JOBS
from .media import download_remote_video, validate_remote_url
from .mailer import EmailDeliveryError, email_delivery_configured, send_transactional_email
from .pipeline import process_job
from .payments import (
    PaymentProviderError,
    create_iyzico_checkout,
    create_paytr_checkout,
    iyzico_public_status,
    paytr_public_status,
    preferred_card_provider,
    process_iyzico_callback,
    process_paytr_callback,
)
from .security import RATE_LIMITER, RateLimitExceeded


app = FastAPI(title=f"LectureSift Backend V{APP_VERSION}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        FRONTEND_BASE_URL,
        "https://www.lecturesift.com",
        "https://clever-horse-22b1a8.netlify.app",
    ],
    allow_origin_regex=r"^https://deploy-preview-[0-9]+--clever-horse-22b1a8\.netlify\.app$|^http://(localhost|127\.0\.0\.1)(:[0-9]+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def _instagram_client() -> InstagramClient:
    try:
        return InstagramClient(
            access_token=INSTAGRAM_ACCESS_TOKEN,
            account_id=INSTAGRAM_ACCOUNT_ID,
            app_secret=INSTAGRAM_APP_SECRET,
            api_version=INSTAGRAM_GRAPH_API_VERSION,
        )
    except InstagramConfigurationError:
        raise HTTPException(503, detail={"code": "LS-IG-01", "message": "Instagram entegrasyonu yapılandırılmamış."})


def _instagram_admin(authorization: str | None = Header(None)) -> None:
    if not INSTAGRAM_ADMIN_TOKEN:
        raise HTTPException(503, detail={"code": "LS-IG-02", "message": "Instagram yayınlama uçları etkin değil."})
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value, INSTAGRAM_ADMIN_TOKEN):
        raise HTTPException(401, detail={"code": "LS-IG-03", "message": "Yetkisiz istek."})


def _billing_user(authorization: str | None = Header(None)) -> dict:
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(401, detail={"code": "LS-BILL-01", "message": "Devam etmek için giriş yap."})
    try:
        return authenticate_session(value)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except BillingAuthenticationError as exc:
        raise HTTPException(401, detail={"code": "LS-BILL-02", "message": str(exc)}) from exc


def _billing_admin(authorization: str | None = Header(None)) -> None:
    if not BILLING_ADMIN_TOKEN and not config.BILLING_ADMIN_EMAILS:
        raise HTTPException(503, detail={"code": "LS-BILL-03", "message": "Ödeme onayı yönetimi etkin değil."})
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and value:
        if BILLING_ADMIN_TOKEN and hmac.compare_digest(value, BILLING_ADMIN_TOKEN):
            return
        try:
            user = authenticate_session(value)
            if user["email"].casefold() in config.BILLING_ADMIN_EMAILS:
                return
        except (BillingAuthenticationError, BillingConfigurationError):
            pass
    raise HTTPException(401, detail={"code": "LS-BILL-04", "message": "Yetkisiz istek."})


def _owned_job(job_id: str, user: dict) -> dict:
    data = JOBS.get(job_id)
    owner_id = (data or {}).get("options", {}).get("billing_user_id")
    if not data or owner_id != user["id"]:
        # Use the same response for missing and foreign jobs so job IDs cannot
        # be used to discover another account's files or processing status.
        raise HTTPException(404, detail={"code": "LS-JOB-01", "message": "İşlem kaydı bulunamadı veya süresi doldu."})
    return data


def _public_job(data: dict) -> dict:
    result = data.copy()
    for key in (
        "job_dir",
        "result_path",
        "technical_error",
        "source_keys",
        "celery_task_id",
        "remote_prefix",
        "remote_result_key",
        "remote_download_key",
        "queue_error",
    ):
        result.pop(key, None)
    result["options"] = {
        key: value for key, value in result.get("options", {}).items() if key != "billing_user_id"
    }
    return result


def _job_download_allowed(data: dict, user: dict) -> bool:
    if bool((data.get("options") or {}).get("download_entitled")):
        return True
    try:
        require_download_entitlement(user["id"])
        return True
    except BillingError:
        return False


def _require_job_download(data: dict, user: dict) -> None:
    if not _job_download_allowed(data, user):
        raise HTTPException(
            402,
            detail={
                "code": "LS-BILL-11",
                "message": (
                    "Dosya indirmek için tek kullanımlık dakika paketi veya ücretli plan seç. "
                    "Ücretsiz hesapta sonuçları sitede önizleyebilirsin."
                ),
            },
        )


def start_url_job(job_id: str, url: str, job_dir: Path, options: dict) -> str:
    """Start the local URL path; durable runtime replaces this in production."""
    def worker() -> None:
        started = time.time()
        try:
            video_path = download_remote_video(url, job_dir)
            JOBS.update(job_id, percent=8, stage="parallel_analysis")
            process_job(job_id, video_path, options)
        except Exception as exc:
            normalized = normalize_error(exc)
            print(f"URL ERROR [{normalized.code}]: {normalized.technical_message}", flush=True)
            traceback.print_exc()
            JOBS.update(
                job_id,
                status="error",
                percent=0,
                stage="error",
                error_code=normalized.code,
                error=normalized.user_message,
                technical_error=normalized.technical_message,
                elapsed_seconds=round(time.time() - started, 1),
            )

    threading.Thread(target=worker, daemon=True).start()
    return "working"


class InstagramMediaRequest(BaseModel):
    media_url: HttpUrl
    caption: str = ""
    media_type: str = "IMAGE"
    cover_url: HttpUrl | None = None


class InstagramPublishRequest(BaseModel):
    container_id: str


class BillingAuthRequest(BaseModel):
    email: str
    password: str


class BillingRegisterRequest(BillingAuthRequest):
    first_name: str
    last_name: str
    phone: str = ""
    country_code: str = "TR"


class BillingEmailRequest(BaseModel):
    email: str


class BillingTokenRequest(BaseModel):
    token: str


class BillingVerificationCodeRequest(BaseModel):
    email: str
    code: str


class BillingPasswordResetRequest(BillingTokenRequest):
    new_password: str


class ManualOrderRequest(BaseModel):
    plan_code: str
    interval: str = "monthly"
    terms_accepted: bool = False
    early_performance_requested: bool = False
    language: str = "tr"


class BillingPreferencesRequest(BaseModel):
    country_code: str
    preferred_language: str


class BillingProfileRequest(BaseModel):
    first_name: str
    last_name: str
    phone: str = ""


class BillingPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class BillingCheckoutRequest(BaseModel):
    plan_code: str
    interval: str = "monthly"
    currency: str = "TRY"
    billing_address: str
    billing_city: str = ""
    billing_zip_code: str = ""
    phone: str = ""
    language: str = "tr"
    terms_accepted: bool = False
    early_performance_requested: bool = False


class LessonQuestionRequest(BaseModel):
    question: str


def _send_verification_email(email: str, token: str, code: str) -> None:
    link = f"{FRONTEND_BASE_URL}/verify.html?token={token}"
    code_cells = "".join(
        (
            '<td style="width:16.66%;height:44px;padding:0;text-align:center;'
            'border:1px solid #d9d5ff;border-radius:8px;background:#f6f4ff;'
            'color:#28204f;font-family:Arial,sans-serif;font-size:24px;'
            f'font-weight:800;line-height:44px;white-space:nowrap">{digit}</td>'
        )
        for digit in code
    )
    code_table = (
        '<table role="presentation" aria-label="Altı haneli doğrulama kodu" '
        'style="width:100%;max-width:276px;table-layout:fixed;border-collapse:separate;'
        'border-spacing:4px;margin:24px 0;mso-table-lspace:0;mso-table-rspace:0">'
        f"<tr>{code_cells}</tr></table>"
    )
    send_transactional_email(
        email,
        "LectureSift e-posta doğrulama",
        (
            "<h1>LectureSift hesabını doğrula</h1>"
            "<p>Hesabını etkinleştirmek için bağlantıya tıkla veya doğrulama ekranına aşağıdaki kodu gir.</p>"
            f"{code_table}"
            f'<p><a href="{link}">E-posta adresimi doğrula</a></p>'
            "<p>Kod ve bağlantı 24 saat geçerlidir. Bu hesabı sen oluşturmadıysan e-postayı yok say.</p>"
        ),
        f"LectureSift doğrulama kodun: {code}\nDoğrulama bağlantın: {link}\nKod ve bağlantı 24 saat geçerlidir.",
    )


def _send_password_reset_email(email: str, token: str) -> None:
    link = f"{FRONTEND_BASE_URL}/reset-password.html?token={token}"
    send_transactional_email(
        email,
        "LectureSift şifre yenileme",
        (
            "<h1>LectureSift şifreni yenile</h1>"
            "<p>Yeni bir şifre belirlemek için aşağıdaki güvenli bağlantıyı kullan.</p>"
            f'<p><a href="{link}">Şifremi yenile</a></p>'
            "<p>Bu bağlantı 45 dakika geçerlidir. Bu isteği sen yapmadıysan e-postayı yok say.</p>"
        ),
        f"LectureSift şifreni yenile: {link}\nBu bağlantı 45 dakika geçerlidir.",
    )


def _instagram_error(exc: InstagramAPIError) -> None:
    raise HTTPException(
        exc.status_code,
        detail={
            "code": "LS-IG-04",
            "message": "Instagram API isteği tamamlanamadı.",
            "type": exc.error_type,
        },
    )


def _options(
    source_language: str,
    output_language: str,
    summary_style: str,
    quiz_count: int,
    flashcard_count: int,
    translate_transcript: bool,
    slides_offset_seconds: float = 0,
    output_formats: str = "pdf",
    job_type: str = "study_pack",
) -> dict:
    source = source_language or "auto"
    output = output_language or "tr"
    translation_enabled = bool(translate_transcript) and not (source != "auto" and source == output)
    formats = [value for value in dict.fromkeys(output_formats.lower().replace(" ", "").split(",")) if value in {"pdf", "docx", "txt"}]
    if not formats:
        formats = ["pdf"]
    selected_job_type = job_type if job_type in {"study_pack", "audio_export", "download_video"} else "study_pack"
    return {
        "source_language": source,
        "output_language": output,
        "summary_style": summary_style or "standard",
        "quiz_count": max(3, min(int(quiz_count), 30)),
        "flashcard_count": max(5, min(int(flashcard_count), 60)),
        "translate_transcript": translation_enabled,
        "slides_offset_seconds": max(-3600.0, min(float(slides_offset_seconds or 0), 3600.0)),
        "output_formats": formats,
        "job_type": selected_job_type,
    }


def _job_path(job_id: str) -> Path:
    path = WORK_DIR / job_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def _raise_public(error: LectureSiftError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.public())


def _client_ip(request: Request) -> str:
    candidates = []
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        candidates.extend(part.strip() for part in forwarded.split(","))
    if request.client:
        candidates.append(request.client.host)
    for candidate in candidates:
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return ""


def _rate_limit(
    request: Request,
    scope: str,
    discriminator: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    client = _client_ip(request) or (request.client.host if request.client else "unknown")
    try:
        RATE_LIMITER.check(
            scope,
            f"{client}|{discriminator.strip().casefold()}",
            limit=limit,
            window_seconds=window_seconds,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            429,
            detail={"code": "LS-SEC-01", "message": str(exc)},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


def _upload_extension(file: UploadFile) -> str:
    extension = Path(file.filename or "video.mp4").suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        _raise_public(LectureSiftError("LS-UPLOAD-01", "Bu video biçimi desteklenmiyor. MP4, MOV, MKV veya WebM kullan."))
    return extension


async def _save_upload(file: UploadFile, destination: Path, bytes_used: int = 0) -> int:
    total = bytes_used
    with open(destination, "wb") as output:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_VIDEO_BYTES:
                _raise_public(
                    LectureSiftError(
                        "LS-UPLOAD-02",
                        "Yüklenen video dosyalarının toplam boyutu izin verilen sınırı aşıyor.",
                        status_code=413,
                    )
                )
            output.write(chunk)
    return total


def _validate_upload_list(files: list[UploadFile], label: str) -> list[str]:
    if not files:
        _raise_public(LectureSiftError("LS-UPLOAD-03", f"{label} için en az bir video ekle."))
    if len(files) > MAX_SOURCE_FILES:
        _raise_public(LectureSiftError("LS-UPLOAD-04", f"{label} için en fazla {MAX_SOURCE_FILES} video eklenebilir."))
    return [_upload_extension(file) for file in files]


async def _save_upload_list(
    files: list[UploadFile],
    extensions: list[str],
    job_dir: Path,
    role: str,
    bytes_used: int,
) -> tuple[list[Path], int, list[int]]:
    paths: list[Path] = []
    sizes: list[int] = []
    total = bytes_used
    for index, (file, extension) in enumerate(zip(files, extensions), 1):
        destination = job_dir / f"{role}_{index:03d}{extension}"
        before = total
        total = await _save_upload(file, destination, total)
        paths.append(destination)
        sizes.append(total - before)
    return paths, total, sizes


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "service": f"LectureSift Backend V{APP_VERSION}",
        "frontend": "https://lecturesift.com/",
    }


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "version": APP_VERSION,
        "openai_key": bool(OPENAI_API_KEY),
        "slide_engine": "v4-layout-persistence",
        "study_pack": True,
        "async_jobs": True,
        "dual_source_upload": True,
        "multi_source_upload": True,
        "output_formats": ["pdf", "docx", "txt"],
        "audio_export": True,
        "url_video_download": True,
        "instagram_configured": all((INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_ACCOUNT_ID, INSTAGRAM_APP_SECRET)),
    }


@app.get("/billing/plans")
def billing_plans(currency: str = "TRY") -> dict:
    return public_catalog(currency)


@app.get("/billing/providers")
def billing_providers() -> dict:
    body = public_providers()
    paytr = paytr_public_status()
    iyzico = iyzico_public_status()
    body["providers"] = [
        {**provider, **paytr} if provider.get("code") == "paytr" else
        ({**provider, **iyzico} if provider.get("code") == "iyzico" else provider)
        for provider in body["providers"]
    ]
    body["preferred_card_provider"] = (
        "iyzico" if iyzico["configured"] else ("paytr" if paytr["configured"] else None)
    )
    body["commerce_identity"] = commerce_identity()
    return body


@app.get("/billing/operator")
def billing_operator() -> dict:
    return commerce_identity()


@app.get("/billing/manual-transfer")
def billing_manual_transfer_status() -> dict:
    return manual_transfer_details()


@app.get("/billing/health")
def billing_health() -> dict:
    try:
        database = billing_database_health()
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    return {
        "ok": True,
        "database": database,
        "email_delivery_configured": email_delivery_configured(),
        "payments": {
            "iyzico": iyzico_public_status(),
            "paytr": paytr_public_status(),
            "bank_transfer": {"configured": manual_transfer_details()["available"]},
        },
        "commerce_identity": commerce_identity(),
    }


@app.post("/billing/register")
def billing_register(payload: BillingRegisterRequest, request: Request) -> dict:
    _rate_limit(request, "register", payload.email, limit=5, window_seconds=60 * 60)
    if not email_delivery_configured():
        raise HTTPException(
            503,
            detail={"code": "LS-BILL-15", "message": "E-posta doğrulama hizmeti henüz etkinleştirilmemiş."},
        )
    try:
        result = register_user(
            payload.email,
            payload.password,
            payload.first_name,
            payload.last_name,
            payload.phone,
            payload.country_code,
        )
        _send_verification_email(
            result["user"]["email"],
            result.pop("verification_token"),
            result.pop("verification_code"),
        )
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-16", "message": str(exc)}) from exc
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-05", "message": str(exc)}) from exc
    return {
        "ok": True,
        "verification_required": True,
        "message": "Doğrulama kodunu ve bağlantısını e-posta adresine gönderdik.",
        "user": result["user"],
    }


@app.post("/billing/verify-email")
def billing_verify_email(payload: BillingTokenRequest, request: Request) -> dict:
    _rate_limit(request, "verify-link", payload.token[:16], limit=12, window_seconds=15 * 60)
    try:
        result = verify_email(payload.token)
    except BillingAuthenticationError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-17", "message": str(exc)}) from exc
    return {"ok": True, **result, "account": account_status(result["user"]["id"])}


@app.post("/billing/verify-email-code")
def billing_verify_email_code(payload: BillingVerificationCodeRequest, request: Request) -> dict:
    _rate_limit(request, "verify-code", payload.email, limit=10, window_seconds=15 * 60)
    try:
        result = verify_email_code(payload.email, payload.code)
    except BillingAuthenticationError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-17", "message": str(exc)}) from exc
    return {"ok": True, **result, "account": account_status(result["user"]["id"])}


@app.post("/billing/resend-verification")
def billing_resend_verification(payload: BillingEmailRequest, request: Request) -> dict:
    _rate_limit(request, "resend-verification", payload.email, limit=5, window_seconds=60 * 60)
    if not email_delivery_configured():
        raise HTTPException(503, detail={"code": "LS-BILL-15", "message": "E-posta doğrulama hizmeti henüz etkinleştirilmemiş."})
    try:
        result = create_verification_token(payload.email)
        if result:
            _send_verification_email(result["email"], result["token"], result["code"])
    except EmailDeliveryError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-16", "message": str(exc)}) from exc
    return {"ok": True, "message": "Hesap uygunsa doğrulama kodu ve bağlantısı gönderildi."}


@app.post("/billing/forgot-password")
def billing_forgot_password(payload: BillingEmailRequest, request: Request) -> dict:
    _rate_limit(request, "forgot-password", payload.email, limit=5, window_seconds=60 * 60)
    if not email_delivery_configured():
        raise HTTPException(503, detail={"code": "LS-BILL-15", "message": "E-posta hizmeti henüz etkinleştirilmemiş."})
    try:
        result = create_password_reset_token(payload.email)
        if result:
            _send_password_reset_email(result["email"], result["token"])
    except EmailDeliveryError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-16", "message": str(exc)}) from exc
    return {"ok": True, "message": "Hesap uygunsa şifre yenileme bağlantısı gönderildi."}


@app.post("/billing/reset-password")
def billing_reset_password(payload: BillingPasswordResetRequest, request: Request) -> dict:
    _rate_limit(request, "reset-password", payload.token[:16], limit=10, window_seconds=60 * 60)
    try:
        reset_password(payload.token, payload.new_password)
    except BillingAuthenticationError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-18", "message": str(exc)}) from exc
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-19", "message": str(exc)}) from exc
    return {"ok": True, "message": "Şifren yenilendi. Yeni şifrenle giriş yapabilirsin."}


@app.post("/billing/login")
def billing_login(payload: BillingAuthRequest, request: Request) -> dict:
    _rate_limit(request, "login", payload.email, limit=10, window_seconds=15 * 60)
    try:
        result = login_user(payload.email, payload.password)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except BillingAuthenticationError as exc:
        raise HTTPException(401, detail={"code": "LS-BILL-06", "message": str(exc)}) from exc
    return {"ok": True, **result, "account": account_status(result["user"]["id"])}


@app.post("/billing/logout")
def billing_logout(user: dict = Depends(_billing_user)) -> dict:
    logout_user(user["id"])
    return {"ok": True, "message": "Oturum kapatıldı.", "user_id": user["id"]}


@app.get("/billing/me")
def billing_me(user: dict = Depends(_billing_user)) -> dict:
    return {"ok": True, "account": account_status(user["id"])}


@app.patch("/billing/me/preferences")
def billing_update_preferences(
    payload: BillingPreferencesRequest,
    user: dict = Depends(_billing_user),
) -> dict:
    try:
        account = update_account_preferences(
            user["id"], payload.country_code, payload.preferred_language
        )
    except BillingAuthenticationError as exc:
        raise HTTPException(401, detail={"code": "LS-BILL-06", "message": str(exc)}) from exc
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-21", "message": str(exc)}) from exc
    return {"ok": True, "message": "Hesap tercihlerin kaydedildi.", "account": account}


@app.patch("/billing/me/profile")
def billing_update_profile(
    payload: BillingProfileRequest,
    user: dict = Depends(_billing_user),
) -> dict:
    try:
        account = update_account_profile(
            user["id"], payload.first_name, payload.last_name, payload.phone
        )
    except BillingAuthenticationError as exc:
        raise HTTPException(401, detail={"code": "LS-BILL-06", "message": str(exc)}) from exc
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-22", "message": str(exc)}) from exc
    return {"ok": True, "message": "Profil bilgilerin güncellendi.", "account": account}


@app.post("/billing/me/change-password")
def billing_change_password(
    payload: BillingPasswordChangeRequest,
    user: dict = Depends(_billing_user),
) -> dict:
    try:
        result = change_account_password(
            user["id"], payload.current_password, payload.new_password
        )
    except BillingAuthenticationError as exc:
        raise HTTPException(401, detail={"code": "LS-BILL-23", "message": str(exc)}) from exc
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-19", "message": str(exc)}) from exc
    return {"ok": True, "message": "Parolan güncellendi.", **result}


@app.post("/billing/me/subscription/cancel")
def billing_cancel_subscription(user: dict = Depends(_billing_user)) -> dict:
    try:
        account = cancel_active_subscription(user["id"])
    except BillingAuthenticationError as exc:
        raise HTTPException(401, detail={"code": "LS-BILL-06", "message": str(exc)}) from exc
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-28", "message": str(exc)}) from exc
    return {
        "ok": True,
        "message": "Yenileme durduruldu. Ücretli hakların mevcut dönemin sonuna kadar devam edecek.",
        "account": account,
    }


@app.post("/billing/manual-transfer/orders")
def billing_create_manual_order(
    payload: ManualOrderRequest,
    request: Request,
    user: dict = Depends(_billing_user),
) -> dict:
    try:
        if not payload.terms_accepted or not payload.early_performance_requested:
            raise BillingError("Ödeme öncesi bilgilendirmeyi ve hizmetin hemen başlamasını açıkça onaylamalısın.")
        order = create_manual_order(user["id"], payload.plan_code, payload.interval)
        record_payment_consent(
            order["reference"],
            user["id"],
            terms_accepted=payload.terms_accepted,
            early_performance_requested=payload.early_performance_requested,
            language=payload.language,
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-07", "message": str(exc)}) from exc
    except BillingError as exc:
        raise HTTPException(400, detail={"code": "LS-BILL-08", "message": str(exc)}) from exc
    return {"ok": True, "order": order}


@app.post("/billing/checkout")
def billing_create_checkout(
    payload: BillingCheckoutRequest,
    request: Request,
    user: dict = Depends(_billing_user),
) -> dict:
    _rate_limit(request, "checkout", user["id"], limit=10, window_seconds=10 * 60)
    try:
        provider = preferred_card_provider()
        common = {
            "plan_code": payload.plan_code,
            "interval": payload.interval,
            "currency": payload.currency,
            "user_ip": _client_ip(request),
            "billing_address": payload.billing_address,
            "phone": payload.phone,
            "language": payload.language,
            "terms_accepted": payload.terms_accepted,
            "early_performance_requested": payload.early_performance_requested,
            "user_agent": request.headers.get("user-agent", ""),
        }
        if provider == "iyzico":
            checkout = create_iyzico_checkout(
                user,
                billing_city=payload.billing_city,
                billing_zip_code=payload.billing_zip_code,
                **common,
            )
        else:
            checkout = create_paytr_checkout(user, **common)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-PAY-01", "message": str(exc)}) from exc
    except (BillingAuthenticationError, BillingError, PaymentProviderError) as exc:
        raise HTTPException(400, detail={"code": "LS-PAY-02", "message": str(exc)}) from exc
    return {"ok": True, **checkout}


@app.post("/billing/iyzico/callback")
def billing_iyzico_callback(order: str, token: str = Form(...)) -> Response:
    if not order.isalnum() or len(order) > 64:
        return Response("Invalid order reference", status_code=400, media_type="text/plain")
    try:
        result = process_iyzico_callback(order_reference=order, token=token)
    except (BillingConfigurationError, BillingError, PaymentProviderError):
        return RedirectResponse(
            f"{FRONTEND_BASE_URL}/plans.html?payment=verification_failed&order={order}",
            status_code=303,
        )
    destination = "account.html" if result["status"] == "paid" else "plans.html"
    payment = "success" if result["status"] == "paid" else "failed"
    return RedirectResponse(
        f"{FRONTEND_BASE_URL}/{destination}?payment={payment}&order={order}",
        status_code=303,
    )


@app.post("/billing/paytr/callback")
def billing_paytr_callback(
    merchant_oid: str = Form(...),
    status: str = Form(...),
    total_amount: str = Form(...),
    payment_amount: str = Form(...),
    hash: str = Form(...),
    failed_reason_code: str = Form(""),
    failed_reason_msg: str = Form(""),
) -> Response:
    try:
        process_paytr_callback(
            merchant_oid=merchant_oid,
            status=status,
            total_amount=total_amount,
            payment_amount=payment_amount,
            callback_hash=hash,
            failed_reason_code=failed_reason_code,
            failed_reason_msg=failed_reason_msg,
        )
    except (BillingConfigurationError, BillingError, PaymentProviderError):
        return Response("PAYTR notification failed", status_code=400, media_type="text/plain")
    return Response("OK", media_type="text/plain")


@app.post(
    "/billing/manual-transfer/orders/{reference}/approve",
    dependencies=[Depends(_billing_admin)],
)
def billing_approve_manual_order(reference: str) -> dict:
    try:
        account = approve_manual_order(reference)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    except BillingError as exc:
        raise HTTPException(404, detail={"code": "LS-BILL-09", "message": str(exc)}) from exc
    return {"ok": True, "account": account}


@app.get(
    "/billing/admin/overview",
    dependencies=[Depends(_billing_admin)],
)
def billing_admin_overview(limit: int = 100) -> dict:
    try:
        overview = admin_billing_overview(limit)
    except BillingConfigurationError as exc:
        raise HTTPException(503, detail={"code": "LS-BILL-00", "message": str(exc)}) from exc
    return {"ok": True, **overview}


@app.get("/instagram/health")
def instagram_health(client: InstagramClient = Depends(_instagram_client)) -> dict:
    try:
        account = client.get_account()
    except InstagramAPIError as exc:
        _instagram_error(exc)
    return {
        "ok": True,
        "connected": True,
        "account": {key: account.get(key) for key in ("id", "username", "account_type", "media_count")},
    }


@app.post("/instagram/media", dependencies=[Depends(_instagram_admin)])
def instagram_create_media(
    payload: InstagramMediaRequest,
    client: InstagramClient = Depends(_instagram_client),
) -> dict:
    media_type = payload.media_type.upper()
    if media_type not in {"IMAGE", "REELS", "STORIES"}:
        raise HTTPException(400, detail={"code": "LS-IG-05", "message": "Desteklenmeyen Instagram medya türü."})
    try:
        result = client.create_media_container(
            media_url=str(payload.media_url),
            caption=payload.caption,
            media_type=media_type,
            cover_url=str(payload.cover_url) if payload.cover_url else None,
        )
    except InstagramAPIError as exc:
        _instagram_error(exc)
    return {"ok": True, "container_id": result.get("id"), "status": "created"}


@app.get("/instagram/media/{container_id}", dependencies=[Depends(_instagram_admin)])
def instagram_media_status(
    container_id: str,
    client: InstagramClient = Depends(_instagram_client),
) -> dict:
    try:
        result = client.get_container_status(container_id)
    except InstagramAPIError as exc:
        _instagram_error(exc)
    return {"ok": True, "container": result}


@app.post("/instagram/media/publish", dependencies=[Depends(_instagram_admin)])
def instagram_publish_media(
    payload: InstagramPublishRequest,
    client: InstagramClient = Depends(_instagram_client),
) -> dict:
    try:
        result = client.publish_media(payload.container_id)
    except InstagramAPIError as exc:
        _instagram_error(exc)
    return {"ok": True, "media_id": result.get("id"), "status": "published"}


@app.get("/instagram/daily/image/{day}.jpg")
def instagram_daily_image(day: str) -> Response:
    """Public deterministic image endpoint used by Instagram's media fetcher."""
    from datetime import date

    try:
        selected_day = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, detail={"code": "LS-IG-06", "message": "Geçersiz tarih."})
    return Response(
        content=render_daily_image(selected_day),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/instagram/daily/reel/{day}.jpg")
def instagram_daily_reel_cover(day: str) -> Response:
    """Public deterministic 9:16 cover fetched by Instagram."""
    from datetime import date

    try:
        selected_day = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, detail={"code": "LS-IG-06", "message": "Geçersiz tarih."})
    return Response(
        content=render_daily_reel_cover(selected_day),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/instagram/daily/reel/{day}.mp4")
def instagram_daily_reel_video(day: str) -> Response:
    """Public deterministic MP4 fetched by Instagram for scheduled publishing."""
    from datetime import date

    try:
        selected_day = date.fromisoformat(day)
        if abs((selected_day - date.today()).days) > 2:
            raise HTTPException(404, detail={"code": "LS-IG-06", "message": "Gönderi videosu bulunamadı."})
        content = render_daily_reel(selected_day)
    except ValueError:
        raise HTTPException(400, detail={"code": "LS-IG-06", "message": "Geçersiz tarih."})
    except RuntimeError as exc:
        raise HTTPException(503, detail={"code": "LS-IG-07", "message": str(exc)}) from exc
    return Response(
        content=content,
        media_type="video/mp4",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/jobs/{job_id}")
def get_job(job_id: str, user: dict = Depends(_billing_user)) -> dict:
    return _public_job(_owned_job(job_id, user))


@app.get("/jobs")
def list_jobs(limit: int = 50, user: dict = Depends(_billing_user)) -> dict:
    return {"jobs": [_public_job(item) for item in JOBS.list_for_user(user["id"], limit)]}


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str, user: dict = Depends(_billing_user)) -> dict:
    data = _owned_job(job_id, user)
    if data.get("status") != "done":
        raise HTTPException(409, detail={"code": "LS-JOB-02", "message": "Ders analizi henüz tamamlanmadı."})
    path = Path(data["job_dir"]) / "result.json"
    if not path.exists():
        raise HTTPException(404, detail={"code": "LS-JOB-03", "message": "Sonuç dosyası bulunamadı."})
    result = json.loads(path.read_text(encoding="utf-8"))
    result["download_enabled"] = _job_download_allowed(data, user)
    return result


@app.post("/jobs/{job_id}/ask")
def ask_lesson_question(
    job_id: str,
    payload: LessonQuestionRequest,
    request: Request,
    user: dict = Depends(_billing_user),
) -> dict:
    data = _owned_job(job_id, user)
    if data.get("status") != "done":
        raise HTTPException(409, detail={"code": "LS-JOB-02", "message": "Ders analizi henüz tamamlanmadı."})
    _rate_limit(request, "lesson-question", user["id"], limit=30, window_seconds=60 * 60)
    result_path = Path(data["job_dir"]) / "result.json"
    if not result_path.exists():
        raise HTTPException(404, detail={"code": "LS-JOB-03", "message": "Sonuç dosyası bulunamadı."})
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        language = str(result.get("options", {}).get("output_language") or "tr")
        answer = answer_lesson_question(result, payload.question, language)
    except LectureSiftError as exc:
        raise HTTPException(exc.status_code, detail=exc.public()) from exc
    return {"ok": True, **answer}


@app.get("/jobs/{job_id}/slide/{filename}")
def get_slide(job_id: str, filename: str, user: dict = Depends(_billing_user)) -> FileResponse:
    data = _owned_job(job_id, user)
    if Path(filename).name != filename:
        raise HTTPException(400, detail={"code": "LS-FILE-01", "message": "Geçersiz dosya adı."})
    path = Path(data["job_dir"]) / "slides" / filename
    if not path.exists():
        raise HTTPException(404, detail={"code": "LS-FILE-02", "message": "Slayt görseli bulunamadı."})
    return FileResponse(str(path), media_type="image/jpeg")


@app.get("/jobs/{job_id}/artifact/{filename}")
def get_artifact(job_id: str, filename: str, user: dict = Depends(_billing_user)) -> FileResponse:
    data = _owned_job(job_id, user)
    if data.get("status") != "done":
        raise HTTPException(409, detail={"code": "LS-JOB-02", "message": "Ders analizi henüz tamamlanmadı."})
    _require_job_download(data, user)
    if Path(filename).name != filename:
        raise HTTPException(400, detail={"code": "LS-FILE-01", "message": "Geçersiz dosya adı."})
    path = Path(data["job_dir"]) / "package" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, detail={"code": "LS-FILE-02", "message": "Çıktı dosyası bulunamadı."})
    return FileResponse(str(path), filename=filename)


@app.get("/jobs/{job_id}/download")
def download(job_id: str, user: dict = Depends(_billing_user)) -> FileResponse:
    data = _owned_job(job_id, user)
    if data.get("status") != "done":
        raise HTTPException(409, detail={"code": "LS-JOB-02", "message": "Ders analizi henüz tamamlanmadı."})
    _require_job_download(data, user)
    return FileResponse(
        data["result_path"],
        media_type="application/zip",
        filename="LectureSift_Paketi.zip",
    )


@app.post("/jobs")
async def create_job(
    file: UploadFile | None = File(None),
    slides_file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    audio_files: list[UploadFile] | None = File(None),
    visual_files: list[UploadFile] | None = File(None),
    source_layout: str = Form("classic"),
    source_language: str = Form("auto"),
    output_language: str = Form("tr"),
    summary_style: str = Form("standard"),
    quiz_count: int = Form(10),
    flashcard_count: int = Form(20),
    translate_transcript: bool = Form(True),
    slides_offset_seconds: float = Form(0),
    output_formats: str = Form("pdf"),
    job_type: str = Form("study_pack"),
    billing_user: dict = Depends(_billing_user),
) -> dict:
    options = _options(
        source_language,
        output_language,
        summary_style,
        quiz_count,
        flashcard_count,
        translate_transcript,
        slides_offset_seconds,
        output_formats,
        job_type,
    )
    try:
        entitlement = validate_job_features(
            billing_user["id"],
            quiz_count=options["quiz_count"],
            flashcard_count=options["flashcard_count"],
            output_formats=options["output_formats"],
            summary_style=options["summary_style"],
            job_type=options["job_type"],
        )
    except BillingError as exc:
        raise HTTPException(402, detail={"code": "LS-BILL-10", "message": str(exc)}) from exc
    layout = "separate" if source_layout == "separate" or slides_file or visual_files else "classic"
    if layout == "separate":
        audio_uploads = list(audio_files or ([] if file is None else [file]))
        visual_uploads = list(visual_files or ([] if slides_file is None else [slides_file]))
        audio_extensions = _validate_upload_list(audio_uploads, "Ses kaynağı")
        visual_extensions = _validate_upload_list(visual_uploads, "Görüntü/slayt kaynağı")
    else:
        classic_uploads = list(files or ([] if file is None else [file]))
        classic_extensions = _validate_upload_list(classic_uploads, "Ders kaynağı")

    JOBS.cleanup_expired()
    job_id = str(uuid.uuid4())
    job_dir = _job_path(job_id)
    total = 0
    try:
        if layout == "separate":
            audio_paths, total, audio_sizes = await _save_upload_list(
                audio_uploads, audio_extensions, job_dir, "audio", total
            )
            visual_paths, total, visual_sizes = await _save_upload_list(
                visual_uploads, visual_extensions, job_dir, "visual", total
            )
        else:
            audio_paths, total, audio_sizes = await _save_upload_list(
                classic_uploads, classic_extensions, job_dir, "part", total
            )
            visual_paths = []
            visual_sizes = []
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    options["billing_user_id"] = billing_user["id"]
    options["download_entitled"] = bool(entitlement.get("download_enabled"))
    source_type = "upload_separate" if layout == "separate" else ("upload_multi" if len(audio_paths) > 1 else "upload")
    JOBS.create(
        job_id,
        job_dir,
        options,
        source_type=source_type,
        source_layout=layout,
        file_size_bytes=total,
        audio_file_sizes=audio_sizes,
        visual_file_sizes=visual_sizes,
        source_file_count=len(audio_paths) + len(visual_paths),
        retention_seconds=max(1, int(entitlement["plan"]["history_days"])) * 24 * 60 * 60,
    )
    threading.Thread(
        target=process_job,
        args=(job_id, audio_paths, options, visual_paths if layout == "separate" else None),
        daemon=True,
    ).start()
    return {"job_id": job_id, "status": "queued", "version": APP_VERSION, "source_layout": layout}


@app.post("/jobs/url")
def create_url_job(
    video_url: str = Form(...),
    source_language: str = Form("auto"),
    output_language: str = Form("tr"),
    summary_style: str = Form("standard"),
    quiz_count: int = Form(10),
    flashcard_count: int = Form(20),
    translate_transcript: bool = Form(True),
    slides_offset_seconds: float = Form(0),
    output_formats: str = Form("pdf"),
    job_type: str = Form("study_pack"),
    billing_user: dict = Depends(_billing_user),
) -> dict:
    options = _options(
        source_language,
        output_language,
        summary_style,
        quiz_count,
        flashcard_count,
        translate_transcript,
        slides_offset_seconds,
        output_formats,
        job_type,
    )
    try:
        entitlement = validate_job_features(
            billing_user["id"],
            quiz_count=options["quiz_count"],
            flashcard_count=options["flashcard_count"],
            output_formats=options["output_formats"],
            summary_style=options["summary_style"],
            job_type=options["job_type"],
        )
    except BillingError as exc:
        raise HTTPException(402, detail={"code": "LS-BILL-10", "message": str(exc)}) from exc
    try:
        url = validate_remote_url(video_url)
    except LectureSiftError as exc:
        _raise_public(exc)

    JOBS.cleanup_expired()
    job_id = str(uuid.uuid4())
    job_dir = _job_path(job_id)
    options["billing_user_id"] = billing_user["id"]
    options["download_entitled"] = bool(entitlement.get("download_enabled"))
    JOBS.create(
        job_id,
        job_dir,
        options,
        source_type="url",
        source_url=url,
        retention_seconds=max(1, int(entitlement["plan"]["history_days"])) * 24 * 60 * 60,
    )
    JOBS.update(job_id, status="working", percent=3, stage="url_download")
    status = start_url_job(job_id, url, job_dir, options)
    return {"job_id": job_id, "status": status, "version": APP_VERSION}
