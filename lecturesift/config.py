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
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{WORK_DIR / 'billing.db'}")
BILLING_SESSION_SECRET = os.getenv("BILLING_SESSION_SECRET", "")
BILLING_ADMIN_TOKEN = os.getenv("BILLING_ADMIN_TOKEN", "")
BILLING_BANK_IBAN = os.getenv("BILLING_BANK_IBAN", "").replace(" ", "").upper()
BILLING_BANK_ACCOUNT_HOLDER = os.getenv("BILLING_BANK_ACCOUNT_HOLDER", "").strip()
BILLING_BANK_NAME = os.getenv("BILLING_BANK_NAME", "").strip()
BILLING_SUPPORT_EMAIL = os.getenv("BILLING_SUPPORT_EMAIL", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "LectureSift <no-reply@mail.lecturesift.com>").strip()
EMAIL_VERIFICATION_REQUIRED = os.getenv("EMAIL_VERIFICATION_REQUIRED", "false").lower() == "true"
EMAIL_VERIFICATION_SECRET = os.getenv("EMAIL_VERIFICATION_SECRET", "")
EMAIL_VERIFICATION_TTL_SECONDS = max(300, int(os.getenv("EMAIL_VERIFICATION_TTL_SECONDS", "600")))
EMAIL_VERIFICATION_RESEND_SECONDS = max(30, int(os.getenv("EMAIL_VERIFICATION_RESEND_SECONDS", "60")))
EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR = max(1, int(os.getenv("EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR", "5")))
EMAIL_VERIFICATION_MAX_ATTEMPTS = max(3, int(os.getenv("EMAIL_VERIFICATION_MAX_ATTEMPTS", "5")))
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

