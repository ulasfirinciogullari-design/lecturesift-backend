"""Synthetic credit/ownership/provider tests. No paid model or media calls."""

import base64
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select, update
from sqlalchemy.pool import StaticPool

from lecturesift import assistant_catalog as catalog, assistant_wallet as wallet
from lecturesift import billing_service as billing, rollout_service, site_assistant as assistant
from lecturesift import app as app_module
from lecturesift.errors import LectureSiftError


@pytest.fixture
def state(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    wallet.METADATA.create_all(engine)
    rollout_service.REFUND_REQUESTS.create(engine, checkfirst=True)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout_service, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(catalog, "SCHEMA_RECOVERY_RELEASE_READY", True)
    monkeypatch.setenv("ASSISTANT_ENABLED", "true")
    clock = {"now": datetime(2026, 9, 8, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(billing, "utcnow", lambda: clock["now"])
    yield clock
    engine.dispose()


def user(verified=True):
    row = billing.register_user(f"assistant-{uuid.uuid4().hex}@example.com", "Safe-test-password1", "Assistant", "Test")
    user_id = row["user"]["id"]
    if verified:
        with billing.ENGINE.begin() as connection:
            connection.execute(update(billing.USER_PROFILES).where(billing.USER_PROFILES.c.user_id == user_id).values(email_verified_at=billing.utcnow()))
    return user_id


def paid(user_id, code, interval="one_time"):
    order = billing.create_payment_order(user_id, "test-provider", code, interval, "USD")
    billing.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"])
    return order


def test_capability_and_checkout_fail_closed_without_release_gate(state, monkeypatch):
    user_id = user()
    monkeypatch.setattr(catalog, "SCHEMA_RECOVERY_RELEASE_READY", False)
    assert catalog.offers("TRY")["available"] is False
    with pytest.raises(LectureSiftError):
        wallet.status(user_id)
    with pytest.raises(billing.BillingConfigurationError):
        paid(user_id, "ai_1000")


def test_missing_schema_fails_closed_without_request_time_ddl(state):
    wallet.GRANTS.drop(billing.ENGINE)
    with pytest.raises(LectureSiftError) as exc:
        wallet.require_available()
    assert exc.value.code == "LS-ASSIST-01"


def test_scheduled_pruning_clears_idle_private_data_without_altering_ledger(state, monkeypatch):
    user_id = user()
    key, _ = wallet.reserve(user_id, "old-answer", "old-hash", 20)
    wallet.settle(user_id, key, input_tokens=1000, response={"answer": "private answer"})
    wallet.reserve_trial("old-guest")
    state["now"] += timedelta(days=3)
    recent, _ = wallet.reserve(user_id, "recent-answer", "recent-hash", 20)
    wallet.settle(user_id, recent, input_tokens=1000, response={"answer": "recent answer"})
    wallet.reserve_trial("current-guest")
    monkeypatch.setenv("ASSISTANT_ENABLED", "false")
    result = wallet.prune_private_cache()
    assert result == {"available": True, "responses_cleared": 1, "trial_keys_removed": 1}
    with billing.ENGINE.connect() as connection:
        old = connection.execute(select(wallet.REQUESTS).where(wallet.REQUESTS.c.id == key)).one()
        assert old.response_json is None
        assert old.charged == 1 and old.state == "complete" and old.fingerprint == "old-hash"
        assert connection.execute(select(wallet.REQUESTS.c.response_json).where(wallet.REQUESTS.c.id == recent)).scalar_one()
        assert connection.execute(select(wallet.GRANTS.c.remaining)).scalar_one() == 48
        days = connection.execute(select(wallet.BUDGET.c.day)).scalars().all()
        assert "2026-09-08" in days and "2026-09-11" in days
        assert "trial:2026-09-08:old-guest" not in days
        assert "trial:2026-09-11:current-guest" in days
    assert wallet.prune_private_cache()["responses_cleared"] == 0


def test_cache_pruning_is_bounded_and_does_not_bypass_schema_capability(state, monkeypatch):
    user_id = user()
    for number in range(2):
        key, _ = wallet.reserve(user_id, str(number), str(number), 2)
        wallet.settle(user_id, key, input_tokens=1000, response={"answer": "private"})
    state["now"] += timedelta(minutes=16)
    assert wallet.prune_private_cache(batch_size=1)["responses_cleared"] == 1
    assert wallet.prune_private_cache(batch_size=1)["responses_cleared"] == 1
    monkeypatch.setattr(catalog, "SCHEMA_RECOVERY_RELEASE_READY", False)
    wallet.METADATA.drop_all(billing.ENGINE)
    assert wallet.prune_private_cache()["available"] is False
    monkeypatch.setattr(catalog, "SCHEMA_RECOVERY_RELEASE_READY", True)
    with pytest.raises(LectureSiftError):
        wallet.prune_private_cache()


def test_welcome_is_once_and_requires_verified_owner(state):
    user_id = user()
    assert wallet.status(user_id)["balance"] == 50
    assert wallet.status(user_id)["balance"] == 50
    with pytest.raises(LectureSiftError):
        wallet.status(user(False))


def test_topup_payment_is_idempotent_and_grants_no_minutes_or_downloads(state):
    user_id = user()
    order = paid(user_id, "ai_1000")
    billing.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"])
    assert wallet.status(user_id)["balance"] == 1050
    assert wallet.status(user_id)["balance"] == 1050
    status = billing.account_status(user_id)
    assert status["plan"]["code"] == "free"
    assert status["credit_minutes"] == 0
    assert status["download_enabled"] is False


def test_unpaid_topups_have_no_credits(state):
    user_id = user()
    billing.create_payment_order(user_id, "test-provider", "ai_1000", "one_time", "USD")
    assert wallet.status(user_id)["balance"] == 50


def test_topup_does_not_replace_subscription_and_monthly_credits_do_not_stack(state):
    user_id = user()
    paid(user_id, "plus", "monthly")
    assert wallet.status(user_id)["balance"] == 1550
    paid(user_id, "ai_1000")
    assert wallet.status(user_id)["balance"] == 2550
    assert billing.account_status(user_id)["plan"]["code"] == "plus"
    paid(user_id, "lite", "monthly")
    assert wallet.status(user_id)["balance"] == 1550


def test_annual_grant_renews_monthly_not_all_at_once(state):
    user_id = user()
    paid(user_id, "lite", "annual")
    assert wallet.status(user_id)["balance"] == 550
    key, _ = wallet.reserve(user_id, "first", "hash", 100)
    wallet.settle(user_id, key, input_tokens=100000, response={"answer":"done"})
    state["now"] += timedelta(days=31)
    assert wallet.status(user_id)["balance"] == 550


def test_reservation_blocks_parallel_requests_and_replays_without_double_charge(state):
    user_id = user()
    key, _ = wallet.reserve(user_id, "request-1", "same", 20)
    with pytest.raises(LectureSiftError):
        wallet.reserve(user_id, "request-2", "second", 20)
    assert wallet.status(user_id)["balance"] == 30
    wallet.settle(user_id, key, input_tokens=1000, output_tokens=500, response={"answer":"ok"})
    assert wallet.status(user_id)["balance"] == 46
    _, replay = wallet.reserve(user_id, "request-1", "same", 20)
    assert replay["charged_credits"] == 4
    with pytest.raises(LectureSiftError):
        wallet.reserve(user_id, "request-1", "changed", 20)


def test_failure_refunds_once_but_unknown_cost_remains_in_platform_budget(state):
    user_id = user()
    key, _ = wallet.reserve(user_id, "request", "hash", 20)
    wallet.settle(user_id, key, unknown_cost=True)
    assert wallet.status(user_id)["balance"] == 50
    with pytest.raises(LectureSiftError):
        wallet.settle(user_id, key)
    with billing.ENGINE.connect() as connection:
        assert connection.execute(select(wallet.BUDGET.c.credits)).scalar_one() == 20


def test_lost_worker_reservation_expires_without_rebilling(state):
    user_id = user()
    key, _ = wallet.reserve(user_id, "request", "hash", 20)
    state["now"] += timedelta(minutes=3)
    assert wallet.status(user_id)["balance"] == 50
    with pytest.raises(LectureSiftError):
        wallet.settle(user_id, key, response={"answer":"late"})


def test_global_budget_and_insufficient_wallet_prevent_provider_work(state, monkeypatch):
    user_id = user()
    with pytest.raises(LectureSiftError) as exc:
        wallet.reserve(user_id, "request", "hash", 51)
    assert exc.value.status_code == 402
    monkeypatch.setattr(wallet, "DAILY_CREDIT_CEILING", 1)
    with pytest.raises(LectureSiftError) as exc:
        wallet.reserve(user_id, "request", "hash", 20)
    assert exc.value.code == "LS-ASSIST-05"
    assert wallet.status(user_id)["balance"] == 50


def test_refund_freezes_only_purchased_credits_and_rejection_restores_unused_balance(state):
    user_id = user()
    order = paid(user_id, "ai_1000")
    assert wallet.status(user_id)["balance"] == 1050
    with billing.ENGINE.begin() as connection:
        connection.execute(rollout_service.REFUND_REQUESTS.insert().values(
            id="refund-test", user_id=user_id, order_reference=order["reference"], provider="test-provider",
            reason="Synthetic refund test", status="requested", created_at=billing.utcnow(), updated_at=billing.utcnow(),
        ))
    assert wallet.status(user_id)["balance"] == 50
    with billing.ENGINE.begin() as connection:
        connection.execute(update(rollout_service.REFUND_REQUESTS).values(status="rejected"))
    assert wallet.status(user_id)["balance"] == 1050


def test_cross_account_settlement_and_export_are_isolated(state):
    first, second = user(), user()
    key, _ = wallet.reserve(first, "request", "hash", 20)
    with pytest.raises(LectureSiftError):
        wallet.settle(second, key, response={"answer":"wrong owner"})
    assert wallet.export_data(second)[wallet.REQUESTS.name] == []
    with billing.ENGINE.begin() as connection:
        wallet.close_account(connection, first)
    assert all(not rows for rows in wallet.export_data(first).values())


def test_guest_trial_is_durable_and_limited_to_three(state):
    for _ in range(3):
        wallet.reserve_trial("synthetic-peer")
    with pytest.raises(LectureSiftError) as exc:
        wallet.reserve_trial("synthetic-peer")
    assert exc.value.code == "LS-ASSIST-08"


def test_catalog_has_all_currencies_and_preserves_prior_snapshot_rights(state):
    from dataclasses import asdict
    from lecturesift.billing import PLAN_BY_CODE, SUPPORTED_CURRENCIES
    assert set(catalog.PRICES) == set(SUPPORTED_CURRENCIES)
    assert catalog.offers("JPY")["packs"][0]["amount_minor"] == 600
    snapshot = asdict(PLAN_BY_CODE["plus"])
    snapshot.pop("assistant_credits")
    assert billing._plan_from_snapshot(snapshot, "plus").assistant_credits == 0
    assert billing._plan_from_snapshot(snapshot, "plus").minutes == 900


@pytest.mark.parametrize("value", ["https://127.0.0.1/secret", "file:/tmp/test", "data:image/svg+xml;base64,AAAA", "data:image/png;base64,not-base64"])
def test_images_reject_remote_urls_and_invalid_data(value):
    with pytest.raises(LectureSiftError):
        assistant._image_url(value)


def test_image_is_reencoded_and_bounded():
    output = io.BytesIO()
    Image.new("RGB", (800, 600)).save(output, "PNG")
    result = assistant._image_url("data:image/png;base64," + base64.b64encode(output.getvalue()).decode())
    image = Image.open(io.BytesIO(base64.b64decode(result.split(",")[1])))
    assert image.format == "JPEG" and max(image.size) == 512


def test_chat_uses_owned_context_actual_tokens_and_safe_action_schema(state, monkeypatch):
    user_id = user()
    captured = {}
    monkeypatch.setattr(assistant.config, "OPENAI_API_KEY", "synthetic-not-a-real-key")
    monkeypatch.setattr(assistant, "record_openai_response", lambda *args: True)
    monkeypatch.setattr(assistant, "_context", lambda uid, currency: {"owner": uid, "sitemap": assistant.SITEMAP})

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.responses = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(status="completed", output_text=json.dumps({"answer":"Your account page has the details.","action":"account"}), usage=SimpleNamespace(input_tokens=1000,output_tokens=100))

    monkeypatch.setattr(assistant, "OpenAI", Client)
    payload = assistant.ChatRequest(request_id="synthetic-request-1", message="My account?", language="en")
    answer = assistant.chat(user_id, payload)
    assert answer["charged_credits"] == 2
    assert answer["balance"] == 48
    assert answer["path"] == "/account.html"
    assert captured["store"] is False and "tools" not in captured
    assert captured["text"]["format"]["strict"] is True
    assert user_id in captured["input"][0]["content"][0]["text"]
    assert assistant.chat(user_id, payload)["balance"] == 48


def test_chat_auth_and_disabled_gate_do_not_call_model(state, monkeypatch):
    client = TestClient(app_module.app)
    assert client.post("/assistant/chat", json={}).status_code == 401
    monkeypatch.setattr(catalog, "SCHEMA_RECOVERY_RELEASE_READY", False)
    response = client.post("/assistant/trial", json={"message":"Hello","language":"en"})
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"


def test_postgres_parallel_reservations_cannot_overspend(monkeypatch):
    import os
    from concurrent.futures import ThreadPoolExecutor
    url = os.getenv("TEST_ASSISTANT_POSTGRES_URL")
    if not url:
        pytest.skip("Only the isolated GitHub Actions PostgreSQL service runs this test")
    engine = create_engine(url)
    billing.METADATA.create_all(engine)
    wallet.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout_service, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(catalog, "SCHEMA_RECOVERY_RELEASE_READY", True)
    monkeypatch.setenv("ASSISTANT_ENABLED", "true")
    user_id = user()
    assert wallet.status(user_id)["balance"] == 50
    def attempt(index):
        try:
            return wallet.reserve(user_id, f"parallel-{index}", "same", 30)[0]
        except LectureSiftError:
            return None
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            keys = [key for key in pool.map(attempt, range(8)) if key]
        assert len(keys) == 1
        assert wallet.status(user_id)["balance"] == 20
        wallet.settle(user_id, keys[0], input_tokens=1000, output_tokens=100, response={"answer":"ok"})
        assert wallet.status(user_id)["balance"] == 48
    finally:
        engine.dispose()


@pytest.fixture
def image_provider(state, monkeypatch):
    from lecturesift import assistant_images
    monkeypatch.setenv('ASSISTANT_IMAGES_ENABLED', 'true')
    monkeypatch.setattr(assistant_images.config, 'OPENAI_API_KEY', 'synthetic-not-a-real-key')
    content = io.BytesIO()
    Image.new('RGB', (1024, 1024), 'blue').save(content, 'JPEG')
    captured = {'calls': [], 'costs': [], 'failure': False}
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['max_retries'] == 0 and kwargs['timeout'] < 120
            self.images = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def generate(self, **kwargs):
            captured['calls'].append(kwargs)
            if captured['failure']:
                raise RuntimeError('private-provider-error')
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(content.getvalue()).decode())],
                                   usage=SimpleNamespace(input_tokens=200, output_tokens=1056))
    monkeypatch.setattr(assistant_images, 'OpenAI', Client)
    monkeypatch.setattr(assistant_images, 'record_cost', lambda **values: captured['costs'].append(values))
    return assistant_images, captured


