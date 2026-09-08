"""Owned, reserved credit ledger. No prompts, attachments or API keys are stored.

Schema creation is an explicit release operation, never a request side effect.
All wallet transitions lock the billing owner before reading or changing credit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, delete, inspect, select, update

from . import assistant_catalog as catalog
from . import billing_service as billing
from .errors import LectureSiftError

METADATA = MetaData()
GRANTS = Table(
    "assistant_credit_grants_v1", METADATA,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("source_reference", String(64), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("credits", Integer, nullable=False),
    Column("remaining", Integer, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
REQUESTS = Table(
    "assistant_credit_requests_v1", METADATA,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("fingerprint", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("allocations_json", Text, nullable=False),
    Column("reserved", Integer, nullable=False),
    Column("charged", Integer, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("response_json", Text, nullable=True),
    Column("budget_day", String(10), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
BUDGET = Table(
    "assistant_daily_budget_v1", METADATA,
    Column("day", String(80), primary_key=True),
    Column("credits", Integer, nullable=False),
)
DAILY_CREDIT_CEILING = 100_000  # $20 model-cost ceiling at the reviewed rate.


def reserve_trial(identity):
    """Three short anonymous turns per day, with the same durable spend ceiling."""
    require_available()
    day = billing.utcnow().date().isoformat()
    key = "trial:" + day + ":" + identity[:48]
    with billing.ENGINE.begin() as connection:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        for item in (day, key):
            connection.execute(insert(BUDGET).values(day=item, credits=0).on_conflict_do_nothing())
        total = connection.execute(select(BUDGET).where(BUDGET.c.day == day).with_for_update()).first()
        trial = connection.execute(select(BUDGET).where(BUDGET.c.day == key).with_for_update()).first()
        if trial.credits >= 3:
            _fail("LS-ASSIST-08", 429)
        if total.credits + 10 > DAILY_CREDIT_CEILING:
            _fail("LS-ASSIST-05", 503)
        connection.execute(update(BUDGET).where(BUDGET.c.day == day).values(credits=BUDGET.c.credits + 10))
        connection.execute(update(BUDGET).where(BUDGET.c.day == key).values(credits=BUDGET.c.credits + 1))


def _fail(code: str, status: int = 409):
    raise LectureSiftError(code, "Asistan işlemi tamamlanamadı.", status_code=status)


def require_available():
    if not catalog.enabled():
        _fail("LS-ASSIST-01", 503)
    with billing.ENGINE.connect() as connection:
        if not all(inspect(connection).has_table(table.name) for table in METADATA.sorted_tables):
            _fail("LS-ASSIST-01", 503)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _id(*parts):
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _owner(connection, user_id):
    billing._lock_billing_user(connection, user_id)
    user = connection.execute(select(billing.USERS).where(billing.USERS.c.id == user_id)).first()
    profile = connection.execute(select(billing.USER_PROFILES).where(billing.USER_PROFILES.c.user_id == user_id)).first()
    if not user or not profile or not profile.email_verified_at or user.email.endswith("@users.invalid"):
        _fail("LS-ASSIST-02", 401)


def _expire_requests(connection, user_id, now):
    # Provider timeout is 45 seconds without retries. After two minutes a lost
    # worker cannot block the account forever; unknown provider cost stays in
    # the platform budget, while the user's reserved credits are restored once.
    rows = connection.execute(select(REQUESTS).where(
        REQUESTS.c.user_id == user_id, REQUESTS.c.state == "reserved",
        REQUESTS.c.created_at < now - timedelta(minutes=2),
    )).all()
    for row in rows:
        for grant_id, amount in json.loads(row.allocations_json):
            connection.execute(update(GRANTS).where(GRANTS.c.id == grant_id, GRANTS.c.user_id == user_id).values(remaining=GRANTS.c.remaining + amount))
        connection.execute(update(REQUESTS).where(REQUESTS.c.id == row.id).values(state="failed"))


def _grant(connection, user_id, reference, kind, credits, expiry, now, period=""):
    key = _id(user_id, reference, period)
    if credits and not connection.execute(select(GRANTS.c.id).where(GRANTS.c.id == key)).first():
        connection.execute(GRANTS.insert().values(
            id=key, user_id=user_id, source_reference=reference, kind=kind,
            credits=credits, remaining=credits, expires_at=expiry, created_at=now,
        ))


def _sync(connection, user_id, now):
    _expire_requests(connection, user_id, now)
    # A welcome allocation is lifetime-only, even after upgrading/downgrading.
    _grant(connection, user_id, "welcome", "welcome", catalog.INCLUDED["free"],
           now + timedelta(days=365), now)
    from .rollout_service import REFUND_REQUESTS
    blocked = set()
    if inspect(connection).has_table(REFUND_REQUESTS.name):
        blocked = set(connection.execute(select(REFUND_REQUESTS.c.order_reference).where(
            REFUND_REQUESTS.c.user_id == user_id, REFUND_REQUESTS.c.status != "rejected",
        )).scalars())
    subscription = billing._active_subscription(connection, user_id, now)
    active_ref = subscription.source_reference if subscription else ""
    # Replaced/expired subscriptions cannot leave spendable monthly allowances.
    connection.execute(update(GRANTS).where(
        GRANTS.c.user_id == user_id, GRANTS.c.kind == "subscription",
        GRANTS.c.source_reference != active_ref,
    ).values(remaining=0))
    if subscription and active_ref not in blocked:
        plan, _ = billing._plan_for_purchase_reference(
            connection, reference=active_ref, plan_code=subscription.plan_code,
            interval=subscription.interval,
        )
        start = billing._subscription_usage_period_start(subscription, now)
        expiry = min(_utc(subscription.ends_at), billing._shift_month(start, 1))
        _grant(connection, user_id, active_ref, "subscription", plan.assistant_credits,
               expiry, now, start.isoformat())
    for table in (billing.PAYMENT_ORDERS, billing.MANUAL_ORDERS):
        rows = connection.execute(select(table).where(
            table.c.user_id == user_id, table.c.plan_code.in_(tuple(catalog.PACKS)),
            table.c.status == "paid", table.c.updated_at > now - timedelta(days=365),
        )).all()
        for order in rows:
            if order.reference in blocked:
                continue
            plan, _ = billing._plan_for_purchase_reference(
                connection, reference=order.reference, plan_code=order.plan_code,
                interval=order.interval, source=order,
            )
            _grant(connection, user_id, order.reference, "topup", plan.assistant_credits,
                   _utc(order.updated_at) + timedelta(days=365), now)
    # Temporarily blocked refunds are excluded by _spendable; balances are not
    # destroyed until completion, so a rejected refund restores unused access.
    return blocked


def _spendable(connection, user_id, now, blocked):
    query = select(GRANTS).where(GRANTS.c.user_id == user_id, GRANTS.c.expires_at > now, GRANTS.c.remaining > 0)
    if blocked:
        query = query.where(GRANTS.c.source_reference.not_in(blocked))
    return connection.execute(query.order_by(GRANTS.c.expires_at, GRANTS.c.id)).all()


def status(user_id):
    require_available()
    now = billing.utcnow()
    with billing.ENGINE.begin() as connection:
        _owner(connection, user_id)
        blocked = _sync(connection, user_id, now)
        grants = _spendable(connection, user_id, now, blocked)
        # Only short-lived response caching; no server-side conversation history.
        connection.execute(update(REQUESTS).where(
            REQUESTS.c.user_id == user_id, REQUESTS.c.created_at < now - timedelta(minutes=15),
        ).values(response_json=None))
        return {"balance": sum(row.remaining for row in grants), "grants": [
            {"kind": row.kind, "remaining": row.remaining, "expires_at": _utc(row.expires_at).isoformat()}
            for row in grants
        ]}


def reserve(user_id, request_id, fingerprint, credits):
    require_available()
    now = billing.utcnow()
    key = _id(user_id, request_id)
    with billing.ENGINE.begin() as connection:
        _owner(connection, user_id)
        _expire_requests(connection, user_id, now)
        previous = connection.execute(select(REQUESTS).where(REQUESTS.c.id == key)).first()
        if previous:
            if previous.fingerprint != fingerprint:
                _fail("LS-ASSIST-04")
            if previous.state == "complete" and previous.response_json and _utc(previous.created_at) > now - timedelta(minutes=15):
                return key, json.loads(previous.response_json)
            _fail("LS-ASSIST-04")  # Same request never incurs a second provider call.
        # A single provider request per account at a time across API replicas.
        # Unknown/crashed requests keep their reservation pending reconciliation.
        if connection.execute(select(REQUESTS.c.id).where(
            REQUESTS.c.user_id == user_id, REQUESTS.c.state == "reserved",
        )).first():
            _fail("LS-ASSIST-04")
        blocked = _sync(connection, user_id, now)
        grants = _spendable(connection, user_id, now, blocked)
        if credits < 1 or credits > 256 or sum(row.remaining for row in grants) < credits:
            _fail("LS-ASSIST-03", 402)
        day = now.date().isoformat()
        # Insert-on-conflict is supported by both deployment PostgreSQL and CI SQLite.
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        connection.execute(insert(BUDGET).values(day=day, credits=0).on_conflict_do_nothing())
        budget = connection.execute(select(BUDGET).where(BUDGET.c.day == day).with_for_update()).first()
        if budget.credits + credits > DAILY_CREDIT_CEILING:
            _fail("LS-ASSIST-05", 503)
        connection.execute(update(BUDGET).where(BUDGET.c.day == day).values(credits=BUDGET.c.credits + credits))
        allocations, left = [], credits
        for grant in grants:
            amount = min(left, grant.remaining)
            connection.execute(update(GRANTS).where(GRANTS.c.id == grant.id).values(remaining=GRANTS.c.remaining - amount))
            allocations.append([grant.id, amount])
            left -= amount
            if not left:
                break
        connection.execute(REQUESTS.insert().values(
            id=key, user_id=user_id, fingerprint=fingerprint, state="reserved",
            allocations_json=json.dumps(allocations), reserved=credits, charged=0,
            input_tokens=0, output_tokens=0, response_json=None, budget_day=day, created_at=now,
        ))
    return key, None


def settle(user_id, key, *, input_tokens=0, output_tokens=0, response=None, unknown_cost=False):
    now = billing.utcnow()
    with billing.ENGINE.begin() as connection:
        _owner(connection, user_id)
        row = connection.execute(select(REQUESTS).where(REQUESTS.c.id == key, REQUESTS.c.user_id == user_id)).first()
        if not row or row.state != "reserved":
            _fail("LS-ASSIST-04")
        actual = max(1, (input_tokens + 6 * output_tokens + 999) // 1000) if response else 0
        # Customer is never charged for an unanswered request. Unknown provider
        # cost still counts at the full reservation against the platform ceiling.
        charge = min(row.reserved, actual)
        refund = row.reserved - charge
        for grant_id, amount in reversed(json.loads(row.allocations_json)):
            restored = min(refund, amount)
            connection.execute(update(GRANTS).where(GRANTS.c.id == grant_id, GRANTS.c.user_id == user_id).values(remaining=GRANTS.c.remaining + restored))
            refund -= restored
        if actual > row.reserved:
            unknown_cost = True  # Estimate drift closes the budget for this day.
            connection.execute(update(BUDGET).where(BUDGET.c.day == row.budget_day).values(credits=DAILY_CREDIT_CEILING))
        elif not unknown_cost:
            connection.execute(update(BUDGET).where(BUDGET.c.day == row.budget_day).values(credits=BUDGET.c.credits - (row.reserved - actual)))
        if response is not None:
            response["charged_credits"] = charge
        connection.execute(update(REQUESTS).where(REQUESTS.c.id == key).values(
            state="complete" if response else "failed", charged=charge,
            input_tokens=input_tokens, output_tokens=output_tokens,
            response_json=json.dumps(response, ensure_ascii=False) if response else None,
        ))
    return response


def export_data(user_id):
    with billing.ENGINE.connect() as connection:
        result = {}
        for table in (GRANTS, REQUESTS):
            if not inspect(connection).has_table(table.name):
                result[table.name] = []
                continue
            result[table.name] = [
                {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items() if key != "response_json"}
                for row in connection.execute(select(table).where(table.c.user_id == user_id)).mappings()
            ]
        return result


def close_account(connection, user_id):
    for table in (REQUESTS, GRANTS):
        if inspect(connection).has_table(table.name):
            connection.execute(delete(table).where(table.c.user_id == user_id))
