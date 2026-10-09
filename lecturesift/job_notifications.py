"""API-side, leased delivery of terminal job-failure emails.

Workers only enqueue job IDs in the existing durable job store. They never need
customer email addresses, preferred languages, or the corporate mail API key.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time

from sqlalchemy import select

from . import billing_service as billing, config
from .jobs import JOBS
from .mailer import send_transactional_email

LOGGER = logging.getLogger(__name__)
RETRY_DELAYS = (60, 300, 900, 3600, 10800)


def queue_failure_notification(job_id: str, quota_context: dict | None = None) -> None:
    """Only explicit terminal paths call this; email trouble never changes a job."""
    try:
        safe_context = {key: value for key, value in (quota_context or {}).items()
            if key in {"required_minutes", "remaining_minutes", "max_minutes_per_job"}
            and (value is None or isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 10**12)}
        JOBS.queue_failure_notification(job_id, safe_context)
    except Exception:
        LOGGER.error("Could not persist a job failure email request")


def _recipient(user_id: str) -> dict | None:
    # An inner profile join intentionally excludes legacy/unverified records.
    # Erased and anonymous identities are never a transactional-mail recipient.
    with billing.ENGINE.connect() as connection:
        row = connection.execute(select(
            billing.USERS.c.email,
            billing.USER_PREFERENCES.c.preferred_language,
        ).select_from(billing.USERS.join(billing.USER_PROFILES,
            billing.USER_PROFILES.c.user_id == billing.USERS.c.id).outerjoin(
            billing.USER_PREFERENCES, billing.USER_PREFERENCES.c.user_id == billing.USERS.c.id
        )).where(billing.USERS.c.id == user_id,
            billing.USER_PROFILES.c.email_verified_at.is_not(None))).first()
    if (not row or not row.email or "@" not in row.email
        or row.email.lower().endswith(("@users.invalid", "@guest.lecturesift.invalid"))):
        return None
    return {"email": row.email, "language": row.preferred_language or "tr"}


def _template_context(data: dict, recipient: dict) -> dict:
    options = data.get("options") or {}
    context = {
        "language": recipient["language"], "error_code": str(data.get("error_code") or "LS-SYSTEM-01"),
        "job_id": str(data["job_id"]), "document_mode": bool(options.get("document_mode")),
    }
    if context["error_code"] == "LS-BILL-10":
        # Use the allowance at rejection, never a new balance after a top-up.
        context.update((data.get("_failure_notification") or {}).get("quota_context") or {})
    return context


def deliver_one(job_id: str) -> str:
    """Claim/send outside job locks; freeze non-PII template inputs across retries."""
    claimed = JOBS.claim_failure_notification(job_id)
    if not claimed:
        return "skipped"
    data, claim = claimed
    notice = data["_failure_notification"]
    delivery_attempted = False
    try:
        recipient = _recipient(str((data.get("options") or {}).get("billing_user_id") or ""))
        if not recipient:
            JOBS.update_failure_notification(job_id, claim, state="ineligible", lease_until=0)
            return "ineligible"
        context = notice.get("template_context") or _template_context(data, recipient)
        if not JOBS.update_failure_notification(job_id, claim, template_context=context):
            return "skipped"
        from .job_notification_templates import render_job_failure_email
        message = notice.get("message") or render_job_failure_email(**context)
        recipient_hash = hashlib.sha256(recipient["email"].encode()).hexdigest()
        if notice.get("recipient_hash") and notice["recipient_hash"] != recipient_hash:
            JOBS.update_failure_notification(job_id, claim, state="recipient_changed", lease_until=0)
            return "recipient_changed"
        envelope_hash = hashlib.sha256(json.dumps({"provider": config.EMAIL_PROVIDER, "from": config.EMAIL_FROM,
            "reply_to": config.BILLING_SUPPORT_EMAIL, "recipient": recipient_hash,
            "message": message}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if notice.get("envelope_hash") and notice["envelope_hash"] != envelope_hash:
            JOBS.update_failure_notification(job_id, claim, state="envelope_changed", lease_until=0)
            return "envelope_changed"
        # Freeze the rendered, name-free content too, so a deployment changing
        # translations cannot change a retried idempotent provider request.
        if not JOBS.update_failure_notification(job_id, claim, message=message,
                                               recipient_hash=recipient_hash, envelope_hash=envelope_hash):
            return "skipped"
        # A restart after an ambiguous provider response reuses this exact key
        # and frozen content. The retry window is shorter than provider dedup.
        key = "job-failure/" + hashlib.sha256(job_id.encode()).hexdigest()
        delivery_attempted = True
        provider_id = send_transactional_email(recipient["email"], message["subject"], message["html"], message["text"],
            reply_to=config.BILLING_SUPPORT_EMAIL, idempotency_key=key)
        JOBS.update_failure_notification(job_id, claim, state="sent", sent_at=time.time(),
                                         provider_id=provider_id, lease_until=0)
        return "sent"
    except Exception:
        # SQL/provider exceptions can contain PII: store only bounded state.
        attempts = int(notice.get("attempts") or 1)
        uncertain_smtp = delivery_attempted and config.EMAIL_PROVIDER != "resend"
        exhausted = attempts >= 6 or uncertain_smtp
        JOBS.update_failure_notification(job_id, claim, state="delivery_unknown" if uncertain_smtp else "exhausted" if exhausted else "retry",
            next_attempt_at=time.time() + RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)], lease_until=0)
        LOGGER.warning("Job failure email delivery deferred" if not exhausted else "Job failure email delivery exhausted")
        return "exhausted" if exhausted else "retry"


def deliver_pending() -> None:
    if os.getenv("LECTURESIFT_WORKER") == "1" or config.current_maintenance_mode() != "off":
        return
    for job_id in JOBS.failure_notifications_due(limit=5):
        try:
            deliver_one(job_id)
        except Exception:
            LOGGER.error("Job failure email dispatcher unavailable")


async def delivery_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(deliver_pending)
        except Exception:
            LOGGER.error("Job failure email dispatcher unavailable")
        await asyncio.sleep(30)
