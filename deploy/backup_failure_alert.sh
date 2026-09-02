#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run backup failure alerting as root." >&2
  exit 1
fi

set +x
umask 077
ENV_FILE="${LECTURESIFT_ENV_FILE:-/etc/lecturesift/runtime.env}"
ALERT_ROOT="/var/lib/lecturesift/backup-alerts"
ALERT_MARKER="$ALERT_ROOT/latest-failure"
FAILED_UNIT="${1:-lecturesift-backup.service}"

[[ "$FAILED_UNIT" =~ ^lecturesift-backup[.]service$ ]] || {
  echo "Refusing an unexpected backup failure unit name." >&2
  exit 1
}
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" && "$(stat -c '%u' -- "$ENV_FILE")" == "0" ]] || {
  echo "The root-only runtime environment is missing or unsafe." >&2
  exit 1
}
case "$(stat -c '%a' -- "$ENV_FILE")" in
  400|600) ;;
  *) echo "The root-only runtime environment must have mode 0400 or 0600." >&2; exit 1 ;;
esac

install -d -o root -g root -m 0700 -- "$ALERT_ROOT"
[[ ! -L "$ALERT_ROOT" && "$(realpath -e -- "$ALERT_ROOT")" == "$ALERT_ROOT" ]] || {
  echo "The backup alert evidence directory is unsafe." >&2
  exit 1
}
marker_tmp="$(mktemp -- "$ALERT_ROOT/.latest-failure-XXXXXXXX")"
{
  printf 'status=backup-failed\n'
  printf 'failed_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'unit=%s\n' "$FAILED_UNIT"
} >"$marker_tmp"
chmod 0600 "$marker_tmp"
mv -f -- "$marker_tmp" "$ALERT_MARKER"

# shellcheck disable=SC1090
source "$ENV_FILE" >/dev/null 2>&1
export EMAIL_PROVIDER EMAIL_FROM RESEND_API_KEY SMTP_HOST SMTP_PORT
export SMTP_USERNAME SMTP_PASSWORD SMTP_USE_TLS LECTURESIFT_OPS_ALERT_EMAIL
python3 - <<'PY'
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.request import Request, urlopen

provider = os.environ.get("EMAIL_PROVIDER", "").strip().lower()
sender = os.environ.get("EMAIL_FROM", "").strip()
recipient = os.environ.get("LECTURESIFT_OPS_ALERT_EMAIL", "").strip()
subject = "[LectureSift] Production backup failed"
text = (
    "The scheduled LectureSift PostgreSQL/Redis off-site backup failed. "
    "Production may still be serving, but recovery freshness is at risk. "
    "Inspect `systemctl status lecturesift-backup.service` and the root-only "
    "backup failure marker on the VPS immediately."
)
if not sender or not recipient:
    raise SystemExit("Backup failure marker recorded, but alert sender/recipient is missing")

if provider == "resend":
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        raise SystemExit("Backup failure marker recorded, but Resend is not configured")
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": sender, "to": [recipient], "subject": subject, "text": text}).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        if response.status < 200 or response.status >= 300:
            raise SystemExit("Backup failure marker recorded, but Resend rejected the alert")
elif provider == "smtp":
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    if not host or not username or not password:
        raise SystemExit("Backup failure marker recorded, but SMTP is not configured")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    use_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}
    with smtplib.SMTP(host, port, timeout=20) as client:
        if use_tls:
            client.starttls(context=ssl.create_default_context())
        client.login(username, password)
        client.send_message(message)
else:
    raise SystemExit("Backup failure marker recorded, but no supported email provider is configured")
PY

echo "Backup failure evidence recorded and the operations alert was sent."
