from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, inspect, select, update
from sqlalchemy.pool import StaticPool

from lecturesift import billing_service as billing, referrals, rollout_service
from lecturesift.app import app


@pytest.fixture
def state(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    referrals.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout_service, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(referrals, "SCHEMA_RECOVERY_RELEASE_READY", True)
    monkeypatch.setattr(referrals, "redemption_currencies", lambda: list(referrals.REGIONAL_COUPON_CAPS_MINOR))
    monkeypatch.setenv("LECTURESIFT_REFERRALS_ENABLED", "true")
    monkeypatch.setenv("LECTURESIFT_REFERRAL_CAMPAIGN_START_AT", "2026-09-08T00:00:00Z")
    clock = {"now": datetime(2026, 9, 8, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(billing, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(rollout_service, "utcnow", lambda: clock["now"])
    yield clock
    engine.dispose()


def user(*, code="", verified=True):
    result = billing.register_user(f"ref-{uuid.uuid4().hex}@example.com", "Safe-test-password1", "Referral", "Test",
                                   referral_code=code)
    if verified:
        with billing.ENGINE.begin() as connection:
            connection.execute(update(billing.USER_PROFILES).where(
                billing.USER_PROFILES.c.user_id == result["user"]["id"],
            ).values(email_verified_at=billing.utcnow()))
    return result["user"]["id"]


def pair():
    inviter = user()
    code = referrals.create_code(inviter)["referral_code"]
    return inviter, user(code=code)


def pay(user_id, plan="lite", *, interval="monthly", coupon=""):
    order = billing.create_payment_order(user_id, "test-provider", plan, interval, "TRY", coupon_code=coupon)
    billing.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"])
    return order


def reward(invitee):
    with billing.ENGINE.connect() as connection:
        return connection.execute(select(referrals.REWARDS).where(
            referrals.REWARDS.c.invitee_user_id == invitee,
        )).one()


def credit(user_id):
    with billing.ENGINE.connect() as connection:
        return connection.execute(select(billing.USERS.c.credit_minutes).where(billing.USERS.c.id == user_id)).scalar_one()


@pytest.mark.parametrize('start', ['', 'invalid', '2026-09-08T00:00:00', '2026-09-09T00:00:00Z'])
def test_campaign_needs_an_explicit_reached_timezone_boundary(state, monkeypatch, start):
    monkeypatch.setenv('LECTURESIFT_REFERRAL_CAMPAIGN_START_AT', start)
    assert referrals.enabled() is False


def test_old_pending_purchase_does_not_gain_a_retroactive_campaign_reward(state, monkeypatch):
    inviter, invitee = pair()
    old = billing.create_payment_order(invitee, 'test-provider', 'lite', 'monthly', 'TRY')
    monkeypatch.setenv('LECTURESIFT_REFERRAL_CAMPAIGN_START_AT', '2026-09-08T13:00:00Z')
    state['now'] += timedelta(hours=2)
    billing.complete_payment_order(old['reference'], succeeded=True, provider_amount_minor=old['amount_minor'])
    assert reward(invitee).order_reference is None
    assert credit(inviter) == 0


def released(state, choice):
    inviter, invitee = pair()
    source = pay(invitee)
    row = reward(invitee)
    referrals.choose_reward(inviter, row.id, choice)
    state["now"] += timedelta(days=14)
    result = referrals.release_reward(row.id, provider_reconciled=True, evidence_reference="provider-review-123")
    assert result["status"] == "released"
    return inviter, invitee, source, row.id


def test_disabled_and_unapproved_schema_never_create_tables_or_break_payment(state, monkeypatch):
    referrals.METADATA.drop_all(billing.ENGINE)
    monkeypatch.setattr(referrals, "SCHEMA_RECOVERY_RELEASE_READY", False)
    assert not (set(referrals.METADATA.tables) & set(billing.METADATA.tables))
    invitee = user(code="LSR-" + "A" * 24)
    pay(invitee)
    assert billing.account_status(invitee)["plan"]["code"] == "lite"
    result = referrals.summary(invitee)
    assert result["enabled"] is False and result["referral_code"] is None
    assert set(referrals.METADATA.tables).isdisjoint(inspect(billing.ENGINE).get_table_names())
    with pytest.raises(referrals.ReferralError, match="kurulumu"):
        referrals.create_code(invitee)
    # Even manually created tables and an environment opt-in cannot bypass
    # the source-controlled schema/recovery release capability.
    referrals.METADATA.create_all(billing.ENGINE)
    with pytest.raises(referrals.ReferralError, match="kurulumu"):
        referrals.create_code(invitee)
    spec = importlib.util.spec_from_file_location("referral_migration", Path(__file__).parents[1] / "deploy/migrate_referrals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(RuntimeError, match="blocked"):
        module.migrate(confirm=True)


def test_api_disabled_auth_and_admin_guards(state, monkeypatch):
    inviter = user()
    token = billing.issue_session(inviter, "unused@example.com")
    headers = {"Authorization": "Bearer " + token}
    monkeypatch.setattr(billing.config, "ADMIN_ADMIN", "private-admin-test")
    monkeypatch.setattr(referrals, "SCHEMA_RECOVERY_RELEASE_READY", False)
    client = TestClient(app)
    assert client.get("/billing/referrals").status_code == 401
    assert client.get("/billing/referrals", headers=headers).json()["referrals"]["enabled"] is False
    assert client.get("/billing/me", headers=headers).status_code == 200
    assert client.post("/billing/referrals/code", headers=headers).status_code == 503
    assert client.get("/billing/admin/referrals", headers=headers).status_code == 401
    assert client.post("/billing/admin/referrals/reconcile-order", headers=headers,
                       json={"order_reference": "LS123"}).status_code == 401


def test_registration_only_and_code_idempotency(state):
    inviter = user()
    first = referrals.create_code(inviter)
    assert first["referral_code"] == referrals.create_code(inviter)["referral_code"]
    assert referrals.CODE_RE.fullmatch(first["referral_code"])
    invitee = user(code=first["referral_code"])
    assert reward(invitee).inviter_user_id == inviter
    bad = user(code="not-a-code")
    with billing.ENGINE.connect() as connection:
        assert connection.execute(select(referrals.REWARDS).where(referrals.REWARDS.c.invitee_user_id == bad)).first() is None
    with billing.ENGINE.begin() as connection:
        assert referrals.attach_at_registration(connection, inviter, "same@example.com",
                                                first["referral_code"], billing.utcnow()) == "invalid"
    assert not any(getattr(route, "path", "") in {"/billing/referrals/attach", "/billing/referrals/claim"} for route in app.routes)


@pytest.mark.parametrize("plan", ["test", "credit"])
def test_test_and_topup_do_not_qualify_but_first_subscription_does(state, plan):
    inviter, invitee = pair()
    pay(invitee, plan, interval="one_time")
    assert reward(invitee).status == "invited"
    pay(invitee)
    assert reward(invitee).status == "pending"
    assert credit(inviter) == 0


def test_unverified_failed_pending_and_admin_grants_do_not_qualify(state):
    inviter = user()
    invitee = user(code=referrals.create_code(inviter)["referral_code"], verified=False)
    order = billing.create_payment_order(invitee, "test-provider", "lite", "monthly", "TRY")
    referrals.after_order_change(order["reference"])
    assert reward(invitee).status == "invited"
    billing.complete_payment_order(order["reference"], succeeded=False, provider_amount_minor=0)
    assert reward(invitee).status == "invited"
    pay(invitee)
    assert reward(invitee).status == "invited"


def test_first_payment_retry_is_immutable_and_new_payment_has_own_renewal(state):
    inviter, invitee = pair()
    order = pay(invitee)
    billing.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"])
    renewal_order = pay(invitee, "plus")
    assert reward(invitee).order_reference == order["reference"]
    with billing.ENGINE.connect() as connection:
        renewal = connection.execute(select(referrals.RENEWAL_REWARDS).where(
            referrals.RENEWAL_REWARDS.c.order_reference == renewal_order["reference"],
        )).one()
    assert renewal.policy_version == referrals.RENEWAL_TERMS.version
    assert referrals.summary(inviter)["monthly_reserved_count"] == 2
    assert credit(inviter) == credit(invitee) == 0


def test_monthly_cap_reserves_both_sides_and_uses_utc_month(state):
    inviter = user()
    code = referrals.create_code(inviter)["referral_code"]
    for index in range(6):
        invitee = user(code=code)
        pay(invitee)
        assert reward(invitee).status == ("pending" if index < 5 else "cap_reached")
        assert credit(invitee) == 0
    assert referrals.summary(inviter)["monthly_remaining_count"] == 0
    state["now"] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    invitee = user(code=code)
    pay(invitee)
    assert reward(invitee).status == "pending"
    assert reward(invitee).reservation_month == "2026-10"
    assert referrals.summary(inviter)["monthly_reserved_count"] == 1


def test_hold_choice_confirmation_and_release_are_atomic_idempotent(state):
    inviter, invitee = pair()
    pay(invitee)
    row = reward(invitee)
    with pytest.raises(referrals.ReferralError):
        referrals.choose_reward(invitee, row.id, "minutes")
    referrals.choose_reward(inviter, row.id, "minutes")
    with pytest.raises(referrals.ReferralError):
        referrals.release_reward(row.id, provider_reconciled=True, evidence_reference="proof-123")
    state["now"] += timedelta(days=14)
    with pytest.raises(referrals.ReferralError):
        referrals.release_reward(row.id, provider_reconciled=False, evidence_reference="proof-123")
    referrals.release_reward(row.id, provider_reconciled=True, evidence_reference="proof-123")
    referrals.release_reward(row.id, provider_reconciled=True, evidence_reference="proof-123")
    assert credit(inviter) == 60 and credit(invitee) == 30
    assert referrals.summary(inviter)["coupons"] == []
    with pytest.raises(referrals.ReferralError):
        referrals.choose_reward(inviter, row.id, "coupon")


@pytest.mark.parametrize("blocker", ["refund", "cancel", "closed"])
def test_blocking_states_prevent_release(state, blocker):
    inviter, invitee = pair()
    order = pay(invitee)
    row = reward(invitee)
    referrals.choose_reward(inviter, row.id, "minutes")
    if blocker == "refund":
        rollout_service.create_refund_request(invitee, order["reference"], "A sufficiently detailed refund reason")
    elif blocker == "cancel":
        billing.cancel_active_subscription(invitee)
    else:
        with billing.ENGINE.begin() as connection:
            connection.execute(update(billing.USER_PROFILES).where(
                billing.USER_PROFILES.c.user_id == invitee).values(email_verified_at=None))
    state["now"] += timedelta(days=14)
    assert referrals.release_reward(row.id, provider_reconciled=True, evidence_reference="proof-123")["status"] == "blocked"
    assert credit(inviter) == credit(invitee) == 0


def test_coupon_choice_next_purchase_server_price_and_single_use(state):
    inviter, invitee, _, _ = released(state, "coupon")
    assert credit(inviter) == 0 and credit(invitee) == 30
    coupon = referrals.summary(inviter)["coupons"][0]
    assert coupon["currency"] == "TRY" and coupon["max_discount_minor"] == 5000
    order = billing.create_payment_order(inviter, "test-provider", "plus", "monthly", "TRY", coupon["code"])
    assert order["amount_minor"] == 59900 - 5000
    with billing.ENGINE.connect() as connection:
        terms = connection.execute(select(billing.PURCHASE_TERMS.c.plan_json).where(
            billing.PURCHASE_TERMS.c.reference == order["reference"])).scalar_one()
        assert json.loads(terms)["purchase"]["amount_minor"] == order["amount_minor"]
    with pytest.raises(referrals.ReferralError):
        billing.create_payment_order(inviter, "test-provider", "lite", "monthly", "TRY", coupon["code"])
    billing.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"])
    assert referrals.summary(inviter)["coupons"][0]["status"] == "used"


@pytest.mark.parametrize("plan,interval,currency", [
    ("lite", "annual", "TRY"), ("credit", "one_time", "TRY"), ("test", "one_time", "TRY"),
    ("lite", "monthly", "USD"),
])
def test_coupon_rejects_annual_topup_test_and_other_currencies(state, plan, interval, currency):
    inviter, _, _, _ = released(state, "coupon")
    code = referrals.summary(inviter)["coupons"][0]["code"]
    with pytest.raises(referrals.ReferralError):
        billing.create_payment_order(inviter, "test-provider", plan, interval, currency, code)
    assert referrals.summary(inviter)["coupons"][0]["status"] == "ready"


def test_coupon_owner_expiry_refund_and_failed_checkout(state):
    inviter, _, source, _ = released(state, "coupon")
    code = referrals.summary(inviter)["coupons"][0]["code"]
    with pytest.raises(referrals.ReferralError):
        billing.create_payment_order(user(), "test-provider", "lite", "monthly", "TRY", code)
    order = billing.create_payment_order(inviter, "test-provider", "lite", "monthly", "TRY", code)
    assert order["amount_minor"] == 26910
    billing.mark_payment_order_token_failed(order["reference"])
    assert referrals.summary(inviter)["coupons"][0]["status"] == "ready"
    state["now"] += timedelta(days=90)
    with pytest.raises(referrals.ReferralError):
        billing.create_payment_order(inviter, "test-provider", "lite", "monthly", "TRY", code)
    assert referrals.summary(inviter)["coupons"][0]["status"] == "expired"


def test_rejected_manual_coupon_order_cannot_be_resurrected(state, monkeypatch):
    inviter, _, _, _ = released(state, "coupon")
    code = referrals.summary(inviter)["coupons"][0]["code"]
    monkeypatch.setattr(billing, "bank_transfer_available", lambda: True)
    order = billing.create_manual_order(inviter, "lite", "monthly", code)
    billing.reject_manual_order(order["reference"])
    assert referrals.summary(inviter)["coupons"][0]["status"] == "ready"
    second = billing.create_manual_order(inviter, "lite", "monthly", code)
    with pytest.raises(billing.BillingError):
        billing.approve_manual_order(order["reference"])
    billing.approve_manual_order(second["reference"])
    billing.approve_manual_order(second["reference"])
    assert referrals.summary(inviter)["coupons"][0]["status"] == "used"


def test_referral_failure_never_breaks_payment_and_admin_can_repair(state, monkeypatch):
    inviter, invitee = pair()
    qualify = referrals._qualify
    monkeypatch.setattr(referrals, "_qualify", lambda _ref: (_ for _ in ()).throw(RuntimeError("injected")))
    order = pay(invitee)
    assert billing.account_status(invitee)["plan"]["code"] == "lite"
    assert reward(invitee).status == "invited"
    monkeypatch.setattr(referrals, "_qualify", qualify)
    assert referrals.reconcile_order(order["reference"])["reconciled"] is True
    assert reward(invitee).status == "pending"
    assert credit(inviter) == credit(invitee) == 0


def test_export_close_and_proof_bound_purge_do_not_leak_other_user(state):
    inviter, invitee = pair()
    pay(invitee)
    exported = referrals.export_data(inviter)
    assert invitee not in json.dumps(exported)
    with billing.ENGINE.begin() as connection:
        with pytest.raises(referrals.ReferralError):
            referrals.purge_rehearsal(connection, [inviter])
        referrals.close_account(connection, inviter)
    assert reward(invitee).status == "blocked"
    with billing.ENGINE.begin() as connection:
        referrals.purge_rehearsal(connection, [inviter, invitee])
    assert referrals.export_data(inviter) == {"rewards": [], "coupons": []}


def test_admin_repair_releases_a_deferred_failed_coupon_reservation(state, monkeypatch):
    inviter, _, _, _ = released(state, "coupon")
    code = referrals.summary(inviter)["coupons"][0]["code"]
    order = billing.create_payment_order(inviter, "test-provider", "lite", "monthly", "TRY", code)
    original = referrals._coupon_order_changed
    monkeypatch.setattr(referrals, "_coupon_order_changed", lambda _ref: (_ for _ in ()).throw(RuntimeError("injected")))
    billing.mark_payment_order_token_failed(order["reference"])
    assert referrals.summary(inviter)["coupons"][0]["status"] == "reserved"
    monkeypatch.setattr(referrals, "_coupon_order_changed", original)
    referrals.reconcile_order(order["reference"])
    assert referrals.summary(inviter)["coupons"][0]["status"] == "ready"


def test_refund_after_release_blocks_unused_coupon_redemption(state):
    inviter, invitee, order, _ = released(state, "coupon")
    code = referrals.summary(inviter)["coupons"][0]["code"]
    rollout_service.create_refund_request(invitee, order["reference"], "Request a refund after the reward review")
    with pytest.raises(referrals.ReferralError):
        billing.create_payment_order(inviter, "test-provider", "lite", "monthly", "TRY", code)


def test_existing_account_cannot_be_bound_later_even_through_internal_helper(state):
    inviter = user()
    invitee = user()
    code = referrals.create_code(inviter)["referral_code"]
    state["now"] += timedelta(seconds=1)
    with billing.ENGINE.begin() as connection:
        email = connection.execute(select(billing.USERS.c.email).where(billing.USERS.c.id == invitee)).scalar_one()
        assert referrals.attach_at_registration(connection, invitee, email, code, billing.utcnow()) == "invalid"


def test_summary_bounds_history_but_aggregates_lifetime_totals(state):
    inviter = user()
    with billing.ENGINE.begin() as connection:
        connection.execute(referrals.REWARDS.insert(), [
            {"id": str(uuid.uuid4()), "invitee_user_id": str(uuid.uuid4()), "inviter_user_id": inviter,
             "status": "released", "reward_choice": "minutes", "policy_version": referrals.POLICY_VERSION,
             "reservation_month": "2026-08", "inviter_minutes": 60, "invitee_minutes": 30,
             "created_at": billing.utcnow()} for _ in range(55)
        ])
    result = referrals.summary(inviter)
    assert len(result["rewards"]) == 50
    assert result["has_more_rewards"] is True
    assert result["earned_minutes"] == 55 * 60
    assert result["monthly_reserved_count"] == 0


def test_credit_release_rolls_back_both_users_on_ledger_failure(state, monkeypatch):
    from sqlalchemy import event
    inviter, invitee = pair()
    pay(invitee)
    row = reward(invitee)
    referrals.choose_reward(inviter, row.id, "minutes")
    state["now"] += timedelta(days=14)

    def fail_final_update(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("UPDATE billing_referral_rewards") and "released_at" in statement:
            raise RuntimeError("injected final ledger failure")

    event.listen(billing.ENGINE, "before_cursor_execute", fail_final_update)
    try:
        with pytest.raises(RuntimeError):
            referrals.release_reward(row.id, provider_reconciled=True, evidence_reference="proof-123")
    finally:
        event.remove(billing.ENGINE, "before_cursor_execute", fail_final_update)
    assert credit(inviter) == credit(invitee) == 0
    assert reward(invitee).status == "pending"


def test_coupon_reservation_rolls_back_if_order_creation_fails(state):
    from sqlalchemy import event
    inviter, _, _, _ = released(state, "coupon")
    code = referrals.summary(inviter)["coupons"][0]["code"]

    def fail_order(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO billing_payment_orders"):
            raise RuntimeError("injected order failure")

    event.listen(billing.ENGINE, "before_cursor_execute", fail_order)
    try:
        with pytest.raises(RuntimeError):
            billing.create_payment_order(inviter, "test-provider", "lite", "monthly", "TRY", code)
    finally:
        event.remove(billing.ENGINE, "before_cursor_execute", fail_order)
    assert referrals.summary(inviter)["coupons"][0]["status"] == "ready"


def renewal_for(reference):
    with billing.ENGINE.connect() as connection:
        return connection.execute(select(referrals.RENEWAL_REWARDS).where(
            referrals.RENEWAL_REWARDS.c.order_reference == reference,
        )).one()


def test_renewal_minutes_reward_only_inviter_and_release_is_idempotent(state):
    inviter, invitee, _, first_id = released(state, "minutes")
    source = pay(invitee, "plus")
    renewal = renewal_for(source["reference"])
    assert renewal.id.startswith("rr-") and len(renewal.id) == 35
    assert renewal.referral_id == first_id
    assert (renewal.inviter_minutes, renewal.invitee_minutes) == (30, 0)
    referrals.choose_reward(inviter, renewal.id, "minutes")
    pending = {row["id"]: row for row in referrals.admin_pending()}
    assert pending[renewal.id]["kind"] == "renewal"
    assert pending[renewal.id]["coupon_percent"] == 5
    assert pending[renewal.id]["coupon_max_discount_minor"] == 2500
    with pytest.raises(referrals.ReferralError):
        referrals.release_reward(renewal.id, provider_reconciled=True, evidence_reference="renewal-proof-123")
    state["now"] += timedelta(days=14)
    referrals.release_reward(renewal.id, provider_reconciled=True, evidence_reference="renewal-proof-123")
    referrals.release_reward(renewal.id, provider_reconciled=True, evidence_reference="renewal-proof-123")
    billing.complete_payment_order(source["reference"], succeeded=True, provider_amount_minor=source["amount_minor"])
    assert credit(inviter) == 90 and credit(invitee) == 30
    summary = referrals.summary(inviter)
    assert summary["earned_minutes"] == 90
    assert summary["renewal"]["monthly_per_invitee_cap"] == 1
    assert {row["kind"] for row in summary["rewards"]} == {"first_purchase", "renewal"}
    assert referrals.summary(invitee)["earned_minutes"] == 30


def test_renewal_and_first_coupons_keep_their_distinct_terms(state):
    inviter, invitee, _, first_id = released(state, "coupon")
    source = pay(invitee)
    renewal = renewal_for(source["reference"])
    referrals.choose_reward(inviter, renewal.id, "coupon")
    state["now"] += timedelta(days=14)
    referrals.release_reward(renewal.id, provider_reconciled=True, evidence_reference="renewal-proof-123")
    with billing.ENGINE.connect() as connection:
        coupons = {row.reward_id: row for row in connection.execute(select(referrals.COUPONS)).all()}
    first_coupon, renewal_coupon = coupons[first_id], coupons[renewal.id]
    assert (first_coupon.percent, first_coupon.max_discount_minor) == (10, 5000)
    assert (renewal_coupon.percent, renewal_coupon.max_discount_minor) == (5, 2500)
    assert credit(inviter) == 0 and credit(invitee) == 30
    first_purchase = billing.create_payment_order(
        inviter, "test-provider", "plus", "monthly", "TRY", first_coupon.code,
    )
    renewal_purchase = billing.create_payment_order(
        inviter, "test-provider", "plus", "monthly", "TRY", renewal_coupon.code,
    )
    assert first_purchase["amount_minor"] == 59900 - 5000
    assert renewal_purchase["amount_minor"] == 59900 - 2500
    with pytest.raises(referrals.ReferralError):
        billing.create_payment_order(inviter, "test-provider", "plus", "monthly", "TRY", renewal_coupon.code)


def test_first_and_renewal_share_cap_and_excluded_orders_never_recycle(state):
    inviter = user()
    code = referrals.create_code(inviter)["referral_code"]
    invitees = [user(code=code) for _ in range(5)]
    for invitee in invitees[:4]:
        pay(invitee)
    renewal_source = pay(invitees[0], "plus")
    assert renewal_for(renewal_source["reference"]).status == "pending"
    first_capped = pay(invitees[4])
    assert reward(invitees[4]).status == "cap_reached"
    renewal_capped = pay(invitees[1], "pro")
    assert renewal_for(renewal_capped["reference"]).status == "cap_reached"
    monthly_excluded = pay(invitees[0], "max")
    assert renewal_for(monthly_excluded["reference"]).status == "monthly_limit"
    assert referrals.summary(inviter)["monthly_reserved_count"] == 5

    state["now"] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for order in (first_capped, renewal_capped, monthly_excluded):
        referrals.reconcile_order(order["reference"])
    assert reward(invitees[4]).status == "cap_reached"
    assert renewal_for(renewal_capped["reference"]).status == "cap_reached"
    assert renewal_for(monthly_excluded["reference"]).status == "monthly_limit"
    assert referrals.summary(inviter)["monthly_reserved_count"] == 0
    next_order = pay(invitees[0])
    assert renewal_for(next_order["reference"]).status == "pending"
    assert referrals.summary(inviter)["monthly_reserved_count"] == 1


def test_annual_quota_reset_topups_and_admin_grants_do_not_create_renewal(state):
    inviter, invitee = pair()
    annual = pay(invitee, interval="annual")
    state["now"] += timedelta(days=31)
    billing.account_status(invitee)
    referrals.reconcile_order(annual["reference"])
    pay(invitee, "credit", interval="one_time")
    pay(invitee, "test", interval="one_time")
    rollout_service.admin_set_user_subscription(
        invitee, plan_code="pro", interval="monthly", duration_days=30, actor="synthetic-admin",
    )
    with billing.ENGINE.connect() as connection:
        assert connection.execute(select(referrals.RENEWAL_REWARDS)).all() == []
    state["now"] = datetime(2027, 9, 8, 12, tzinfo=timezone.utc)
    next_annual = pay(invitee, interval="annual")
    assert renewal_for(next_annual["reference"]).status == "pending"
    referrals.reconcile_order(next_annual["reference"])
    with billing.ENGINE.connect() as connection:
        assert len(connection.execute(select(referrals.RENEWAL_REWARDS)).all()) == 1


def test_genuine_renewal_survives_replacement_and_expiration(state):
    inviter, invitee, _, _ = released(state, "minutes")
    source = pay(invitee)
    renewal = renewal_for(source["reference"])
    referrals.choose_reward(inviter, renewal.id, "minutes")
    pay(invitee, "plus")  # Same-month plan switch cannot earn a second renewal.
    state["now"] += timedelta(days=31)
    assert referrals.release_reward(
        renewal.id, provider_reconciled=True, evidence_reference="renewal-proof-123",
    )["status"] == "released"
    assert credit(inviter) == 90 and credit(invitee) == 30


@pytest.mark.parametrize("blocker", ["refund", "cancel", "closed"])
def test_renewal_release_blocks_refund_cancellation_or_closed_account(state, blocker):
    state["now"] = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    inviter, invitee, _, _ = released(state, "minutes")
    source = pay(invitee)
    renewal = renewal_for(source["reference"])
    referrals.choose_reward(inviter, renewal.id, "minutes")
    if blocker == "refund":
        rollout_service.create_refund_request(invitee, source["reference"], "Synthetic renewal refund review")
    elif blocker == "cancel":
        billing.cancel_active_subscription(invitee)
    else:
        with billing.ENGINE.begin() as connection:
            connection.execute(update(billing.USER_PROFILES).where(
                billing.USER_PROFILES.c.user_id == invitee,
            ).values(email_verified_at=None))
    state["now"] += timedelta(days=14)
    assert referrals.release_reward(
        renewal.id, provider_reconciled=True, evidence_reference="renewal-proof-123",
    )["status"] == "blocked"
    assert credit(inviter) == 60 and credit(invitee) == 30
    assert renewal_for(source["reference"]).slot_month == "2026-09"
    assert referrals.summary(inviter)["monthly_reserved_count"] == 2


def test_renewal_export_close_and_purge_cover_both_reward_tables(state):
    inviter, invitee, _, _ = released(state, "minutes")
    source = pay(invitee)
    renewal = renewal_for(source["reference"])
    exported = referrals.export_data(inviter)
    assert {row["kind"] for row in exported["rewards"]} == {"first_purchase", "renewal"}
    assert invitee not in json.dumps(exported)
    assert source["reference"] not in json.dumps(exported)
    with billing.ENGINE.begin() as connection:
        with pytest.raises(referrals.ReferralError):
            referrals.purge_rehearsal(connection, [inviter])
        referrals.close_account(connection, inviter)
    assert renewal_for(source["reference"]).status == "blocked"
    with billing.ENGINE.begin() as connection:
        referrals.purge_rehearsal(connection, [inviter, invitee])
    assert referrals.export_data(inviter) == {"rewards": [], "coupons": []}


def test_legacy_schema_remains_exportable_and_closable_but_cannot_activate_renewals(state):
    inviter, invitee = pair()
    pay(invitee)
    referrals.REWARD_PREFERENCES.drop(billing.ENGINE)
    referrals.RENEWAL_REWARDS.drop(billing.ENGINE)
    with pytest.raises(referrals.ReferralError, match="kurulumu"):
        referrals.create_code(inviter)
    assert len(referrals.export_data(inviter)["rewards"]) == 1
    with billing.ENGINE.begin() as connection:
        referrals.close_account(connection, inviter)
    assert reward(invitee).status == "blocked"
    with billing.ENGINE.begin() as connection:
        referrals.purge_rehearsal(connection, [inviter, invitee])
    assert referrals.export_data(inviter) == {"rewards": [], "coupons": []}


def test_wrong_renewal_table_shape_fails_closed_for_activation_and_lifecycle(state):
    inviter, _ = pair()
    referrals.RENEWAL_REWARDS.drop(billing.ENGINE)
    with billing.ENGINE.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE billing_referral_renewal_rewards (id TEXT PRIMARY KEY)")
    with pytest.raises(referrals.ReferralError):
        referrals.create_code(inviter)
    with pytest.raises(referrals.ReferralError):
        referrals.export_data(inviter)


def test_selected_coupon_currency_is_persisted_and_must_match_checkout(state):
    inviter, invitee = pair()
    pay(invitee)
    first = reward(invitee)
    assert first.policy_version == referrals.REGIONAL_FIRST_PURCHASE_TERMS.version
    before = referrals.summary(inviter)
    assert before["rewards"][0]["coupon_currency_selected"] is False
    selected = referrals.choose_reward(inviter, first.id, "coupon", currency="EUR")
    row = selected["rewards"][0]
    terms = referrals.coupon_terms_for_policy(first.policy_version, "EUR")
    assert row["coupon_currency"] == "EUR" and row["coupon_currency_selected"] is True
    assert row["coupon_max_discount_minor"] == terms.coupon_max_minor
    assert "EUR" in selected["coupon_policies"][first.policy_version]
    state["now"] += timedelta(days=14)
    referrals.release_reward(first.id, provider_reconciled=True, evidence_reference="currency-proof-123")
    coupon = referrals.summary(inviter)["coupons"][0]
    assert coupon["currency"] == "EUR"
    assert coupon["max_discount_minor"] == terms.coupon_max_minor
    with pytest.raises(referrals.ReferralError):
        billing.create_payment_order(inviter, "test-provider", "plus", "monthly", "TRY", coupon["code"])
    order = billing.create_payment_order(inviter, "test-provider", "plus", "monthly", "EUR", coupon["code"])
    assert order["amount_minor"] == 1599 - min(1599 * terms.coupon_percent // 100, terms.coupon_max_minor)
    with pytest.raises(referrals.ReferralError):
        referrals.choose_reward(inviter, first.id, "coupon", currency="USD")


def test_historical_first_policy_retains_try_only_coupon(state):
    inviter, invitee = pair()
    with billing.ENGINE.begin() as connection:
        connection.execute(update(referrals.REWARDS).where(
            referrals.REWARDS.c.invitee_user_id == invitee,
        ).values(policy_version=referrals.FIRST_PURCHASE_TERMS.version))
    pay(invitee)
    first = reward(invitee)
    with pytest.raises(referrals.ReferralError):
        referrals.choose_reward(inviter, first.id, "coupon", currency="EUR")
    selected = referrals.choose_reward(inviter, first.id, "coupon")
    assert set(selected["coupon_policies"][first.policy_version]) == {"TRY"}
    state["now"] += timedelta(days=14)
    referrals.release_reward(first.id, provider_reconciled=True, evidence_reference="legacy-proof-123")
    coupon = referrals.summary(inviter)["coupons"][0]
    assert (coupon["currency"], coupon["percent"], coupon["max_discount_minor"]) == ("TRY", 10, 5000)


def test_renewal_currency_choice_survives_minute_choice_and_appears_in_export(state):
    inviter, invitee, _, _ = released(state, "minutes")
    source = pay(invitee)
    renewal = renewal_for(source["reference"])
    referrals.choose_reward(inviter, renewal.id, "coupon", currency="USD")
    referrals.choose_reward(inviter, renewal.id, "minutes")
    selected = referrals.choose_reward(inviter, renewal.id, "coupon")
    row = next(item for item in selected["rewards"] if item["id"] == renewal.id)
    assert (row["coupon_currency"], row["coupon_percent"], row["coupon_max_discount_minor"]) == ("USD", 5, 75)
    exported = next(item for item in referrals.export_data(inviter)["rewards"] if item["id"] == renewal.id)
    assert exported["coupon_currency"] == "USD"
    with billing.ENGINE.begin() as connection:
        referrals.close_account(connection, inviter)
        assert connection.execute(select(referrals.REWARD_PREFERENCES).where(
            referrals.REWARD_PREFERENCES.c.reward_id == renewal.id,
        )).first() is None


@pytest.mark.parametrize("currency", ["", "XYZ", "US", 123])
def test_invalid_coupon_currency_never_changes_pending_choice(state, currency):
    inviter, invitee = pair()
    pay(invitee)
    first = reward(invitee)
    with pytest.raises(referrals.ReferralError):
        referrals.choose_reward(inviter, first.id, "coupon", currency=currency)
    assert reward(invitee).reward_choice is None
    with billing.ENGINE.connect() as connection:
        assert connection.execute(select(referrals.REWARD_PREFERENCES)).all() == []


def test_unavailable_coupon_currency_keeps_minutes_available(state, monkeypatch):
    inviter, invitee = pair()
    pay(invitee)
    first = reward(invitee)
    monkeypatch.setattr(referrals, "redemption_currencies", lambda: [])
    with pytest.raises(referrals.ReferralError):
        referrals.choose_reward(inviter, first.id, "coupon", currency="EUR")
    assert referrals.choose_reward(inviter, first.id, "minutes", currency="EUR")["redemption_currencies"] == []
    state["now"] += timedelta(days=14)
    assert referrals.release_reward(
        first.id, provider_reconciled=True, evidence_reference="minutes-proof-123",
    )["status"] == "released"
    assert credit(inviter) == 60 and credit(invitee) == 30


def test_currency_channel_closing_blocks_new_coupon_release_without_revoking_issued_coupon(state, monkeypatch):
    inviter, invitee, _, _ = released(state, "coupon")
    issued = referrals.summary(inviter)["coupons"][0]
    source = pay(invitee)
    renewal = renewal_for(source["reference"])
    referrals.choose_reward(inviter, renewal.id, "coupon", currency="EUR")
    monkeypatch.setattr(referrals, "redemption_currencies", lambda: [])
    state["now"] += timedelta(days=14)
    with pytest.raises(referrals.ReferralError):
        referrals.release_reward(renewal.id, provider_reconciled=True, evidence_reference="renewal-proof-123")
    assert renewal_for(source["reference"]).status == "pending"
    # Issued coupon terms survive capability changes. The real checkout path
    # enforces provider availability before this server-priced reservation.
    purchase = billing.create_payment_order(inviter, "test-provider", "plus", "monthly", "TRY", issued["code"])
    assert purchase["amount_minor"] == 54900


@pytest.mark.parametrize("status, expected", [("active", ["USD", "EUR"]), ("test_mode", []), ("pending_credentials", [])])
def test_redemption_currencies_use_only_active_preferred_checkout(monkeypatch, status, expected):
    from lecturesift import payments
    monkeypatch.setattr(billing, "bank_transfer_available", lambda: False)
    monkeypatch.setattr(billing, "commerce_identity", lambda: {"configured": True})
    monkeypatch.setattr(payments, "preferred_card_provider", lambda: "paytr")
    monkeypatch.setattr(payments, "paytr_public_status", lambda: {
        "configured": status != "pending_credentials", "status": status, "currencies": ["USD", "EUR"],
    })
    assert referrals.redemption_currencies() == expected
