"""Opt-in referral ledger. Never included in normal billing schema creation.

No provider call or automatic release occurs here. A trusted operator must
reconcile the paid order after the hold; elapsed time alone is insufficient.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import re
import secrets
import uuid

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, UniqueConstraint, case, func, inspect, or_, select, update

from . import billing_service as billing
from .referral_policy import (
    FIRST_PURCHASE_TERMS, REGIONAL_FIRST_PURCHASE_TERMS, RENEWAL_TERMS,
    POLICY_TERMS, REGIONAL_COUPON_CAPS_MINOR, coupon_terms_for_policy, policy_for_purchase,
)

LOGGER = logging.getLogger(__name__)
METADATA = MetaData()
POLICY_VERSION = REGIONAL_FIRST_PURCHASE_TERMS.version
# The September 9 release includes the verified PG18 v4 schema/recovery and
# separate runtime-role evidence. Campaign and runtime flags remain required.
SCHEMA_RECOVERY_RELEASE_READY = True
ELIGIBLE_PLANS = frozenset({"lite", "plus", "pro", "max"})
HOLD_DAYS = 14
MONTHLY_CAP = 5
INVITER_MINUTES = 60
INVITEE_MINUTES = 30
COUPON_PERCENT = 10
COUPON_MAX_MINOR = 5000
COUPON_DAYS = 90
CODE_RE = re.compile(r"LS[RC]-[A-F0-9]{24}")

# No foreign keys into the independent billing metadata: this explicit schema
# must not be created implicitly by API/worker startup. IDs are validated and
# locked by every mutation. Close/export/purge have explicit integration hooks.
CODES = Table(
    "billing_referral_codes", METADATA,
    Column("user_id", String(36), primary_key=True),
    Column("code", String(32), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
REWARDS = Table(
    "billing_referral_rewards", METADATA,
    Column("id", String(36), primary_key=True),
    Column("invitee_user_id", String(36), nullable=False, unique=True),
    Column("inviter_user_id", String(36), nullable=False, index=True),
    Column("order_reference", String(64), nullable=True, unique=True),
    Column("status", String(24), nullable=False),
    Column("reward_choice", String(16), nullable=True),
    Column("policy_version", String(32), nullable=False),
    Column("reservation_month", String(7), nullable=True),
    Column("inviter_minutes", Integer, nullable=False),
    Column("invitee_minutes", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("qualified_at", DateTime(timezone=True), nullable=True),
    Column("pending_until", DateTime(timezone=True), nullable=True),
    Column("released_at", DateTime(timezone=True), nullable=True),
    Column("reconciliation_reference", String(120), nullable=True),
)
# Keep the original unique invitee row as permanent attribution and the first
# reward record. Later paid subscriptions have their own immutable decisions.
RENEWAL_REWARDS = Table(
    "billing_referral_renewal_rewards", METADATA,
    Column("id", String(36), primary_key=True),
    Column("referral_id", String(36), nullable=False, index=True),
    Column("invitee_user_id", String(36), nullable=False, index=True),
    Column("inviter_user_id", String(36), nullable=False, index=True),
    Column("order_reference", String(64), nullable=False, unique=True),
    Column("status", String(24), nullable=False),
    Column("reward_choice", String(16), nullable=True),
    Column("policy_version", String(32), nullable=False),
    Column("reservation_month", String(7), nullable=False),
    # Excluded same-month orders keep their own row, with no second slot.
    # A blocked or capped decision retains its slot, preventing replay reuse.
    Column("slot_month", String(7), nullable=True),
    Column("inviter_minutes", Integer, nullable=False),
    Column("invitee_minutes", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("qualified_at", DateTime(timezone=True), nullable=False),
    Column("pending_until", DateTime(timezone=True), nullable=False),
    Column("released_at", DateTime(timezone=True), nullable=True),
    Column("reconciliation_reference", String(120), nullable=True),
    UniqueConstraint("invitee_user_id", "slot_month", name="uq_referral_renewal_invitee_month"),
)
COUPONS = Table(
    "billing_referral_coupons", METADATA,
    Column("code", String(32), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("reward_id", String(36), nullable=False, unique=True),
    Column("status", String(16), nullable=False),
    Column("percent", Integer, nullable=False),
    Column("max_discount_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("order_reference", String(64), nullable=True, unique=True),
    Column("discount_minor", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)
REWARD_PREFERENCES = Table(
    "billing_referral_reward_preferences", METADATA,
    Column("reward_id", String(36), primary_key=True),
    Column("coupon_currency", String(3), nullable=False),
)


class ReferralError(billing.BillingError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def campaign_start() -> datetime | None:
    value = os.getenv("LECTURESIFT_REFERRAL_CAMPAIGN_START_AT", "").strip()
    try:
        start = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return start.astimezone(timezone.utc) if start.tzinfo is not None else None
    except ValueError:
        return None


def enabled() -> bool:
    start = campaign_start()
    return (SCHEMA_RECOVERY_RELEASE_READY
            and os.getenv("LECTURESIFT_REFERRALS_ENABLED", "").strip().lower() == "true"
            and start is not None and start <= _utc(billing.utcnow()))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _month(now: datetime) -> str:
    return _utc(now).strftime("%Y-%m")


def _table_shape_matches(inspector, table) -> bool:
    if not inspector.has_table(table.name):
        return False
    columns = {column["name"]: column for column in inspector.get_columns(table.name)}
    if set(columns) != set(table.c.keys()):
        return False
    for expected in table.c:
        actual = columns[expected.name]
        if bool(actual["nullable"]) != bool(expected.nullable):
            return False
        if actual["type"]._type_affinity is not expected.type._type_affinity:
            return False
        if isinstance(expected.type, String) and actual["type"].length != expected.type.length:
            return False
    expected_pk = {column.name for column in table.primary_key.columns}
    if set(inspector.get_pk_constraint(table.name)["constrained_columns"]) != expected_pk:
        return False
    uniques = {frozenset(item["column_names"]) for item in inspector.get_unique_constraints(table.name)}
    expected_uniques = {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    }
    return uniques == expected_uniques


def _schema_exists(connection) -> bool:
    inspector = inspect(connection)
    return all(_table_shape_matches(inspector, table) for table in METADATA.sorted_tables)


def _lifecycle_reward_tables(connection) -> tuple:
    """Keep legacy close/export/purge available without allowing v1 activation."""
    inspector = inspect(connection)
    tables = (CODES, REWARDS, COUPONS)
    if not any(inspector.has_table(table.name) for table in METADATA.sorted_tables):
        return ()
    if not all(_table_shape_matches(inspector, table) for table in tables):
        raise ReferralError("LS-REF-SCHEMA", "Davet kayıtlarının şeması doğrulanamadı.")
    if not inspector.has_table(RENEWAL_REWARDS.name):
        if inspector.has_table(REWARD_PREFERENCES.name):
            raise ReferralError("LS-REF-SCHEMA", "Davet tercihi şeması eksik bir sürüme bağlı.")
        return (REWARDS,)
    if not _table_shape_matches(inspector, RENEWAL_REWARDS):
        raise ReferralError("LS-REF-SCHEMA", "Yenileme ödülü şeması doğrulanamadı.")
    if inspector.has_table(REWARD_PREFERENCES.name) and not _table_shape_matches(inspector, REWARD_PREFERENCES):
        raise ReferralError("LS-REF-SCHEMA", "Davet tercihi şeması doğrulanamadı.")
    return (REWARDS, RENEWAL_REWARDS)


def _reward_table(reward_id: str):
    if re.fullmatch(r"rr-[0-9a-f]{32}", reward_id or ""):
        return RENEWAL_REWARDS
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", reward_id or ""):
        return REWARDS
    raise ReferralError("LS-REF-NOT-FOUND", "Davet ödülü bulunamadı.")


def _reward_terms(reward):
    terms = POLICY_TERMS.get(reward.policy_version)
    expected = (RENEWAL_TERMS,) if _reward_table(reward.id) is RENEWAL_REWARDS else (
        FIRST_PURCHASE_TERMS, REGIONAL_FIRST_PURCHASE_TERMS,
    )
    if (terms not in expected or reward.inviter_minutes != terms.inviter_minutes
            or reward.invitee_minutes != terms.invitee_minutes):
        raise ReferralError("LS-REF-CHOICE", "Ödül kural sürümü doğrulanamadı.")
    return terms


def _coupon_terms(connection, reward):
    _reward_terms(reward)
    preference = connection.execute(select(REWARD_PREFERENCES.c.coupon_currency).where(
        REWARD_PREFERENCES.c.reward_id == reward.id,
    )).scalar_one_or_none()
    terms = coupon_terms_for_policy(reward.policy_version, preference or "TRY")
    if terms is None:
        raise ReferralError("LS-REF-COUPON", "Ödülün kupon para birimi doğrulanamadı.")
    return terms


def _monthly_reserved_count(connection, inviter_id: str, month: str) -> int:
    # Once allocated, even a subsequently blocked reward retains its place.
    return sum(int(connection.execute(select(func.count()).select_from(table).where(
        table.c.inviter_user_id == inviter_id, table.c.reservation_month == month,
        table.c.status.in_(("pending", "released", "blocked")),
    )).scalar_one()) for table in (REWARDS, RENEWAL_REWARDS))


def redemption_currencies() -> list[str]:
    """Configured checkout currencies only; never contacts a payment provider."""
    from . import payments
    currencies = {"TRY"} if billing.bank_transfer_available() else set()
    if billing.commerce_identity()["configured"]:
        try:
            provider = payments.preferred_card_provider()
            status = payments.iyzico_public_status() if provider == "iyzico" else payments.paytr_public_status()
            usable = status.get("configured") and status.get("status") == "active"
            if provider == "iyzico":
                usable = usable and payments._iyzico_base_url() == "https://api.iyzipay.com"
                usable = usable and billing.config.PUBLIC_BASE_URL.startswith("https://")
            if usable:
                currencies.update(status.get("currencies", ()))
        except billing.BillingConfigurationError:
            pass
    return [currency for currency in REGIONAL_COUPON_CAPS_MINOR if currency in currencies]


def _require(connection) -> None:
    if os.getenv("LECTURESIFT_REFERRALS_ENABLED", "").strip().lower() != "true":
        raise ReferralError("LS-REF-DISABLED", "Davet programı henüz etkin değil.")
    if not SCHEMA_RECOVERY_RELEASE_READY or not _schema_exists(connection):
        raise ReferralError("LS-REF-SCHEMA", "Davet programının güvenli kurulumu tamamlanmadı.")


def _verified(connection, user_id: str) -> bool:
    row = connection.execute(
        select(billing.USERS.c.email, billing.USER_PROFILES.c.email_verified_at)
        .join(billing.USER_PROFILES, billing.USER_PROFILES.c.user_id == billing.USERS.c.id)
        .where(billing.USERS.c.id == user_id)
    ).first()
    return bool(row and row.email_verified_at and not row.email.endswith("@users.invalid"))


def _lock_users(connection, *user_ids: str) -> None:
    ids = sorted(set(user_ids))
    rows = connection.execute(
        select(billing.USERS.c.id).where(billing.USERS.c.id.in_(ids))
        .order_by(billing.USERS.c.id).with_for_update()
    ).scalars().all()
    if set(rows) != set(ids):
        raise ReferralError("LS-REF-ACCOUNT", "Davet hesabı kullanılamıyor.")
    # PostgreSQL locks above serialize reservations/releases. SQLite needs a
    # write lock too; this changes no balance and keeps local tests equivalent.
    if connection.dialect.name == "sqlite":
        connection.execute(update(billing.USERS).where(billing.USERS.c.id == ids[0])
                           .values(credit_minutes=billing.USERS.c.credit_minutes))


def create_code(user_id: str) -> dict:
    with billing.ENGINE.begin() as connection:
        _require(connection)
        _lock_users(connection, user_id)
        if not _verified(connection, user_id):
            raise ReferralError("LS-REF-VERIFY", "Davet için doğrulanmış bir hesap gerekiyor.")
        row = connection.execute(select(CODES).where(CODES.c.user_id == user_id)).first()
        if not row:
            connection.execute(CODES.insert().values(
                user_id=user_id, code="LSR-" + secrets.token_hex(12).upper(), created_at=billing.utcnow(),
            ))
    return summary(user_id)


def attach_at_registration(connection, user_id: str, email: str, code: str, now: datetime) -> str:
    """Called only inside the new-user insertion transaction, never by a route."""
    if not enabled():
        return "disabled"
    selected = (code or "").strip().upper()
    if not selected:
        return "not_provided"
    _require(connection)
    if not CODE_RE.fullmatch(selected) or not selected.startswith("LSR-"):
        return "invalid"
    new_user = connection.execute(select(billing.USERS.c.email, billing.USERS.c.created_at).where(
        billing.USERS.c.id == user_id,
    )).first()
    if (not new_user or new_user.email.casefold() != email.casefold()
            or _utc(new_user.created_at) != _utc(now)):
        return "invalid"
    inviter = connection.execute(select(CODES.c.user_id).where(CODES.c.code == selected)).scalar_one_or_none()
    if not inviter or inviter == user_id or not _verified(connection, inviter):
        return "invalid"
    inviter_email = connection.execute(select(billing.USERS.c.email).where(billing.USERS.c.id == inviter)).scalar_one()
    if inviter_email.casefold() == email.casefold():
        return "invalid"
    connection.execute(REWARDS.insert().values(
        id=str(uuid.uuid4()), invitee_user_id=user_id, inviter_user_id=inviter,
        status="invited", policy_version=POLICY_VERSION,
        inviter_minutes=INVITER_MINUTES, invitee_minutes=INVITEE_MINUTES, created_at=now,
    ))
    return "accepted"


def _paid_source(connection, reference: str):
    payment = connection.execute(select(billing.PAYMENT_ORDERS).where(
        billing.PAYMENT_ORDERS.c.reference == reference)).first()
    manual = connection.execute(select(billing.MANUAL_ORDERS).where(
        billing.MANUAL_ORDERS.c.reference == reference)).first()
    if bool(payment) == bool(manual):
        return None
    order = payment or manual
    if order.status != "paid" or int(order.amount_minor) <= 0:
        return None
    if payment and int(payment.provider_amount_minor or 0) != int(payment.amount_minor):
        return None
    return order


def _first_subscription_payment(connection, user_id: str, reference: str) -> bool:
    paid = []
    for table in (billing.PAYMENT_ORDERS, billing.MANUAL_ORDERS):
        paid.extend(connection.execute(select(table.c.reference, table.c.updated_at).where(
            table.c.user_id == user_id, table.c.status == "paid",
            table.c.plan_code.in_(ELIGIBLE_PLANS), table.c.interval.in_(("monthly", "annual")),
        )).all())
    return bool(paid and min(paid, key=lambda row: (_utc(row.updated_at), row.reference)).reference == reference)


def _blocked(connection, reward) -> bool:
    if not _verified(connection, reward.inviter_user_id) or not _verified(connection, reward.invitee_user_id):
        return True
    order = _paid_source(connection, reward.order_reference)
    if not order or order.user_id != reward.invitee_user_id:
        return True
    subscription = connection.execute(select(billing.SUBSCRIPTIONS).where(
        billing.SUBSCRIPTIONS.c.source_reference == reward.order_reference,
        billing.SUBSCRIPTIONS.c.user_id == reward.invitee_user_id,
    )).first()
    if not subscription:
        return True
    if _reward_table(reward.id) is REWARDS:
        # Preserve the original first-purchase conditions exactly.
        if subscription.status != "active" or _utc(subscription.ends_at) <= _utc(billing.utcnow()):
            return True
    elif subscription.status not in {"active", "replaced", "expired"}:
        # Ordinary renewal/replacement or time passing does not erase a real
        # settled renewal payment. Explicit cancellation remains conservative.
        return True
    if _reward_table(reward.id) is RENEWAL_REWARDS and not connection.execute(select(REWARDS.c.id).where(
        REWARDS.c.id == reward.referral_id, REWARDS.c.inviter_user_id == reward.inviter_user_id,
        REWARDS.c.invitee_user_id == reward.invitee_user_id,
    )).first():
        return True
    from .rollout_service import REFUND_REQUESTS
    refund = connection.execute(select(REFUND_REQUESTS.c.id).where(
        REFUND_REQUESTS.c.order_reference == reward.order_reference,
        REFUND_REQUESTS.c.status != "rejected",
    )).first()
    return bool(refund)


def _qualify(reference: str) -> None:
    with billing.ENGINE.begin() as connection:
        _require(connection)
        order = _paid_source(connection, reference)
        if not order or order.plan_code not in ELIGIBLE_PLANS or order.interval not in {"monthly", "annual"}:
            return
        reward = connection.execute(select(REWARDS).where(REWARDS.c.invitee_user_id == order.user_id)).first()
        if not reward:
            return
        _lock_users(connection, reward.inviter_user_id, reward.invitee_user_id)
        reward = connection.execute(select(REWARDS).where(REWARDS.c.id == reward.id).with_for_update()).one()
        # Payment state/account verification may change while waiting for the
        # user locks. Both qualification paths use only the re-read source.
        order = _paid_source(connection, reference)
        if not order or order.user_id != reward.invitee_user_id:
            return
        if reward.order_reference == reference or connection.execute(select(RENEWAL_REWARDS.c.id).where(
            RENEWAL_REWARDS.c.order_reference == reference,
        )).first():
            return
        if (_utc(order.created_at) < max(_utc(reward.created_at), campaign_start())
                or not _verified(connection, order.user_id)):
            return
        verified_at = connection.execute(select(billing.USER_PROFILES.c.email_verified_at).where(
            billing.USER_PROFILES.c.user_id == order.user_id,
        )).scalar_one()
        if _utc(verified_at) > _utc(order.updated_at):
            return
        first_purchase = not reward.order_reference and _first_subscription_payment(connection, order.user_id, reference)
        source_kind = "payment_order" if "provider" in order._mapping else "manual_order"
        terms = policy_for_purchase(
            plan_code=order.plan_code, interval=order.interval, payment_source=source_kind,
            amount_minor=int(order.amount_minor), mode="first" if first_purchase else "renewal",
        )
        if terms is None:
            return
        now = billing.utcnow()
        month = _month(now)
        reserved = _monthly_reserved_count(connection, reward.inviter_user_id, month)
        status = "pending" if reserved < MONTHLY_CAP else "cap_reached"
        if first_purchase:
            if reward.status != "invited":
                return
            terms = _reward_terms(reward)
            connection.execute(update(REWARDS).where(REWARDS.c.id == reward.id).values(
                order_reference=reference, status=status, reservation_month=month,
                qualified_at=now, pending_until=now + timedelta(days=terms.hold_days),
            ))
            return
        if not _verified(connection, reward.inviter_user_id):
            return
        previous_slot = connection.execute(select(RENEWAL_REWARDS.c.id).where(
            RENEWAL_REWARDS.c.invitee_user_id == order.user_id,
            RENEWAL_REWARDS.c.slot_month == month,
        )).first()
        # Record exclusions too: replaying this paid order next month cannot
        # recycle a monthly exclusion or a capped reservation into a reward.
        connection.execute(RENEWAL_REWARDS.insert().values(
            id="rr-" + uuid.uuid4().hex, referral_id=reward.id,
            invitee_user_id=reward.invitee_user_id, inviter_user_id=reward.inviter_user_id,
            order_reference=reference, status="monthly_limit" if previous_slot else status,
            policy_version=terms.version, reservation_month=month,
            slot_month=None if previous_slot else month,
            inviter_minutes=terms.inviter_minutes, invitee_minutes=terms.invitee_minutes,
            created_at=now, qualified_at=now, pending_until=now + timedelta(days=terms.hold_days),
        ))


def after_order_change(reference: str) -> None:
    """Best effort, AFTER the payment transaction commits; never breaks payment."""
    if not enabled():
        return
    try:
        _coupon_order_changed(reference)
        _qualify(reference)
    except Exception:
        # No order details, PII, provider body or credentials enter this log.
        LOGGER.warning("Referral reconciliation deferred; operator review required.")


def choose_reward(user_id: str, reward_id: str, choice: str, currency: str | None = None) -> dict:
    if choice not in {"minutes", "coupon"}:
        raise ReferralError("LS-REF-CHOICE", "Bonus dakika veya kupon seç.")
    with billing.ENGINE.begin() as connection:
        _require(connection)
        _lock_users(connection, user_id)
        table = _reward_table(reward_id)
        reward = connection.execute(select(table).where(
            table.c.id == reward_id, table.c.inviter_user_id == user_id,
        ).with_for_update()).first()
        if not reward or reward.status != "pending":
            raise ReferralError("LS-REF-STATE", "Bu ödül için seçim yapılamıyor.")
        _reward_terms(reward)
        existing = connection.execute(select(REWARD_PREFERENCES.c.coupon_currency).where(
            REWARD_PREFERENCES.c.reward_id == reward.id,
        )).scalar_one_or_none()
        selected_currency = (existing or "TRY") if currency is None else currency
        if not isinstance(selected_currency, str):
            raise ReferralError("LS-REF-COUPON", "Desteklenen bir kupon para birimi seç.")
        selected_currency = selected_currency.strip().upper()
        if coupon_terms_for_policy(reward.policy_version, selected_currency) is None:
            raise ReferralError("LS-REF-COUPON", "Bu ödül için kupon para birimi desteklenmiyor.")
        if choice == "coupon" and selected_currency not in redemption_currencies():
            raise ReferralError("LS-REF-COUPON", "Bu para birimiyle kupon kullanımı henüz açık değil; dakika seçebilirsin.")
        if existing is None:
            connection.execute(REWARD_PREFERENCES.insert().values(
                reward_id=reward.id, coupon_currency=selected_currency,
            ))
        else:
            connection.execute(update(REWARD_PREFERENCES).where(REWARD_PREFERENCES.c.reward_id == reward.id)
                               .values(coupon_currency=selected_currency))
        connection.execute(update(table).where(table.c.id == reward.id).values(reward_choice=choice))
    return summary(user_id)


def release_reward(reward_id: str, *, provider_reconciled: bool, evidence_reference: str) -> dict:
    evidence = (evidence_reference or "").strip()
    if provider_reconciled is not True or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]{7,119}", evidence):
        raise ReferralError("LS-REF-RECONCILIATION", "Sağlayıcı mutabakatı ve gizli veri içermeyen kanıt referansı gerekiyor.")
    now = billing.utcnow()
    blocked = False
    with billing.ENGINE.begin() as connection:
        _require(connection)
        table = _reward_table(reward_id)
        reward = connection.execute(select(table).where(table.c.id == reward_id)).first()
        if not reward:
            raise ReferralError("LS-REF-NOT-FOUND", "Davet ödülü bulunamadı.")
        _lock_users(connection, reward.inviter_user_id, reward.invitee_user_id)
        reward = connection.execute(select(table).where(table.c.id == reward_id).with_for_update()).one()
        if reward.status == "released":
            return {"id": reward.id, "status": "released"}
        if reward.status != "pending" or not reward.pending_until or _utc(reward.pending_until) > _utc(now):
            raise ReferralError("LS-REF-HOLD", "Ödül bekleme süresi veya uygunluk koşulları tamamlanmadı.")
        terms = _coupon_terms(connection, reward)
        if reward.reward_choice not in {"minutes", "coupon"}:
            raise ReferralError("LS-REF-CHOICE", "Ödül seçimi veya kural sürümü doğrulanamadı.")
        if reward.reward_choice == "coupon" and terms.currency not in redemption_currencies():
            raise ReferralError("LS-REF-COUPON", "Kuponun para birimi için ödeme kanalı henüz açık değil.")
        blocked = _blocked(connection, reward)
        if blocked:
            connection.execute(update(table).where(table.c.id == reward.id).values(status="blocked"))
        else:
            if terms.invitee_minutes:
                connection.execute(update(billing.USERS).where(billing.USERS.c.id == reward.invitee_user_id)
                                   .values(credit_minutes=billing.USERS.c.credit_minutes + terms.invitee_minutes))
            if reward.reward_choice == "minutes":
                connection.execute(update(billing.USERS).where(billing.USERS.c.id == reward.inviter_user_id)
                                   .values(credit_minutes=billing.USERS.c.credit_minutes + reward.inviter_minutes))
            else:
                connection.execute(COUPONS.insert().values(
                    code="LSC-" + secrets.token_hex(12).upper(), user_id=reward.inviter_user_id,
                    reward_id=reward.id, status="ready", percent=terms.coupon_percent,
                    max_discount_minor=terms.coupon_max_minor, currency=terms.currency,
                    created_at=now, expires_at=now + timedelta(days=terms.coupon_valid_days),
                ))
            connection.execute(update(table).where(table.c.id == reward.id).values(
                status="released", released_at=now, reconciliation_reference=evidence,
            ))
    return {"id": reward_id, "status": "blocked" if blocked else "released"}


def reserve_coupon(connection, *, user_id: str, code: str, reference: str,
                   plan_code: str, interval: str, currency: str, amount_minor: int) -> int:
    """Reserve once in the SAME transaction as the server-priced order/terms."""
    _require(connection)
    selected = (code or "").strip().upper()
    if (plan_code not in ELIGIBLE_PLANS or interval != "monthly"
            or not CODE_RE.fullmatch(selected) or not selected.startswith("LSC-")):
        raise ReferralError("LS-REF-COUPON", "Kupon yalnız düzenlendiği para birimiyle aylık abonelikte kullanılabilir.")
    # Read IDs first, then use the same USERS -> ledger/coupon lock order as
    # reward release, refund creation and account closure.
    candidate = connection.execute(select(COUPONS).where(
        COUPONS.c.code == selected, COUPONS.c.user_id == user_id,
    )).first()
    origin_table = _reward_table(candidate.reward_id) if candidate else None
    origin = connection.execute(select(origin_table).where(
        origin_table.c.id == candidate.reward_id,
    )).first() if candidate else None
    if not origin:
        raise ReferralError("LS-REF-COUPON", "Kupon kullanılamıyor.")
    _lock_users(connection, origin.inviter_user_id, origin.invitee_user_id)
    coupon = connection.execute(select(COUPONS).where(
        COUPONS.c.code == selected, COUPONS.c.user_id == user_id,
    ).with_for_update()).first()
    origin = connection.execute(select(origin_table).where(origin_table.c.id == candidate.reward_id)).first()
    if not origin:
        raise ReferralError("LS-REF-COUPON", "Kuponun dayandığı ödül bulunamadı.")
    _reward_terms(origin)
    terms = coupon_terms_for_policy(origin.policy_version, coupon.currency) if coupon else None
    if (not coupon or coupon.status != "ready" or _utc(coupon.expires_at) <= _utc(billing.utcnow())
            or coupon.reward_id != origin.id or origin.inviter_user_id != user_id
            or terms is None or currency != coupon.currency
            or coupon.percent != terms.coupon_percent or coupon.max_discount_minor != terms.coupon_max_minor
            or coupon.currency != terms.currency or not _verified(connection, user_id)):
        raise ReferralError("LS-REF-COUPON", "Kupon kullanılamıyor veya süresi dolmuş.")
    from .rollout_service import REFUND_REQUESTS
    if (not origin or origin.status != "released" or not _verified(connection, origin.invitee_user_id)
            or connection.execute(
        select(REFUND_REQUESTS.c.id).where(
            REFUND_REQUESTS.c.order_reference == origin.order_reference,
            REFUND_REQUESTS.c.status != "rejected",
        )
    ).first()):
        raise ReferralError("LS-REF-COUPON", "Kuponun dayandığı ödül veya iade durumu doğrulanamadı.")
    discount = min(int(amount_minor) * coupon.percent // 100, coupon.max_discount_minor)
    if discount <= 0 or discount >= amount_minor:
        raise ReferralError("LS-REF-COUPON", "Kupon tutarı doğrulanamadı.")
    changed = connection.execute(update(COUPONS).where(
        COUPONS.c.code == selected, COUPONS.c.status == "ready",
    ).values(status="reserved", order_reference=reference, discount_minor=discount))
    if changed.rowcount != 1:
        raise ReferralError("LS-REF-COUPON", "Kupon başka bir ödeme için ayrılmış.")
    return int(amount_minor) - discount


def _coupon_order_changed(reference: str) -> None:
    with billing.ENGINE.begin() as connection:
        _require(connection)
        coupon = connection.execute(select(COUPONS).where(
            COUPONS.c.order_reference == reference, COUPONS.c.status == "reserved",
        ).with_for_update()).first()
        if not coupon:
            return
        order = connection.execute(select(billing.PAYMENT_ORDERS).where(
            billing.PAYMENT_ORDERS.c.reference == reference)).first()
        if not order:
            order = connection.execute(select(billing.MANUAL_ORDERS).where(
                billing.MANUAL_ORDERS.c.reference == reference)).first()
        if order and order.status == "paid":
            connection.execute(update(COUPONS).where(COUPONS.c.code == coupon.code)
                               .values(status="used", used_at=billing.utcnow()))
        elif order and order.status in {"failed", "token_failed", "cancelled", "rejected"}:
            connection.execute(update(COUPONS).where(COUPONS.c.code == coupon.code)
                               .values(status="ready", order_reference=None, discount_minor=None))


def _preference_map(connection, reward_ids) -> dict:
    if not reward_ids or not inspect(connection).has_table(REWARD_PREFERENCES.name):
        return {}
    return dict(connection.execute(select(
        REWARD_PREFERENCES.c.reward_id, REWARD_PREFERENCES.c.coupon_currency,
    ).where(REWARD_PREFERENCES.c.reward_id.in_(reward_ids))).all())


def _public_reward_terms(row, selected_currency: str | None = None) -> dict:
    _reward_terms(row)
    terms = coupon_terms_for_policy(row.policy_version, selected_currency or "TRY")
    if terms is None:
        raise ReferralError("LS-REF-COUPON", "Ödülün kupon para birimi doğrulanamadı.")
    return {
        "kind": "renewal" if _reward_table(row.id) is RENEWAL_REWARDS else "first_purchase",
        "policy_version": terms.version,
        "inviter_minutes": terms.inviter_minutes, "invitee_minutes": terms.invitee_minutes,
        "coupon_percent": terms.coupon_percent, "coupon_max_discount_minor": terms.coupon_max_minor,
        "coupon_currency": terms.currency, "coupon_currency_selected": selected_currency is not None,
    }


def _public_coupon_policies() -> dict:
    result = {}
    for version in POLICY_TERMS:
        result[version] = {}
        for currency in REGIONAL_COUPON_CAPS_MINOR:
            terms = coupon_terms_for_policy(version, currency)
            if terms is not None:
                result[version][currency] = {
                    "percent": terms.coupon_percent, "max_discount_minor": terms.coupon_max_minor,
                    "currency": terms.currency, "valid_days": terms.coupon_valid_days, "monthly_only": True,
                }
    return result


def summary(user_id: str) -> dict:
    now = billing.utcnow()
    result = {
        "enabled": enabled(), "referral_code": None, "referral_url": None,
        "reward_minutes": INVITER_MINUTES, "invitee_reward_minutes": INVITEE_MINUTES,
        "monthly_invitation_cap": MONTHLY_CAP, "month_utc": _month(now),
        "monthly_reward_cap": MONTHLY_CAP,
        "coupon_policies": _public_coupon_policies(),
        "redemption_currencies": redemption_currencies(),
        "monthly_reserved_count": 0, "monthly_remaining_count": MONTHLY_CAP,
        "earned_minutes": 0, "pending_minutes": 0, "hold_days": HOLD_DAYS,
        "settlement": "admin_reconciliation_after_14_days",
        "coupon": {"percent": COUPON_PERCENT, "max_discount_minor": COUPON_MAX_MINOR,
                   "currency": "TRY", "valid_days": COUPON_DAYS, "monthly_only": True},
        "renewal": {
            "reward_minutes": RENEWAL_TERMS.inviter_minutes,
            "invitee_reward_minutes": RENEWAL_TERMS.invitee_minutes,
            "monthly_per_invitee_cap": 1,
            "coupon": {"percent": RENEWAL_TERMS.coupon_percent,
                       "max_discount_minor": RENEWAL_TERMS.coupon_max_minor,
                       "currency": RENEWAL_TERMS.currency,
                       "valid_days": RENEWAL_TERMS.coupon_valid_days, "monthly_only": True},
        },
        "rewards": [], "coupons": [],
        "history_limit": 50, "has_more_rewards": False, "has_more_coupons": False,
    }
    if not enabled():
        return result
    with billing.ENGINE.connect() as connection:
        _require(connection)
        code = connection.execute(select(CODES.c.code).where(CODES.c.user_id == user_id)).scalar_one_or_none()
        rows = []
        for table in (REWARDS, RENEWAL_REWARDS):
            belongs_to_user = or_(table.c.inviter_user_id == user_id, table.c.invitee_user_id == user_id)
            credited_minutes = case(
                (table.c.inviter_user_id == user_id,
                 case((table.c.reward_choice == "minutes", table.c.inviter_minutes), else_=0)),
                else_=table.c.invitee_minutes,
            )
            totals = connection.execute(select(
                func.coalesce(func.sum(case((table.c.status == "released", credited_minutes), else_=0)), 0),
                func.coalesce(func.sum(case((table.c.status == "pending", credited_minutes), else_=0)), 0),
            ).where(belongs_to_user)).one()
            result["earned_minutes"] += int(totals[0])
            result["pending_minutes"] += int(totals[1])
            rows.extend(connection.execute(select(table).where(belongs_to_user)
                        .order_by(table.c.created_at.desc(), table.c.id).limit(51)).all())
        result["monthly_reserved_count"] = _monthly_reserved_count(connection, user_id, _month(now))
        rows.sort(key=lambda row: (_utc(row.created_at), row.id), reverse=True)
        preferences = _preference_map(connection, [row.id for row in rows[:50]])
        coupons = connection.execute(select(COUPONS).where(COUPONS.c.user_id == user_id)
                                     .order_by(COUPONS.c.created_at.desc(), COUPONS.c.code).limit(51)).all()
        result["has_more_rewards"] = len(rows) > 50
        result["has_more_coupons"] = len(coupons) > 50
        for row in rows[:50]:
            inviter = row.inviter_user_id == user_id
            result["rewards"].append({
                "id": row.id, "role": "inviter" if inviter else "invitee", "status": row.status,
                "reward_choice": row.reward_choice,
                "pending_until": _utc(row.pending_until).isoformat() if row.pending_until else None,
                "created_at": _utc(row.created_at).isoformat(),
                **_public_reward_terms(row, preferences.get(row.id)),
            })
        result["coupons"] = [
            {"code": row.code, "status": "expired" if row.status == "ready" and _utc(row.expires_at) <= _utc(now) else row.status,
             "expires_at": _utc(row.expires_at).isoformat(), "percent": row.percent,
             "max_discount_minor": row.max_discount_minor, "currency": row.currency}
            for row in coupons[:50]
        ]
    result["monthly_remaining_count"] = max(0, MONTHLY_CAP - result["monthly_reserved_count"])
    result["referral_code"] = code
    if code:
        result["referral_url"] = billing.config.FRONTEND_BASE_URL.rstrip("/") + "/register.html?ref=" + code
    return result


def admin_pending() -> list[dict]:
    with billing.ENGINE.connect() as connection:
        _require(connection)
        rows = []
        for table in (REWARDS, RENEWAL_REWARDS):
            rows.extend(connection.execute(select(table).where(table.c.status == "pending")
                        .order_by(table.c.qualified_at, table.c.id).limit(100)).all())
        rows.sort(key=lambda row: (_utc(row.qualified_at), row.id))
        preferences = _preference_map(connection, [row.id for row in rows[:100]])
    return [{"id": row.id, "order_reference": row.order_reference, "reward_choice": row.reward_choice,
             "pending_until": _utc(row.pending_until).isoformat(),
             **_public_reward_terms(row, preferences.get(row.id))} for row in rows[:100]]


def reconcile_order(reference: str) -> dict:
    """Trusted repair for a deferred post-payment hook; grants no spendable reward."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", reference):
        raise ReferralError("LS-REF-ORDER", "Geçersiz sipariş referansı.")
    with billing.ENGINE.connect() as connection:
        _require(connection)
        matches = sum(connection.execute(select(func.count()).select_from(table).where(
            table.c.reference == reference,
        )).scalar_one() for table in (billing.PAYMENT_ORDERS, billing.MANUAL_ORDERS))
        if matches != 1:
            raise ReferralError("LS-REF-ORDER", "Mutabakata uygun tekil sipariş bulunamadı.")
    _coupon_order_changed(reference)
    _qualify(reference)
    return {"order_reference": reference, "reconciled": True}


