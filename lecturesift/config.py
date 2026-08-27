import os
import tempfile
from pathlib import Path


APP_VERSION = "4.2"
WORK_DIR = Path(os.getenv("LECTURESIFT_WORK_DIR", str(Path(tempfile.gettempdir()) / "lecturesift")))
WORK_DIR.mkdir(parents=True, exist_ok=True)


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().casefold() in {"1", "true", "yes", "on"}


def _origins(value: str) -> list[str]:
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
VISION_TRANSLATION_MODEL = os.getenv("VISION_TRANSLATION_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET", "")
INSTAGRAM_ADMIN_TOKEN = os.getenv("INSTAGRAM_ADMIN_TOKEN", "")
INSTAGRAM_GRAPH_API_VERSION = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v23.0")
INSTAGRAM_DAILY_AUTOMATION_ENABLED = _bool("INSTAGRAM_DAILY_AUTOMATION_ENABLED")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "https://lecturesift.com").rstrip("/")

CORS_ALLOW_ORIGINS = _origins(
    os.getenv(
        "CORS_ALLOW_ORIGINS",
        "https://lecturesift.com,https://www.lecturesift.com,"
        "https://clever-horse-22b1a8.netlify.app,http://localhost:8000,http://127.0.0.1:8000",
    )
)
RATE_LIMIT_ENABLED = _bool("RATE_LIMIT_ENABLED", "true")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{WORK_DIR / 'billing.db'}")
BILLING_SESSION_SECRET = os.getenv("BILLING_SESSION_SECRET", "")
BILLING_ADMIN_TOKEN = os.getenv("BILLING_ADMIN_TOKEN", "")
BILLING_BANK_IBAN = os.getenv("BILLING_BANK_IBAN", "").replace(" ", "").upper()
BILLING_BANK_ACCOUNT_HOLDER = os.getenv("BILLING_BANK_ACCOUNT_HOLDER", "").strip()
BILLING_BANK_NAME = os.getenv("BILLING_BANK_NAME", "").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "support@lecturesift.com").strip()
BILLING_SUPPORT_EMAIL = os.getenv("BILLING_SUPPORT_EMAIL", CONTACT_EMAIL).strip()

EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "none").strip().lower()
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = _bool("SMTP_USE_TLS", "true")

PAYTR_MERCHANT_ID = os.getenv("PAYTR_MERCHANT_ID", "").strip()
PAYTR_MERCHANT_KEY = os.getenv("PAYTR_MERCHANT_KEY", "").strip()
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "").strip()
PAYTR_TEST_MODE = _bool("PAYTR_TEST_MODE", "true")
PAYTR_DEBUG = _bool("PAYTR_DEBUG", "false")
PAYTR_RECURRING_ENABLED = _bool("PAYTR_RECURRING_ENABLED", "false")
PAYTR_TIMEOUT_MINUTES = max(5, min(int(os.getenv("PAYTR_TIMEOUT_MINUTES", "30")), 60))
PAYTR_TOKEN_URL = os.getenv("PAYTR_TOKEN_URL", "https://www.paytr.com/odeme/api/get-token").strip()
PAYTR_IFRAME_BASE_URL = os.getenv("PAYTR_IFRAME_BASE_URL", "https://www.paytr.com/odeme/guvenli").strip().rstrip("/")
PAYTR_REFUND_URL = os.getenv("PAYTR_REFUND_URL", "https://www.paytr.com/odeme/iade").strip()
LIVE_SALES_ENABLED = _bool("LIVE_SALES_ENABLED", "false")

LEGAL_ENTITY_NAME = os.getenv("LEGAL_ENTITY_NAME", "").strip()
LEGAL_ADDRESS = os.getenv("LEGAL_ADDRESS", "").strip()
LEGAL_TAX_ID = os.getenv("LEGAL_TAX_ID", "").strip()
LEGAL_REGISTRY_ID = os.getenv("LEGAL_REGISTRY_ID", "").strip()
LEGAL_EMAIL = os.getenv("LEGAL_EMAIL", CONTACT_EMAIL).strip()
LEGAL_PHONE = os.getenv("LEGAL_PHONE", "").strip()
ACCOUNT_DELETION_GRACE_DAYS = max(1, min(int(os.getenv("ACCOUNT_DELETION_GRACE_DAYS", "7")), 30))

MAX_VIDEO_BYTES = int(os.getenv("LECTURESIFT_MAX_VIDEO_BYTES", str(1024 * 1024 * 1024)))
MAX_SOURCE_FILES = int(os.getenv("LECTURESIFT_MAX_SOURCE_FILES", "24"))
JOB_TTL_SECONDS = int(os.getenv("LECTURESIFT_JOB_TTL_SECONDS", str(6 * 60 * 60)))
GUEST_TRIAL_MAX_MINUTES = float(os.getenv("LECTURESIFT_GUEST_TRIAL_MINUTES", "5"))
INSTAGRAM_BONUS_MINUTES = int(os.getenv("LECTURESIFT_INSTAGRAM_BONUS_MINUTES", "30"))

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
REDIS_URL = os.getenv("REDIS_URL", CELERY_BROKER_URL)
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_REGION = os.getenv("S3_REGION", "auto")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".mpeg", ".mpg", ".m4v"}

LANGUAGE_NAMES = {
    "tr": "Turkish",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "hi": "Hindi",
}

SUMMARY_STYLES = {
    "short": "concise but complete; cover the central argument, essential definitions, and main conclusions in roughly 250-450 words",
    "standard": "broad and comprehensive; cover every major topic, definition, distinction, example, causal link, lecturer emphasis, and conclusion in a structured 700-1400 word summary",
    "detailed": "deep, explanatory, and comprehensive; preserve the lecture's conceptual sequence and explain difficult links in roughly 1400-2600 words when the source supports it",
    "exam": "comprehensive and exam-focused with definitions, distinctions, mechanisms, likely questions, examples, common traps, and lecturer emphasis",
    "five_minute": "a compact five-minute learning plan that still covers the minimum complete conceptual map, not merely a few bullet points",
}