def test_image_fixed_price_replay_and_provider_budget_are_distinct(image_provider):
    images, captured = image_provider
    owner = user()
    paid(owner, 'ai_1000')
    payload = images.ImageRequest(request_id='synthetic-image-123', prompt='Water cycle illustration')
    result = images.generate(owner, payload)
    assert result['charged_credits'] == 200 and result['balance'] == 850
    assert result['image'].startswith('data:image/jpeg;base64,')
    assert images.generate(owner, payload) == result
    assert len(captured['calls']) == 1 and len(captured['costs']) == 2
    call = captured['calls'][0]
    assert call['model'] == 'gpt-image-1.5' and call['n'] == 1
    assert call['quality'] == 'medium' and call['size'] == '1024x1024'
    assert 'Water cycle' not in str(captured['costs'])
    with billing.ENGINE.connect() as connection:
        assert connection.execute(select(wallet.BUDGET.c.credits)).scalar_one() == 174
    other = user()
    with pytest.raises(LectureSiftError) as error:
        images.generate(other, payload)
    assert error.value.status_code == 402 and len(captured['calls']) == 1


def test_failed_image_refunds_user_but_retains_unknown_platform_cost(image_provider):
    images, captured = image_provider
    owner = user()
    paid(owner, 'ai_1000')
    captured['failure'] = True
    payload = images.ImageRequest(request_id='synthetic-image-fail', prompt='Synthetic diagram')
    with pytest.raises(LectureSiftError) as error:
        images.generate(owner, payload)
    assert error.value.code == 'LS-ASSIST-07'
    assert 'private-provider-error' not in str(error.value)
    assert wallet.status(owner)['balance'] == 1050
    with billing.ENGINE.connect() as connection:
        assert connection.execute(select(wallet.BUDGET.c.credits)).scalar_one() == 200
    with pytest.raises(LectureSiftError):
        images.generate(owner, payload)
    assert len(captured['calls']) == 1


