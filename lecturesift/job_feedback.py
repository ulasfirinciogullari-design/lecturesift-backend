"""Feedback on a finished study pack.

One row per job: a thumb (up/down), an optional one-line comment and the
author's consent to show that comment on the site with their initials.
Guests leave no account reference. The comment is stored as plain text,
capped in length and never written to logs.
"""
from __future__ import annotations

import csv
import io
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, select, update
from sqlalchemy.exc import IntegrityError

from . import billing_service
from .billing_service import METADATA, SUBSCRIPTIONS, USERS, USER_PROFILES, BillingError

LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")
RATINGS = ("up", "down")
COMMENT_MAX_LENGTH = 280
# Administrator CSV export columns, in order.
CSV_COLUMNS = (
    "created_at", "updated_at", "rating", "comment", "consent",
    "plan", "locale", "job_id", "user_id", "email",
)

JOB_FEEDBACK = Table(
    "lecturesift_job_feedback",
    METADATA,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(80), nullable=False, unique=True),
    # NULL for guests: their trial account is temporary and must not be shown.
    Column("user_id", String(36), ForeignKey("billing_users.id"), nullable=True, index=True),
    Column("rating", String(8), nullable=False),
    Column("comment", String(COMMENT_MAX_LENGTH), nullable=True),
    Column("consent", Boolean, nullable=False, default=False),
    Column("locale", String(8), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _now() -> datetime:
    return billing_service.utcnow()


def _ensure_ready() -> None:
    # rollout_service owns table creation (and imports this table), so the
    # import stays local to avoid a cycle.
    from .rollout_service import init_rollout_database

    init_rollout_database()


def _is_guest(user: dict[str, Any]) -> bool:
    from .guest_claim import GUEST_EMAIL_SUFFIX

    return str(user.get("email") or "").endswith(GUEST_EMAIL_SUFFIX)


def normalize_comment(value: Any) -> str:
    """One line of plain text: no control or format characters, single spaces."""
    text = unicodedata.normalize("NFC", str(value or ""))
    visible = "".join(" " if unicodedata.category(char).startswith("C") else char for char in text)
    return " ".join(visible.split())


def normalize_locale(value: Any) -> str:
    code = str(value or "").strip().replace("_", "-").split("-", 1)[0].casefold()
    if code not in LANGUAGES:
        raise BillingError("Geçersiz dil kodu.")
    return code


def _iso(value: Any) -> Any:
    if not isinstance(value, datetime):
        return value
    # SQLite hands back naive timestamps; they were stored as UTC.
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _store(job_id: str, values: dict[str, Any], now: datetime) -> bool:
    """Replace the job's row, or insert it. Returns True when a row was created."""
    with billing_service.ENGINE.begin() as connection:
        replaced = connection.execute(
            update(JOB_FEEDBACK).where(JOB_FEEDBACK.c.job_id == job_id).values(**values)
        ).rowcount
        if replaced:
            return False
        connection.execute(
            JOB_FEEDBACK.insert().values(id=str(uuid.uuid4()), job_id=job_id, created_at=now, **values)
        )
        return True


def save_job_feedback(
    job_id: str,
    user: dict[str, Any],
    *,
    rating: str,
    comment: Any = "",
    consent: bool = False,
    locale: Any = "tr",
) -> dict[str, Any]:
    """Store the single feedback row of a job; a repeat submission replaces it."""
    if rating not in RATINGS:
        raise BillingError("Geçersiz değerlendirme.")
    text = normalize_comment(comment)
    if len(text) > COMMENT_MAX_LENGTH:
        raise BillingError(f"Yorum en fazla {COMMENT_MAX_LENGTH} karakter olabilir.")
    language = normalize_locale(locale)
    _ensure_ready()
    now = _now()
    values = {
        "user_id": None if _is_guest(user) else str(user["id"]),
        "rating": rating,
        "comment": text or None,
        "consent": bool(consent),
        "locale": language,
        "updated_at": now,
    }
    try:
        created = _store(job_id, values, now)
    except IntegrityError:
        # Two first submissions for the same job raced; the later one replaces
        # the row the other just created.
        with billing_service.ENGINE.begin() as connection:
            connection.execute(update(JOB_FEEDBACK).where(JOB_FEEDBACK.c.job_id == job_id).values(**values))
        created = False
    with billing_service.ENGINE.connect() as connection:
        row = connection.execute(select(JOB_FEEDBACK).where(JOB_FEEDBACK.c.job_id == job_id)).one()
    return {
        "job_id": row.job_id,
        "rating": row.rating,
        "comment": row.comment or "",
        "consent": bool(row.consent),
        "locale": row.locale,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "updated": not created,
    }


def _active_plans(connection: Any, user_ids: set[str], now: datetime) -> dict[str, str]:
    """Plan code of each user's current subscription, latest expiry first."""
    if not user_ids:
        return {}
    rows = connection.execute(
        select(SUBSCRIPTIONS.c.user_id, SUBSCRIPTIONS.c.plan_code)
        .where(
            SUBSCRIPTIONS.c.user_id.in_(sorted(user_ids)),
            SUBSCRIPTIONS.c.status.in_(("active", "cancel_at_end")),
            SUBSCRIPTIONS.c.ends_at > now,
        )
        .order_by(SUBSCRIPTIONS.c.ends_at.desc())
    ).all()
    plans: dict[str, str] = {}
    for row in rows:
        plans.setdefault(row.user_id, row.plan_code)
    return plans


def _initials(first_name: Any, last_name: Any, email: Any) -> str:
    letters = "".join(str(value or "").strip()[:1].upper() for value in (first_name, last_name))
    return letters or str(email or "").strip()[:1].upper()


def _public(row: Any, plans: dict[str, str]) -> dict[str, Any]:
    guest = row.user_id is None
    email = None if guest else row.email
    return {
        "id": row.id,
        "job_id": row.job_id,
        "user_id": row.user_id,
        "email": email,
        "initials": "" if guest else _initials(row.first_name, row.last_name, email),
        "plan": "guest" if guest else plans.get(row.user_id, "free"),
        "rating": row.rating,
        "comment": row.comment or "",
        "consent": bool(row.consent),
        "locale": row.locale,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def list_job_feedback(limit: int = 250) -> list[dict[str, Any]]:
    """Newest feedback first, with the author's current plan ("guest" for guests)."""
    _ensure_ready()
    safe_limit = max(1, min(int(limit), 500))
    now = _now()
    with billing_service.ENGINE.connect() as connection:
        rows = connection.execute(
            select(JOB_FEEDBACK, USERS.c.email, USER_PROFILES.c.first_name, USER_PROFILES.c.last_name)
            .select_from(
                JOB_FEEDBACK.outerjoin(USERS, USERS.c.id == JOB_FEEDBACK.c.user_id)
                .outerjoin(USER_PROFILES, USER_PROFILES.c.user_id == JOB_FEEDBACK.c.user_id)
            )
            .order_by(JOB_FEEDBACK.c.updated_at.desc(), JOB_FEEDBACK.c.created_at.desc())
            .limit(safe_limit)
        ).all()
        plans = _active_plans(connection, {row.user_id for row in rows if row.user_id}, now)
    return [_public(row, plans) for row in rows]


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # Spreadsheet formula guard, the same rule as the panel's client-side exports.
    return f"'{text}" if text[:1] in "=+-@\t\r" else text


def job_feedback_csv(limit: int = 500) -> str:
    """UTF-8 CSV (with BOM so spreadsheet apps read Turkish text) of the newest feedback."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for item in list_job_feedback(limit):
        writer.writerow([_csv_cell(item.get(column)) for column in CSV_COLUMNS])
    return "﻿" + buffer.getvalue()
