import os
import tempfile
from pathlib import Path


APP_VERSION = "4.1"
WORK_DIR = Path(os.getenv("LECTURESIFT_WORK_DIR", str(Path(tempfile.gettempdir()) / "lecturesift")))
WORK_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
PRECISE_TRANSCRIPT_TIMESTAMPS = os.getenv(
    "LECTURESIFT_PRECISE_TRANSCRIPT_TIMESTAMPS", "false"
).lower() == "true"
TRANSCRIPTION_PARALLELISM = max(
    1, min(4, int(os.getenv("LECTURESIFT_TRANSCRIPTION_PARALLELISM", "3")))
)
MEDIA_PREP_PARALLELISM = max(
    1, min(2, int(os.getenv("LECTURESIFT_MEDIA_PREP_PARALLELISM", "2")))
)
SLIDE_ANALYSIS_PARALLELISM = max(
    1, min(3, int(os.getenv("LECTURESIFT_SLIDE_ANALYSIS_PARALLELISM", "2")))
)
SLIDE_EXPORT_PARALLELISM = max(
    1, min(2, int(os.getenv("LECTURESIFT_SLIDE_EXPORT_PARALLELISM", "2")))
)
TRANSLATION_PARALLELISM = max(
    1, min(4, int(os.getenv("LECTURESIFT_TRANSLATION_PARALLELISM", "3")))
)
STUDY_PACK_PARALLELISM = max(
    1, min(4, int(os.getenv("LECTURESIFT_STUDY_PACK_PARALLELISM", "3")))
)
SOURCE_DOWNLOAD_PARALLELISM = max(
    1, min(6, int(os.getenv("LECTURESIFT_SOURCE_DOWNLOAD_PARALLELISM", "4")))
)
STORAGE_TRANSFER_PARALLELISM = max(
    1, min(6, int(os.getenv("LECTURESIFT_STORAGE_TRANSFER_PARALLELISM", "4")))
)
STORAGE_FILE_TRANSFER_CONCURRENCY = max(
    1, min(4, int(os.getenv("LECTURESIFT_STORAGE_FILE_TRANSFER_CONCURRENCY", "2")))
)
DURATION_PROBE_PARALLELISM = max(
    1, min(6, int(os.getenv("LECTURESIFT_DURATION_PROBE_PARALLELISM", "4")))
)
ARTIFACT_EXPORT_PARALLELISM = max(
    1, min(4, int(os.getenv("LECTURESIFT_ARTIFACT_EXPORT_PARALLELISM", "3")))
)
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET", "")
INSTAGRAM_ADMIN_TOKEN = os.getenv("INSTAGRAM_ADMIN_TOKEN", "")
INSTAGRAM_GRAPH_API_VERSION = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v23.0")
INSTAGRAM_DAILY_AUTOMATION_ENABLED = os.getenv("INSTAGRAM_DAILY_AUTOMATION_ENABLED", "false").lower() == "true"
INSTAGRAM_DAILY_MEDIA_TYPE = os.getenv("INSTAGRAM_DAILY_MEDIA_TYPE", "IMAGE").strip().upper()
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or ""
).rstrip("/")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "https://lecturesift.com").rstrip("/")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{WORK_DIR / 'billing.db'}")
BILLING_SESSION_SECRET = os.getenv("BILLING_SESSION_SECRET", "")
ADMIN_ADMIN = os.getenv("ADMIN_ADMIN", "")
BILLING_PROTECTED_EMAILS = {
    email.strip().casefold()
    for email in os.getenv("BILLING_PROTECTED_EMAILS", "").split(",")
    if email.strip()
}
BILLING_BANK_IBAN = os.getenv("BILLING_BANK_IBAN", "").replace(" ", "").upper()
BILLING_BANK_ACCOUNT_HOLDER = os.getenv("BILLING_BANK_ACCOUNT_HOLDER", "").strip()
BILLING_BANK_NAME = os.getenv("BILLING_BANK_NAME", "").strip()
PAYTR_MERCHANT_ID = os.getenv("PAYTR_MERCHANT_ID", "").strip()
PAYTR_MERCHANT_KEY = os.getenv("PAYTR_MERCHANT_KEY", "")
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "")
PAYTR_TEST_MODE = os.getenv("PAYTR_TEST_MODE", "true").lower() == "true"
PAYTR_DEBUG = os.getenv("PAYTR_DEBUG", "false").lower() == "true"
IYZICO_API_KEY = os.getenv("IYZICO_API_KEY", "").strip()
IYZICO_SECRET_KEY = os.getenv("IYZICO_SECRET_KEY", "")
IYZICO_BASE_URL = os.getenv("IYZICO_BASE_URL", "https://api.iyzipay.com").rstrip("/")
IYZICO_BANK_TRANSFER_ENABLED = os.getenv(
    "IYZICO_BANK_TRANSFER_ENABLED", "false"
).lower() == "true"
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "support@lecturesift.com").strip()
BILLING_SUPPORT_EMAIL = os.getenv("BILLING_SUPPORT_EMAIL", CONTACT_EMAIL).strip()
LEGAL_OPERATOR_NAME = os.getenv("LEGAL_OPERATOR_NAME", "").strip()
LEGAL_OPERATOR_ADDRESS = os.getenv("LEGAL_OPERATOR_ADDRESS", "").strip()
LEGAL_OPERATOR_COUNTRY = os.getenv("LEGAL_OPERATOR_COUNTRY", "").strip()
LEGAL_OPERATOR_PHONE = os.getenv("LEGAL_OPERATOR_PHONE", "").strip()
LEGAL_OPERATOR_EMAIL = os.getenv("LEGAL_OPERATOR_EMAIL", BILLING_SUPPORT_EMAIL).strip()
LEGAL_TAX_ID = os.getenv("LEGAL_TAX_ID", "").strip()
LEGAL_TAX_OFFICE = os.getenv("LEGAL_TAX_OFFICE", "").strip()
LEGAL_REGISTRATION_ID = os.getenv("LEGAL_REGISTRATION_ID", "").strip()
LEGAL_MERSIS_ID = os.getenv("LEGAL_MERSIS_ID", "").strip()
LEGAL_TRADE_REGISTRY = os.getenv("LEGAL_TRADE_REGISTRY", "").strip()
LEGAL_KEP_ADDRESS = os.getenv("LEGAL_KEP_ADDRESS", "").strip()
LEGAL_CHAMBER_NAME = os.getenv("LEGAL_CHAMBER_NAME", "").strip()
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "none").strip().lower()
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
MAX_VIDEO_BYTES = int(os.getenv("LECTURESIFT_MAX_VIDEO_BYTES", str(1024 * 1024 * 1024)))
MAX_SOURCE_FILES = int(os.getenv("LECTURESIFT_MAX_SOURCE_FILES", "24"))
MAX_DOCUMENT_BYTES = int(os.getenv("LECTURESIFT_MAX_DOCUMENT_BYTES", str(100 * 1024 * 1024)))
MAX_DOCUMENT_PAGES = int(os.getenv("LECTURESIFT_MAX_DOCUMENT_PAGES", "500"))
MAX_DOCUMENT_CHARACTERS = int(os.getenv("LECTURESIFT_MAX_DOCUMENT_CHARACTERS", "1500000"))
DOCUMENT_WORDS_PER_CREDIT_MINUTE = max(
    50, int(os.getenv("LECTURESIFT_DOCUMENT_WORDS_PER_CREDIT_MINUTE", "200"))
)
OCR_ENABLED = os.getenv("LECTURESIFT_OCR_ENABLED", "true").lower() == "true"
OCR_COMMAND = os.getenv("LECTURESIFT_OCR_COMMAND", "tesseract").strip() or "tesseract"
OCR_DPI = max(150, min(300, int(os.getenv("LECTURESIFT_OCR_DPI", "200"))))
OCR_MAX_PAGES = max(1, min(MAX_DOCUMENT_PAGES, int(os.getenv("LECTURESIFT_OCR_MAX_PAGES", "150"))))
OCR_PARALLELISM = max(1, min(4, int(os.getenv("LECTURESIFT_OCR_PARALLELISM", "2"))))
OCR_PAGE_TIMEOUT_SECONDS = max(10, int(os.getenv("LECTURESIFT_OCR_PAGE_TIMEOUT_SECONDS", "45")))
OCR_MIN_NATIVE_CHARACTERS = max(1, int(os.getenv("LECTURESIFT_OCR_MIN_NATIVE_CHARACTERS", "24")))
OCR_ESTIMATED_WORDS_PER_PAGE = max(50, int(os.getenv("LECTURESIFT_OCR_ESTIMATED_WORDS_PER_PAGE", "400")))
JOB_TTL_SECONDS = int(os.getenv("LECTURESIFT_JOB_TTL_SECONDS", str(6 * 60 * 60)))
GUEST_TRIAL_MAX_MINUTES = float(os.getenv("LECTURESIFT_GUEST_TRIAL_MINUTES", "5"))
INSTAGRAM_BONUS_MINUTES = int(os.getenv("LECTURESIFT_INSTAGRAM_BONUS_MINUTES", "30"))
REWARDED_ADS_ENABLED = os.getenv("LECTURESIFT_REWARDED_ADS_ENABLED", "false").lower() == "true"
REWARDED_AD_UNIT_PATH = os.getenv("LECTURESIFT_REWARDED_AD_UNIT_PATH", "").strip()
REWARDED_AD_MINUTES_PER_VIEW = max(1, int(os.getenv("LECTURESIFT_REWARDED_AD_MINUTES_PER_VIEW", "1")))
REWARDED_AD_DAILY_LIMIT_MINUTES = max(
    REWARDED_AD_MINUTES_PER_VIEW,
    int(os.getenv("LECTURESIFT_REWARDED_AD_DAILY_LIMIT_MINUTES", "3")),
)
DISPLAY_ADS_ENABLED = os.getenv("LECTURESIFT_DISPLAY_ADS_ENABLED", "false").lower() == "true"
DISPLAY_AD_UNIT_PATH = os.getenv("LECTURESIFT_DISPLAY_AD_UNIT_PATH", "").strip()
ADSENSE_PUBLISHER_ID = os.getenv(
    "LECTURESIFT_ADSENSE_PUBLISHER_ID", "ca-pub-7608481350058806"
).strip()
SITE_BANNER_ENABLED = os.getenv("LECTURESIFT_SITE_BANNER_ENABLED", "true").lower() == "true"
SITE_BANNER_TITLE = os.getenv(
    "LECTURESIFT_SITE_BANNER_TITLE", "Derslerini daha hızlı çalış"
).strip()
SITE_BANNER_TEXT = os.getenv(
    "LECTURESIFT_SITE_BANNER_TEXT",
    "Quiz, bilgi kartı, akıllı özet ve daha fazla işleme dakikası için planları keşfet.",
).strip()
SITE_BANNER_CTA = os.getenv("LECTURESIFT_SITE_BANNER_CTA", "Planları incele").strip()
SITE_BANNER_URL = os.getenv("LECTURESIFT_SITE_BANNER_URL", "/plans.html").strip()
ACCOUNT_ACTIVITY_RETENTION_DAYS = max(
    30, int(os.getenv("LECTURESIFT_ACCOUNT_ACTIVITY_RETENTION_DAYS", "180"))
)
ANALYTICS_ENABLED = os.getenv("LECTURESIFT_ANALYTICS_ENABLED", "false").lower() == "true"
GA_MEASUREMENT_ID = os.getenv("LECTURESIFT_GA_MEASUREMENT_ID", "").strip().upper()
GOOGLE_ADS_ID = os.getenv("LECTURESIFT_GOOGLE_ADS_ID", "").strip().upper()
GOOGLE_ADS_SIGNUP_LABEL = os.getenv("LECTURESIFT_GOOGLE_ADS_SIGNUP_LABEL", "").strip()
GOOGLE_ADS_PURCHASE_LABEL = os.getenv("LECTURESIFT_GOOGLE_ADS_PURCHASE_LABEL", "").strip()

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
REDIS_URL = os.getenv("REDIS_URL", CELERY_BROKER_URL)
REQUIRE_DURABLE_PROCESSING = os.getenv("LECTURESIFT_REQUIRE_DURABLE_PROCESSING", "false").lower() == "true"
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_REGION = os.getenv("S3_REGION", "auto")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
DATABASE_RECOVERY_CONFIRMED = os.getenv(
    "LECTURESIFT_DATABASE_RECOVERY_CONFIRMED", "false"
).lower() == "true"
OBJECT_RETENTION_CONFIRMED = os.getenv(
    "LECTURESIFT_OBJECT_RETENTION_CONFIRMED", "false"
).lower() == "true"
RECOVERY_DRILL_CONFIRMED = os.getenv(
    "LECTURESIFT_RECOVERY_DRILL_CONFIRMED", "false"
).lower() == "true"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".mpeg", ".mpg", ".m4v"}
AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wma", ".aiff", ".aif", ".mka",
}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}

