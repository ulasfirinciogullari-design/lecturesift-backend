from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old!r}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "main.py",
    'from lecturesift.app import app\n\n__all__ = ["app"]\n',
    'from lecturesift.app import app\nfrom lecturesift.email_auth import install_email_auth\n\ninstall_email_auth(app)\n\n__all__ = ["app"]\n',
)

replace_once(
    "lecturesift/config.py",
    'BILLING_SUPPORT_EMAIL = os.getenv("BILLING_SUPPORT_EMAIL", "").strip()\n',
    'BILLING_SUPPORT_EMAIL = os.getenv("BILLING_SUPPORT_EMAIL", "").strip()\n'
    'RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")\n'
    'RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "LectureSift <no-reply@mail.lecturesift.com>").strip()\n'
    'EMAIL_VERIFICATION_REQUIRED = os.getenv("EMAIL_VERIFICATION_REQUIRED", "false").lower() == "true"\n'
    'EMAIL_VERIFICATION_SECRET = os.getenv("EMAIL_VERIFICATION_SECRET", "")\n'
    'EMAIL_VERIFICATION_TTL_SECONDS = max(300, int(os.getenv("EMAIL_VERIFICATION_TTL_SECONDS", "600")))\n'
    'EMAIL_VERIFICATION_RESEND_SECONDS = max(30, int(os.getenv("EMAIL_VERIFICATION_RESEND_SECONDS", "60")))\n'
    'EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR = max(1, int(os.getenv("EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR", "5")))\n'
    'EMAIL_VERIFICATION_MAX_ATTEMPTS = max(3, int(os.getenv("EMAIL_VERIFICATION_MAX_ATTEMPTS", "5")))\n',
)

replace_once(
    "render.yaml",
    '      - key: BILLING_SESSION_SECRET\n        generateValue: true\n',
    '      - key: BILLING_SESSION_SECRET\n        generateValue: true\n'
    '      - key: RESEND_API_KEY\n        sync: false\n'
    '      - key: RESEND_FROM_EMAIL\n        value: LectureSift <no-reply@mail.lecturesift.com>\n'
    '      - key: EMAIL_VERIFICATION_REQUIRED\n        value: false\n'
    '      - key: EMAIL_VERIFICATION_SECRET\n        generateValue: true\n',
)

replace_once(
    "frontend/index.html",
    '  <script src="./app.js"></script>\n',
    '  <script src="./app.js"></script>\n  <script src="./email-auth.js"></script>\n',
)

replace_once(
    "README.md",
    "## Local development\n",
    """## Email verification\n\nProduction registration can require a one-time email code delivered through Resend. The verification state is stored in the billing database; codes are HMAC-hashed, expire after 10 minutes, are single-use, and are protected by resend and attempt limits. Existing accounts created before activation remain valid.\n\nRequired production variables:\n\n- `RESEND_API_KEY`\n- `RESEND_FROM_EMAIL` (recommended: `LectureSift <no-reply@mail.lecturesift.com>`)\n- `EMAIL_VERIFICATION_SECRET`\n- `EMAIL_VERIFICATION_REQUIRED=true` after the sending domain is verified\n\nOptional controls are `EMAIL_VERIFICATION_TTL_SECONDS`, `EMAIL_VERIFICATION_RESEND_SECONDS`, `EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR`, and `EMAIL_VERIFICATION_MAX_ATTEMPTS`. Health is available at `GET /billing/email/health`.\n\n## Local development\n""",
)