def test_image_switch_and_utf8_limit_prevent_any_charge(image_provider, monkeypatch):
    images, captured = image_provider
    owner = user()
    paid(owner, 'ai_1000')
    with pytest.raises(LectureSiftError) as error:
        images.generate(owner, images.ImageRequest(request_id='synthetic-too-large', prompt='图' * 400))
    assert error.value.status_code == 422
    monkeypatch.setenv('ASSISTANT_IMAGES_ENABLED', 'false')
    assert catalog.offers('TRY')['image']['available'] is False
    with pytest.raises(LectureSiftError) as error:
        images.generate(owner, images.ImageRequest(request_id='synthetic-disabled', prompt='Diagram'))
    assert error.value.status_code == 503
    assert captured['calls'] == [] and wallet.status(owner)['balance'] == 1050


def test_image_endpoint_requires_authentication_and_sanitizes_large_bodies(image_provider):
    images, captured = image_provider
    client = TestClient(app_module.app)
    assert client.post('/assistant/image', json={'prompt': 'Diagram'}).status_code == 401
    app_module.app.dependency_overrides[app_module._billing_user] = lambda: {'id': user_id}
    user_id = user()
    try:
        response = client.post('/assistant/image', content=b'x' * 9000)
        assert response.status_code == 413
        assert response.headers['Cache-Control'] == 'no-store'
        response = client.post('/assistant/image', json={'prompt': 'private-input', 'request_id': 'bad'})
        assert response.status_code == 422 and 'private-input' not in response.text
    finally:
        app_module.app.dependency_overrides.clear()
    assert not captured['calls']