# Cost inputs are deliberately separated from invoice confirmation. A numeric
# value is a planning input; it becomes a verified fixed expense only when the
# matching *_CONFIRMED flag is true. This prevents a missing value or an old
# plan price from being presented as an exact accounting total.
def _cost_float(name: str, default: str = "0") -> float:
    try:
        return max(0.0, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return max(0.0, float(default))


def _cost_confirmed(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


COST_USD_TRY_FALLBACK = _cost_float("LECTURESIFT_COST_USD_TRY_FALLBACK", "42")
COST_RENDER_MONTHLY_USD = _cost_float("LECTURESIFT_COST_RENDER_MONTHLY_USD")
COST_RENDER_CONFIRMED = _cost_confirmed("LECTURESIFT_COST_RENDER_CONFIRMED")
COST_NETLIFY_MONTHLY_USD = _cost_float("LECTURESIFT_COST_NETLIFY_MONTHLY_USD", "20")
COST_NETLIFY_CONFIRMED = _cost_confirmed("LECTURESIFT_COST_NETLIFY_CONFIRMED")
COST_RESEND_MONTHLY_USD = _cost_float("LECTURESIFT_COST_RESEND_MONTHLY_USD")
COST_RESEND_CONFIRMED = _cost_confirmed("LECTURESIFT_COST_RESEND_CONFIRMED")
COST_DOMAIN_ANNUAL_USD = _cost_float("LECTURESIFT_COST_DOMAIN_ANNUAL_USD")
COST_DOMAIN_CONFIRMED = _cost_confirmed("LECTURESIFT_COST_DOMAIN_CONFIRMED")
COST_OTHER_MONTHLY_USD = _cost_float("LECTURESIFT_COST_OTHER_MONTHLY_USD")
COST_OTHER_CONFIRMED = _cost_confirmed("LECTURESIFT_COST_OTHER_CONFIRMED")

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
