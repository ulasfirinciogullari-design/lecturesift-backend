"""Short signup, guest result claims, checkout buyer data and Google sign-in.

Synthetic data only: no provider, Google or email request leaves the test.
"""
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

import lecturesift.app as app_module
from lecturesift import billing_service as billing
from lecturesift import config, google_signin, guest_claim, jobs, payments, referrals, security
from lecturesift import rollout_service as rollout
from lecturesift import workspace_state as workspace
from main import app

PASSWORD = "Strong-test-password1"
CLIENT_ID = "1234567890-synthetic.apps.googleusercontent.com"
client = TestClient(app)


@pytest.fixture
def state(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    workspace.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(config, "REQUIRE_DURABLE_PROCESSING", False)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(jobs, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs, "REDIS_URL", "")
    store = jobs.JobStore()
    monkeypatch.setattr(jobs, "JOBS", store)
    sent = {}
    monkeypatch.setattr(app_module, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(
        app_module, "_send_verification_email",
        lambda email, token, code: sent.update(email=email, token=token, code=code),
    )
    yield store, sent
    engine.dispose()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def new_guest() -> tuple[str, str]:
    response = client.post("/billing/guest-session", json={"device_id": f"device-{uuid.uuid4()}"})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["token"], body["account"]["user"]["id"]


def guest_job(store, owner: str, job_id: str, **extra) -> dict:
    options = {"billing_user_id": owner, "job_type": "study_pack"}
    values = {"status": "done", "worker_state": "done", "title": "Guest lesson", "retention_seconds": 86400}
    store.create(job_id, store._state_path.parent / job_id, options, **{**values, **extra})
    return options


def verified_account() -> tuple[str, str, str]:
    email = f"account-{uuid.uuid4().hex}@example.com"
    created = billing.register_user(email, PASSWORD)
    verified = billing.verify_email(created["verification_token"])
    return email, verified["token"], verified["user"]["id"]


def owner(store, job_id: str) -> str:
    return store.metadata(job_id)["options"]["billing_user_id"]


def google_claims(**overrides) -> dict:
    claims = {
        "aud": CLIENT_ID, "iss": "https://accounts.google.com", "exp": str(int(time.time()) + 600),
        "email": "Student@Example.com", "email_verified": "true", "sub": "1234567890",
        "given_name": "Ada", "family_name": "Lovelace",
    }
    claims.update(overrides)
    return claims


def google_links(user_id: str) -> list:
    with billing.ENGINE.connect() as connection:
        return connection.execute(select(billing.AUTH_TOKENS.c.token_hash).where(
            billing.AUTH_TOKENS.c.user_id == user_id,
            billing.AUTH_TOKENS.c.purpose == billing.GOOGLE_LINK_PURPOSE,
        )).scalars().all()


def sign_in_google(**headers):
    return client.post("/billing/google", json={"credential": "header.payload.signature"}, headers=headers)


def test_short_signup_needs_only_email_and_password(state):
    _store, sent = state
    email = f"short-{uuid.uuid4().hex}@example.com"
    response = client.post("/billing/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["first_name"] == "" and body["user"]["last_name"] == ""
    assert body["user"]["name"] == ""
    assert body["guest_claim"] == {"status": "not_provided"}

    # The password rule is unchanged: 10+ characters, upper, lower and digit.
    for weak in ("Short1a", "alllowercase12", "ALLUPPERCASE12", "NoDigitsHereAtAll"):
        rejected = client.post("/billing/register", json={"email": f"weak-{uuid.uuid4().hex}@example.com", "password": weak})
        assert rejected.status_code == 400
        assert rejected.json()["detail"]["code"] == "LS-BILL-05"

    verified = client.post("/billing/verify-email", json={"token": sent["token"]})
    assert verified.status_code == 200
    assert verified.json()["account"]["user"]["email_verified"] is True
    assert verified.json()["guest_claim"]["status"] == "not_provided"

    named = billing.register_user(f"named-{uuid.uuid4().hex}@example.com", PASSWORD, " Ada ", "Lovelace")
    assert named["user"]["name"] == "Ada Lovelace"
    with pytest.raises(billing.BillingError, match="Ad 2 ile 80"):
        billing.register_user(f"bad-{uuid.uuid4().hex}@example.com", PASSWORD, "A", "")


def _configure_iyzico(monkeypatch) -> None:
    monkeypatch.setattr(config, "IYZICO_API_KEY", "live-api-key")
    monkeypatch.setattr(config, "IYZICO_SECRET_KEY", "live-secret-key")
    monkeypatch.setattr(config, "PAYMENT_TOKEN_BINDING_SECRET", "payment-binding-secret-at-least-32-chars")
    monkeypatch.setattr(config, "IYZICO_BASE_URL", "https://api.iyzipay.com")
    monkeypatch.setattr(config, "IYZICO_BANK_TRANSFER_ENABLED", False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://api.example.invalid")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "billing@example.com")


def test_checkout_asks_for_missing_buyer_data_and_never_invents_it(state, monkeypatch):
    _configure_iyzico(monkeypatch)
    monkeypatch.setattr(payments.httpx, "post", lambda *args, **kwargs: pytest.fail("must not contact the provider"))
    _email, token, user_id = verified_account()
    payload = {
        "plan_code": "plus", "interval": "monthly", "currency": "TRY", "payment_method": "card",
        "billing_address": "Örnek Mahallesi No 11", "billing_city": "Hatay", "billing_zip_code": "31800",
        "language": "tr", "terms_accepted": True, "early_performance_requested": True,
    }
    response = client.post("/billing/checkout", headers={**auth(token), "X-Forwarded-For": "203.0.113.90"}, json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "LS-PAY-03"
    assert detail["missing_fields"] == ["first_name", "last_name", "phone"]
    assert "ad, soyad, telefon" in detail["message"]

    partial = client.post(
        "/billing/checkout", headers={**auth(token), "X-Forwarded-For": "203.0.113.90"},
        json={**payload, "first_name": "Ada", "last_name": "Lovelace", "billing_city": " "},
    )
    assert partial.json()["detail"]["missing_fields"] == ["phone", "billing_city"]
    with billing.ENGINE.connect() as connection:
        orders = connection.execute(select(billing.PAYMENT_ORDERS).where(billing.PAYMENT_ORDERS.c.user_id == user_id)).all()
    assert orders == []

    # PayTR needs name, address and phone as well.
    monkeypatch.setattr(config, "PAYTR_MERCHANT_ID", "merchant")
    monkeypatch.setattr(config, "PAYTR_MERCHANT_KEY", "merchant-key")
    monkeypatch.setattr(config, "PAYTR_MERCHANT_SALT", "merchant-salt")
    user = billing.authenticate_session(token)
    with pytest.raises(payments.MissingBuyerDataError) as missing:
        payments.create_paytr_checkout(
            user, plan_code="plus", interval="monthly", currency="TRY", user_ip="203.0.113.90",
            first_name="", last_name="", billing_address="", phone="", language="tr",
            terms_accepted=True, early_performance_requested=True, user_agent="pytest",
        )
    assert missing.value.fields == ["first_name", "last_name", "phone", "billing_address"]


def test_registering_from_a_guest_session_moves_the_result_after_verification(state):
    store, sent = state
    guest_token, guest_id = new_guest()
    guest_job(store, guest_id, "guest-job-pending")
    email = f"claim-{uuid.uuid4().hex}@example.com"
    response = client.post("/billing/register", json={"email": email, "password": PASSWORD, "guest_token": guest_token})
    assert response.status_code == 200, response.text
    assert response.json()["guest_claim"] == {"status": "pending"}
    # Nothing moves before the new account proves its email address.
    assert owner(store, "guest-job-pending") == guest_id

    verified = client.post("/billing/verify-email-code", json={"email": email, "code": sent["code"]})
    assert verified.status_code == 200, verified.text
    assert verified.json()["guest_claim"] == {"status": "claimed", "jobs": 1}
    account_id = verified.json()["user"]["id"]
    job = store.metadata("guest-job-pending")
    assert job["options"]["billing_user_id"] == account_id
    assert job["retention_seconds"] >= 7 * 86400
    assert [row["job_id"] for row in store.list_for_user(account_id)] == ["guest-job-pending"]
    # The spent guest token stops working; the pending claim is single use.
    assert client.get("/billing/me", headers=auth(guest_token)).status_code == 401
    assert guest_claim.claim_pending_guest(account_id)["status"] == "not_provided"


def test_an_invalid_guest_token_never_blocks_registration(state):
    _store, _sent = state
    _email, account_token, _account_id = verified_account()
    for token in ("not-a-session", account_token):
        response = client.post(
            "/billing/register",
            json={"email": f"plain-{uuid.uuid4().hex}@example.com", "password": PASSWORD, "guest_token": token},
        )
        assert response.status_code == 200, response.text
        assert response.json()["guest_claim"] == {"status": "refused"}


def test_only_the_guest_session_holder_can_claim_and_only_once(state):
    store, _sent = state
    guest_token, guest_id = new_guest()
    guest_job(store, guest_id, "guest-job-once")
    first_email, _first_token, first_id = verified_account()
    second_email, second_token, second_id = verified_account()

    # A forged value or a registered account session is not the guest session.
    for token in ("not-a-session", second_token):
        refused = client.post("/billing/login", json={"email": first_email, "password": PASSWORD, "guest_token": token})
        assert refused.status_code == 200
        assert refused.json()["guest_claim"]["status"] == "refused"
    assert owner(store, "guest-job-once") == guest_id

    claimed = client.post("/billing/login", json={"email": first_email, "password": PASSWORD, "guest_token": guest_token})
    assert claimed.status_code == 200
    assert claimed.json()["guest_claim"] == {"status": "claimed", "jobs": 1}
    assert owner(store, "guest-job-once") == first_id

    # The old token is spent, and the guest identity itself is claimed once.
    again = client.post("/billing/login", json={"email": second_email, "password": PASSWORD, "guest_token": guest_token})
    assert again.json()["guest_claim"]["status"] == "refused"
    with pytest.raises(billing.BillingError, match="başka bir hesaba"):
        guest_claim.claim_guest_account(second_id, guest_id)
    assert guest_claim.claim_guest_account(first_id, guest_id)["status"] == "already_claimed"
    assert owner(store, "guest-job-once") == first_id


def test_claim_moves_library_placement_and_survives_a_worker_rewrite(state):
    store, _sent = state
    guest_token, guest_id = new_guest()
    worker_options = guest_job(store, guest_id, "guest-job-working", status="working", worker_state=None)
    _email, _token, account_id = verified_account()
    now = billing.utcnow()
    with billing.ENGINE.begin() as connection:
        connection.execute(workspace.FOLDERS.insert().values(id="folder-guest", user_id=guest_id, name="Biyoloji", created_at=now))
        connection.execute(workspace.FOLDERS.insert().values(id="folder-account", user_id=account_id, name="biyoloji", created_at=now))
        connection.execute(workspace.ITEMS.insert().values(job_id="guest-job-working", user_id=guest_id, folder_id="folder-guest"))

    assert guest_claim.claim_guest_session(account_id, guest_token) == {"status": "claimed", "jobs": 1}
    with billing.ENGINE.connect() as connection:
        item = connection.execute(select(workspace.ITEMS).where(workspace.ITEMS.c.job_id == "guest-job-working")).one()
        folders = connection.execute(select(workspace.FOLDERS.c.id)).scalars().all()
    assert (item.user_id, item.folder_id) == (account_id, "folder-account")
    assert folders == ["folder-account"]

    # A worker still holding its own options copy cannot move the job back.
    store.update("guest-job-working", options=worker_options, status="done", worker_state="done")
    assert owner(store, "guest-job-working") == account_id
    assert worker_options["billing_user_id"] == guest_id


def test_auth_config_and_google_sign_in_are_disabled_by_default(state, monkeypatch):
    source = Path(config.__file__).read_text(encoding="utf-8")
    assert 'GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()' in source
    body = client.get("/billing/auth-config").json()
    assert body["google"] == {"enabled": False, "client_id": None}
    assert body["registration"] == {"required_fields": ["email", "password"], "password_min_length": 10}
    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: pytest.fail("must not contact Google"))
    response = client.post("/billing/google", json={"credential": "a.b.c"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "LS-GOOGLE-00"

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    assert client.get("/billing/auth-config").json()["google"] == {"enabled": True, "client_id": CLIENT_ID}


def test_google_id_token_audience_issuer_expiry_and_email_are_checked(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: google_claims(hd="Example.COM"))
    identity = google_signin.verify_google_id_token("header.payload.signature")
    assert identity == {
        "email": "student@example.com", "sub": "1234567890", "hd": "example.com",
        "given_name": "Ada", "family_name": "Lovelace",
    }
    for bad in (
        {"aud": "other-client.apps.googleusercontent.com"},
        {"iss": "https://accounts.example.invalid"},
        {"email_verified": "false"},
        {"exp": str(int(time.time()) - 5)},
        {"email": ""},
        # casefold() would turn the long s into "s", so it is refused first.
        {"email": "ſtudent@gmail.com"},
        {"sub": ""},
        {"sub": "1" * 256},
        {"hd": "éxample.com"},
    ):
        monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential, bad=bad: google_claims(**bad))
        with pytest.raises(billing.BillingAuthenticationError):
            google_signin.verify_google_id_token("header.payload.signature")
    with pytest.raises(billing.BillingAuthenticationError):
        google_signin.verify_google_id_token("not-a-jwt")


def test_google_tokeninfo_failures_are_not_treated_as_valid(monkeypatch):
    class Response:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

    calls = []

    def fake_post(url, *, data, timeout):
        calls.append((url, data))
        return Response(400, {"error": "invalid_token"})

    monkeypatch.setattr(google_signin.httpx, "post", fake_post)
    monkeypatch.setattr(google_signin.httpx, "get", lambda *args, **kwargs: pytest.fail("the token must not go in a URL"))
    with pytest.raises(billing.BillingAuthenticationError):
        google_signin._fetch_tokeninfo("header.payload.signature")
    # The token travels in the form body; the URL carries no query string.
    assert calls == [(google_signin.GOOGLE_TOKENINFO_URL, {"id_token": "header.payload.signature"})]
    assert "?" not in google_signin.GOOGLE_TOKENINFO_URL

    def offline(url, *, data, timeout):
        raise google_signin.httpx.ConnectError("offline")

    monkeypatch.setattr(google_signin.httpx, "post", offline)
    with pytest.raises(google_signin.GoogleSignInUnavailable):
        google_signin._fetch_tokeninfo("header.payload.signature")


def test_google_sign_in_creates_a_verified_account_and_claims_the_guest(state, monkeypatch):
    store, _sent = state
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    email = f"google-{uuid.uuid4().hex}@gmail.com"
    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: google_claims(email=email))
    guest_token, guest_id = new_guest()
    guest_job(store, guest_id, "guest-job-google")

    response = client.post("/billing/google", json={"credential": "header.payload.signature", "guest_token": guest_token})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True and body["google_link"] == "created"
    assert body["user"]["email"] == email and body["user"]["email_verified"] is True
    assert (body["user"]["first_name"], body["user"]["last_name"]) == ("Ada", "Lovelace")
    assert body["guest_claim"] == {"status": "claimed", "jobs": 1}
    assert billing.authenticate_session(body["token"])["id"] == body["user"]["id"]
    assert owner(store, "guest-job-google") == body["user"]["id"]
    assert google_links(body["user"]["id"]) == [billing._google_link_hash("1234567890")]

    again = sign_in_google()
    assert again.status_code == 200
    assert again.json()["created"] is False and again.json()["google_link"] == "existing"
    assert again.json()["user"]["id"] == body["user"]["id"]

    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: google_claims(email=email, aud="other.apps.googleusercontent.com"))
    rejected = sign_in_google()
    assert rejected.status_code == 401
    assert rejected.json()["detail"]["code"] == "LS-GOOGLE-01"


def test_google_links_an_existing_account_only_where_google_is_authoritative(state, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    notices = []
    monkeypatch.setattr(
        app_module, "send_transactional_email",
        lambda to, subject, html, text: notices.append((to, subject)),
    )
    email, _token, account_id = verified_account()  # an example.com address

    # A Google account that merely carries the address (a recycled university
    # or company mailbox, say) gets no session without the password.
    for extra in ({}, {"hd": "other.example"}):
        monkeypatch.setattr(
            google_signin, "_fetch_tokeninfo",
            lambda credential, extra=extra: google_claims(email=email, sub="former-owner", **extra),
        )
        refused = sign_in_google()
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "LS-GOOGLE-04"
        assert "parolanla giriş yap" in refused.json()["detail"]["message"]
    assert google_links(account_id) == [] and notices == []
    assert billing.login_user(email, PASSWORD)["user"]["id"] == account_id

    # A Google Workspace account of the address's own domain speaks for it.
    monkeypatch.setattr(
        google_signin, "_fetch_tokeninfo",
        lambda credential: google_claims(email=email, sub="workspace-user", hd="example.com"),
    )
    linked = sign_in_google()
    assert linked.status_code == 200, linked.text
    assert linked.json()["user"]["id"] == account_id
    assert linked.json()["created"] is False and linked.json()["google_link"] == "linked"
    assert google_links(account_id) == [billing._google_link_hash("workspace-user")]
    assert notices == [(email, "LectureSift hesabına Google ile giriş eklendi")]

    # Later sign-ins match the stored subject, even after the Google address
    # changes, and send no second notice.
    monkeypatch.setattr(
        google_signin, "_fetch_tokeninfo",
        lambda credential: google_claims(email=f"renamed-{uuid.uuid4().hex}@gmail.com", sub="workspace-user"),
    )
    again = sign_in_google()
    assert again.status_code == 200, again.text
    assert again.json()["user"]["id"] == account_id and again.json()["google_link"] == "existing"
    assert len(notices) == 1

    # Resetting the password through the mailbox revokes the link.
    reset = billing.create_password_reset_token(email)
    billing.reset_password(reset["token"], "Another-strong-password2")
    assert google_links(account_id) == []
    monkeypatch.setattr(
        google_signin, "_fetch_tokeninfo",
        lambda credential: google_claims(email=email, sub="workspace-user"),
    )
    assert sign_in_google().status_code == 409


def test_google_sign_in_rejects_non_ascii_addresses_before_folding_them(state):
    email = f"student{uuid.uuid4().hex[:8]}@gmail.com"
    created = billing.register_user(email, PASSWORD)
    account_id = billing.verify_email(created["verification_token"])["user"]["id"]
    lookalike = "ſ" + email[1:]
    assert lookalike.casefold() == email
    with pytest.raises(billing.BillingAuthenticationError):
        billing.sign_in_with_google(lookalike, sub="lookalike-subject")
    assert google_links(account_id) == []
    # Gmail addresses are Google's own: the real address links without hd.
    result = billing.sign_in_with_google(email.upper(), sub="gmail-subject")
    assert result["user"]["id"] == account_id and result["google_link"] == "linked"


def test_google_sign_in_takes_over_an_unverified_registration_safely(state, monkeypatch):
    store, _sent = state
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    referrals.METADATA.create_all(billing.ENGINE)
    _squatter_token, squatter_guest = new_guest()
    guest_job(store, squatter_guest, "squatter-guest-job")
    email = f"preregistered-{uuid.uuid4().hex}@example.com"
    created = billing.register_user(
        email, PASSWORD, "Mallory", "Squatter", "+90 555 111 22 33", guest_user_id=squatter_guest,
    )
    account_id = created["user"]["id"]
    _inviter_email, _inviter_token, inviter_id = verified_account()
    with billing.ENGINE.begin() as connection:
        connection.execute(referrals.REWARDS.insert().values(
            id=str(uuid.uuid4()), invitee_user_id=account_id, inviter_user_id=inviter_id, status="invited",
            policy_version="synthetic", inviter_minutes=30, invitee_minutes=30, created_at=billing.utcnow(),
        ))

    # Without a matching hd claim Google does not speak for example.com.
    monkeypatch.setattr(google_signin, "_fetch_tokeninfo", lambda credential: google_claims(email=email))
    assert sign_in_google().status_code == 409

    monkeypatch.setattr(
        google_signin, "_fetch_tokeninfo", lambda credential: google_claims(email=email, hd="example.com"),
    )
    response = sign_in_google()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is False and body["user"]["id"] == account_id
    assert body["user"]["email_verified"] is True
    # Nothing the earlier registrant set up survives: no pending guest claim,
    # no referral attribution and no name or phone to prefill checkout.
    assert body["guest_claim"]["status"] == "not_provided"
    assert owner(store, "squatter-guest-job") == squatter_guest
    assert guest_claim.claim_pending_guest(account_id)["status"] == "not_provided"
    with billing.ENGINE.connect() as connection:
        profile = connection.execute(
            select(billing.USER_PROFILES).where(billing.USER_PROFILES.c.user_id == account_id)
        ).one()
        rewards = connection.execute(
            select(referrals.REWARDS).where(referrals.REWARDS.c.invitee_user_id == account_id)
        ).all()
    assert (profile.first_name, profile.last_name, profile.phone) == ("", "", None)
    assert rewards == []
    # Whoever chose the password before Google verified the address cannot use it.
    with pytest.raises(billing.BillingAuthenticationError):
        billing.login_user(email, PASSWORD)
    with pytest.raises(billing.BillingAuthenticationError):
        billing.verify_email(created["verification_token"])


def test_google_sign_in_has_a_global_ceiling_across_clients(state, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    # A fresh process-local limiter with a frozen bucket keeps limits deterministic.
    limiter = security.RateLimiter()
    limiter._redis = None
    monkeypatch.setattr(app_module, "RATE_LIMITER", limiter)
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 1_800_000_000.0))
    monkeypatch.setattr(app_module, "GOOGLE_SIGN_IN_GLOBAL_LIMIT", 3)
    monkeypatch.setattr(
        google_signin, "_fetch_tokeninfo",
        lambda credential: google_claims(aud="other.apps.googleusercontent.com"),
    )
    responses = [sign_in_google(**{"X-Forwarded-For": f"203.0.113.{index}"}) for index in range(1, 5)]
    # Each client is far below its own limit; the shared ceiling still applies.
    assert [response.status_code for response in responses] == [401, 401, 401, 429]
    assert responses[-1].json()["detail"]["code"] == "LS-SEC-01"