def export_data(user_id: str) -> dict:
    with billing.ENGINE.connect() as connection:
        tables = _lifecycle_reward_tables(connection)
        if not tables:
            return {"rewards": [], "coupons": []}
        rows = []
        for table in tables:
            rows.extend(connection.execute(select(table).where(or_(
                table.c.inviter_user_id == user_id, table.c.invitee_user_id == user_id,
            ))).all())
        coupons = connection.execute(select(COUPONS).where(COUPONS.c.user_id == user_id)).all()
        preferences = _preference_map(connection, [row.id for row in rows])
    # No other user's identifier, email or purchase reference is exported.
    return {
        "rewards": [{"id": row.id, "role": "inviter" if row.inviter_user_id == user_id else "invitee",
                     "status": row.status, "reward_choice": row.reward_choice,
                     **_public_reward_terms(row, preferences.get(row.id)),
                     "reservation_month": row.reservation_month} for row in rows],
        "coupons": [{"code": row.code, "status": row.status,
                     "currency": row.currency, "percent": row.percent,
                     "max_discount_minor": row.max_discount_minor,
                     "expires_at": _utc(row.expires_at).isoformat()} for row in coupons],
    }


def purge_rehearsal(connection, user_ids: list[str]) -> None:
    """Used only after the caller proves its isolated DB and synthetic users."""
    tables = _lifecycle_reward_tables(connection)
    if not tables:
        return
    ids = set(user_ids)
    rows = []
    for table in tables:
        rows.extend(connection.execute(select(table).where(or_(
            table.c.inviter_user_id.in_(ids), table.c.invitee_user_id.in_(ids),
        ))).all())
    if any(row.inviter_user_id not in ids or row.invitee_user_id not in ids for row in rows):
        raise ReferralError("LS-REF-PURGE", "Davet kaydı prova dışındaki bir hesaba bağlı.")
    reward_ids = {row.id for row in rows}
    first_ids = {row.id for row in rows if _reward_table(row.id) is REWARDS}
    if any(_reward_table(row.id) is RENEWAL_REWARDS and row.referral_id not in first_ids for row in rows):
        raise ReferralError("LS-REF-PURGE", "Yenileme kaydı prova dışındaki bir davete bağlı.")
    coupons = connection.execute(select(COUPONS).where(or_(
        COUPONS.c.user_id.in_(ids), COUPONS.c.reward_id.in_(reward_ids),
    ))).all()
    if any(row.user_id not in ids or row.reward_id not in reward_ids for row in coupons):
        raise ReferralError("LS-REF-PURGE", "Kupon prova dışındaki bir ödüle veya hesaba bağlı.")
    connection.execute(COUPONS.delete().where(COUPONS.c.user_id.in_(ids)))
    if inspect(connection).has_table(REWARD_PREFERENCES.name):
        connection.execute(REWARD_PREFERENCES.delete().where(REWARD_PREFERENCES.c.reward_id.in_(reward_ids)))
    for table in reversed(tables):
        connection.execute(table.delete().where(table.c.id.in_(reward_ids)))
    connection.execute(CODES.delete().where(CODES.c.user_id.in_(ids)))


def close_account(connection, user_id: str) -> None:
    # Existing schema remains available for close/export even after disable.
    tables = _lifecycle_reward_tables(connection)
    if not tables:
        return
    _lock_users(connection, user_id)
    connection.execute(CODES.delete().where(CODES.c.user_id == user_id))
    for table in tables:
        # Choice changes lock the ledger before its preference. Match that
        # order when the *invitee* closes an account while the inviter chooses.
        pending_ids = connection.execute(select(table.c.id).where(
            or_(table.c.inviter_user_id == user_id, table.c.invitee_user_id == user_id),
            table.c.status.in_(("invited", "pending")),
        ).order_by(table.c.id).with_for_update()).scalars().all()
        connection.execute(update(table).where(table.c.id.in_(pending_ids)).values(status="blocked"))
        if inspect(connection).has_table(REWARD_PREFERENCES.name):
            connection.execute(REWARD_PREFERENCES.delete().where(REWARD_PREFERENCES.c.reward_id.in_(pending_ids)))
    connection.execute(update(COUPONS).where(
        COUPONS.c.user_id == user_id, COUPONS.c.status == "ready",
    ).values(status="void"))
