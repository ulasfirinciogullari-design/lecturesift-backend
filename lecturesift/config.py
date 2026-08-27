import os
from pathlib import Path


APP_VERSION = "4.1"
WORK_DIR = Path(os.getenv("LECTURESIFT_WORK_DIR", "/tmp/lecturesift"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MAX_VIDEO_BYTES = int(os.getenv("LECTURESIFT_MAX_VIDEO_BYTES", str(1024 * 1024 * 1024)))
MAX_SOURCE_FILES = int(os.getenv("LECTURESIFT_MAX_SOURCE_FILES", "24"))
JOB_TTL_SECONDS = int(os.getenv("LECTURESIFT_JOB_TTL_SECONDS", str(6 * 60 * 60)))
GUEST_TRIAL_MAX_MINUTES = float(os.getenv("LECTURESIFT_GUEST_TRIAL_MAX_MINUTES", "5"))
INSTAGRAM_BONUS_MINUTES = float(os.getenv("LECTURESIFT_INSTAGRAM_BONUS_MINUTES", "30"))

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
REDIS_URL = os.getenv("REDIS_URL", CELERY_BROKER_URL)
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_REGION = os.getenv("S3_REGION", "auto")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "support@lecturesift.com")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "LectureSift <noreply@lecturesift.com>")
ADMIN_TOKEN = os.getenv("LECTURESIFT_ADMIN_TOKEN", "")
BANK_TRANSFER_IBAN = os.getenv("BANK_TRANSFER_IBAN", "")
BANK_TRANSFER_RECIPIENT = os.getenv("BANK_TRANSFER_RECIPIENT", "")
INSTAGRAM_ACCOUNT = os.getenv("LECTURESIFT_INSTAGRAM_ACCOUNT", "lecturesift")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".mpeg", ".mpg", ".m4v"}

LANGUAGE_NAMES = {
    "tr": "Turkish", "en": "English", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "ar": "Arabic", "zh": "Chinese",
    "ja": "Japanese", "ko": "Korean", "hi": "Hindi",
}

SUMMARY_STYLES = {
    "short": "concise but still cover all major lecture themes and essential conclusions",
    "standard": "comprehensive, structured, explanatory, and study-friendly; cover every major theme, definition, distinction, example, and lecturer emphasis rather than producing a short abstract",
    "detailed": "very detailed, explanatory, sectioned, and study-friendly with context, examples, distinctions, and connections between concepts",
    "exam": "comprehensive and exam-focused with definitions, distinctions, likely questions, traps, examples, and lecturer emphasis",
    "five_minute": "a five-minute learning plan with the smallest useful set of ideas while still naming all major topics",
}
