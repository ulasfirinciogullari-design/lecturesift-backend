import os
import tempfile
from pathlib import Path


APP_VERSION = "4.1"
WORK_DIR = Path(os.getenv("LECTURESIFT_WORK_DIR", str(Path(tempfile.gettempdir()) / "lecturesift")))
WORK_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET", "")
INSTAGRAM_ADMIN_TOKEN = os.getenv("INSTAGRAM_ADMIN_TOKEN", "")
INSTAGRAM_GRAPH_API_VERSION = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v23.0")
INSTAGRAM_DAILY_AUTOMATION_ENABLED = os.getenv("INSTAGRAM_DAILY_AUTOMATION_ENABLED", "false").lower() == "true"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "https://lecturesift.com").rstrip("/")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{WORK_DIR / 'billing.db'}")
BILLING_SESSION_SECRET = os.getenv("BILLING_SESSION_SECRET", "")
BILLING_ADMIN_TOKEN = os.getenv("BILLING_ADMIN_TOKEN", "")
BILLING_BANK_IBAN = os.getenv("BILLING_BANK_IBAN", "").replace(" ", "").upper()
BILLING_BANK_ACCOUNT_HOLDER = os.getenv("BILLING_BANK_ACCOUNT_HOLDER", "").strip()
BILLING_BANK_NAME = os.getenv("BILLING_BANK_NAME", "").strip()
BILLING_SUPPORT_EMAIL = os.getenv("BILLING_SUPPORT_EMAIL", "").strip()
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
JOB_TTL_SECONDS = int(os.getenv("LECTURESIFT_JOB_TTL_SECONDS", str(6 * 60 * 60)))

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
    "short": "very concise, key points only",
    "standard": "balanced, structured, and study-friendly",
    "detailed": "detailed, explanatory, and study-friendly",
    "exam": "exam-focused with definitions, distinctions, likely questions, and common traps",
    "five_minute": "a five-minute learning plan with the smallest useful set of ideas",
}

