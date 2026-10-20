"""Move one guest trial's results into the account created or opened from it.

Only the holder of the signed guest session can start a claim: directly when
signing in, or through a pending claim stored at registration and applied
once the new account verifies its email. A guest identity is claimed by one
account only, enforced by a primary-key marker in the same transaction.
Usage events stay with the guest: the trial was its own allowance.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import billing_service as billing
from .billing_service import (
    AUTH_TOKENS,
    GUEST_CLAIM_PENDING_PURPOSE,
    GUEST_CLAIMED_PURPOSE,
    USERS,
    USER_PROFILES,
    BillingAuthenticationError,
    BillingError,
    _guest_claim_pending_hash,
    _guest_claimed_hash,
    _lock_billing_user,
    _token_is_expired,
    account_status,
    authenticate_session,
    utcnow,
)
from .rollout_service import GUEST_TRIALS, init_rollout_database

logger = logging.getLogger(__name__)

GUEST_EMAIL_SUFFIX = "@guest.lecturesift.invalid"
_GUEST_SESSION_ERROR = "Misafir oturumu doğrulanamadı."
_CLAIM_RANK = {
    "not_provided": 0, "empty": 1, "expired": 2, "refused": 2,
    "unavailable": 2, "already_claimed": 3, "claimed": 4,
}


def guest_user_for_token(token: str) -> str:
    """Return the guest user id of a valid signed guest session token."""
    selected = str(token or "").strip()
    if not selected or len(selected) > 4096:
        raise BillingAuthenticationError(_GUEST_SESSION_ERROR)
    try:
        user = authenticate_session(selected)
    except BillingAuthenticationError as exc:
        raise BillingAuthenticationError(_GUEST_SESSION_ERROR) from exc
    init_rollout_database()
    with billing.ENGINE.connect() as connection:
        trial = connection.execute(
            select(GUEST_TRIALS.c.user_id).where(GUEST_TRIALS.c.user_id == user["id"])
        ).first()
    if not trial or not str(user.get("email", "")).endswith(GUEST_EMAIL_SUFFIX):
        raise BillingAuthenticationError(_GUEST_SESSION_ERROR)
    return user["id"]


def claim_guest_account(account_user_id: str, guest_user_id: str) -> dict:
    """Move a guest's jobs and library placements to a verified account once."""
    from . import workspace_state
    from .jobs import JOBS

    if not account_user_id or not guest_user_id or account_user_id == guest_user_id:
        raise BillingError("Misafir sonucu bu hesaba taşınamaz.")
    init_rollout_database()
    history_days = int(account_status(account_user_id)["plan"].get("history_days") or 0)
    retention_seconds = max(1, history_days) * 24 * 60 * 60
    now = utcnow()
    with billing.ENGINE.begin() as connection:
        for user_id in sorted((account_user_id, guest_user_id)):
            _lock_billing_user(connection, user_id)
        account = connection.execute(select(USERS).where(USERS.c.id == account_user_id)).first()
        profile = connection.execute(
            select(USER_PROFILES).where(USER_PROFILES.c.user_id == account_user_id)
        ).first()
        if (
            not account
            or account.email.endswith((GUEST_EMAIL_SUFFIX, "@users.invalid"))
            or (profile and profile.email_verified_at is None)
        ):
            raise BillingError("Misafir sonucu bu hesaba taşınamaz.")
        trial = connection.execute(
            select(GUEST_TRIALS.c.user_id).where(GUEST_TRIALS.c.user_id == guest_user_id)
        ).first()
        if not trial:
            raise BillingAuthenticationError(_GUEST_SESSION_ERROR)
        marker = connection.execute(
            select(AUTH_TOKENS).where(AUTH_TOKENS.c.token_hash == _guest_claimed_hash(guest_user_id))
        ).first()
        if marker:
            if marker.user_id == account_user_id:
                return {"status": "already_claimed", "jobs": 0}
            raise BillingError("Bu misafir sonucu başka bir hesaba kaydedilmiş.")
        if not JOBS.list_for_user(guest_user_id, limit=None):
            return {"status": "empty", "jobs": 0}
        try:
            # Both user rows are locked, so a parallel claim waits and then
            # sees this marker; the primary key is the final guard.
            connection.execute(
                AUTH_TOKENS.insert().values(
                    token_hash=_guest_claimed_hash(guest_user_id),
                    user_id=account_user_id,
                    purpose=GUEST_CLAIMED_PURPOSE,
                    expires_at=now + timedelta(days=3650),
                    consumed_at=now,
                    created_at=now,
                )
            )
        except IntegrityError as exc:
            raise BillingError("Bu misafir sonucu daha önce kaydedilmiş.") from exc
        workspace_state.transfer_account_state(connection, guest_user_id, account_user_id)
        # The old guest token stops working; the next guest session for this
        # browser resumes an identity whose single trial is already used.
        connection.execute(
            update(USER_PROFILES)
            .where(USER_PROFILES.c.user_id == guest_user_id)
            .values(session_version=USER_PROFILES.c.session_version + 1, updated_at=now)
        )
        # Last step: a job-store failure rolls back the marker above.
        moved = JOBS.transfer_owner(guest_user_id, account_user_id, retention_seconds=retention_seconds)
    return {"status": "claimed", "jobs": len(moved)}


def claim_guest_session(account_user_id: str, guest_token: str) -> dict:
    return claim_guest_account(account_user_id, guest_user_for_token(guest_token))


def claim_pending_guest(account_user_id: str) -> dict:
    """Apply the claim stored when this account registered from a guest session."""
    init_rollout_database()
    token_hash = _guest_claim_pending_hash(account_user_id)
    with billing.ENGINE.connect() as connection:
        pending = connection.execute(
            select(AUTH_TOKENS).where(
                AUTH_TOKENS.c.token_hash == token_hash,
                AUTH_TOKENS.c.purpose == GUEST_CLAIM_PENDING_PURPOSE,
            )
        ).first()
    if not pending or pending.consumed_at is not None:
        return {"status": "not_provided", "jobs": 0}
    if _token_is_expired(pending.expires_at):
        result = {"status": "expired", "jobs": 0}
    else:
        try:
            result = claim_guest_account(account_user_id, pending.user_id)
        except BillingError:
            result = {"status": "refused", "jobs": 0}
    # Definitive outcomes consume it; a transient failure raised above keeps
    # it for the next sign-in.
    with billing.ENGINE.begin() as connection:
        connection.execute(
            update(AUTH_TOKENS)
            .where(AUTH_TOKENS.c.token_hash == token_hash, AUTH_TOKENS.c.consumed_at.is_(None))
            .values(consumed_at=utcnow())
        )
    return result


def claim_guest_work(account_user_id: str, guest_token: str = "") -> dict:
    """Best effort for sign-in routes: a failed claim never blocks signing in."""
    attempts = [lambda: claim_pending_guest(account_user_id)]
    if str(guest_token or "").strip():
        attempts.insert(0, lambda: claim_guest_session(account_user_id, guest_token))
    result = {"status": "not_provided", "jobs": 0}
    for attempt in attempts:
        try:
            outcome = attempt()
        except BillingError:
            outcome = {"status": "refused", "jobs": 0}
        except Exception:
            logger.exception("Guest result claim failed")
            outcome = {"status": "unavailable", "jobs": 0}
        if _CLAIM_RANK.get(outcome["status"], 0) > _CLAIM_RANK.get(result["status"], 0):
            result = outcome
    return result
