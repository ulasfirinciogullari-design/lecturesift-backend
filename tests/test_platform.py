import lecturesift.platform as platform_module


def make_store(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(platform_module, "REDIS_URL", "")
    monkeypatch.setattr(platform_module, "RESEND_API_KEY", "")
    return platform_module.PlatformStore()


def create_user(store, email="student@example.com"):
    requested = store.request_code(email, "login")
    code = requested["development_code"]
    verified = store.verify_code(email, code, "Student")
    return verified["token"], verified["user"]


def test_passwordless_account_profile_and_email_change(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    token, user = create_user(store)
    assert user["email"] == "student@example.com"
    updated = store.update_profile(token, "New Name", "en")
    assert updated["name"] == "New Name"
    assert updated["preferred_language"] == "en"

    requested = store.request_code("new@example.com", "email_change", token)
    result = store.verify_code("new@example.com", requested["development_code"])
    assert result["user"]["email"] == "new@example.com"
    assert store.me(token)["email"] == "new@example.com"


def test_guest_trial_is_limited_and_one_time(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    monkeypatch.setattr(platform_module, "GUEST_TRIAL_MAX_MINUTES", 5.0)
    first = store.authorize_minutes("", "device-1", 4.9)
    assert first["mode"] == "guest"
    try:
        store.authorize_minutes("", "device-1", 1.0)
    except ValueError as exc:
        assert "daha önce" in str(exc)
    else:
        raise AssertionError("guest trial was reusable")
    try:
        store.authorize_minutes("", "device-2", 5.5)
    except ValueError as exc:
        assert "en fazla" in str(exc)
    else:
        raise AssertionError("guest duration limit was bypassed")


def test_bank_transfer_order_admin_approval_adds_minutes(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    monkeypatch.setattr(platform_module, "ADMIN_TOKEN", "secret-admin")
    monkeypatch.setattr(platform_module, "BANK_TRANSFER_IBAN", "configured-iban")
    monkeypatch.setattr(platform_module, "BANK_TRANSFER_RECIPIENT", "Configured Recipient")
    token, _ = create_user(store)
    order = store.create_order(token, "starter", "monthly", "TRY")
    assert order["status"] == "pending_transfer"
    assert order["order_no"].startswith("LS-")
    assert order["transfer_note"] == order["order_no"]
    assert order["bank"]["iban"] == "configured-iban"
    pending = store.admin_orders("secret-admin")
    assert pending[0]["order_no"] == order["order_no"]
    store.decide_order("secret-admin", order["order_no"], True)
    me = store.me(token)
    assert me["minutes_balance"] == platform_module.PLANS["starter"]["minutes"]
    assert me["plan"] == "starter"


def test_currency_prices_and_instagram_bonus(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    monkeypatch.setattr(platform_module, "ADMIN_TOKEN", "secret-admin")
    monkeypatch.setattr(platform_module, "INSTAGRAM_BONUS_MINUTES", 30.0)
    token, _ = create_user(store)
    prices = store.prices("EUR")
    assert prices["currency"] == "EUR"
    assert all("monthly" in plan and "yearly" in plan for plan in prices["plans"])
    reward = store.claim_instagram(token, "@student")
    assert reward["status"] == "pending_verification"
    store.decide_reward("secret-admin", reward["id"], True)
    assert store.me(token)["minutes_balance"] == 30.0
    try:
        store.claim_instagram(token, "@student")
    except ValueError as exc:
        assert "daha önce" in str(exc)
    else:
        raise AssertionError("Instagram bonus was reusable")


def test_eta_learns_from_completed_jobs(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    baseline = store.eta_seconds(10, 0, 0)
    assert baseline >= 300
    store.record_job_speed(media_minutes=10, elapsed_seconds=200, bytes_size=1000)
    learned = store.eta_seconds(10, 0, 0)
    assert 190 <= learned <= 210
