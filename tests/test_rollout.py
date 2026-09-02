import json
import re
import uuid
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import lecturesift.jobs as jobs_module
import lecturesift.rollout_service as rollout_service
import lecturesift.exports as exports_module
from lecturesift import config, pipeline
from lecturesift.billing_service import (
    ENGINE,
    IYZICO_BANK_TRANSFER_INTENT_PROVIDER,
    IYZICO_BANK_TRANSFER_PROVIDER,
    IYZICO_CARD_INTENT_PROVIDER,
    IYZICO_CARD_PROVIDER,
    IYZICO_LEGACY_PROVIDER,
    USER_PROFILES,
    create_payment_order,
    register_user,
    verify_email,
)
from lecturesift.pipeline_enhancements import install_pipeline_enhancements
from main import app


client = TestClient(app)


def new_account() -> tuple[str, str]:
    email = f"rollout-{uuid.uuid4()}@example.com"
    created = register_user(email, "Strong-test-password1", "Test", "User", country_code="TR")
    verified = verify_email(created["verification_token"])
    return email, verified["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_display_ads_are_disabled_by_default_and_hide_unit_details(monkeypatch):
    monkeypatch.setattr(config, "DISPLAY_ADS_ENABLED", False)
    monkeypatch.setattr(config, "DISPLAY_AD_UNIT_PATH", "/1234567/lecturesift_banner")
    monkeypatch.setattr(config, "SITE_BANNER_ENABLED", True)
    disabled = client.get("/ads/config")
    assert disabled.status_code == 200
    assert disabled.json() == {
        "enabled": False,
        "provider": None,
        "banner_unit_path": None,
        "consent_required": True,
        "paid_plans_ad_free": True,
        "adsense_auto_ads": {
            "enabled": True,
            "publisher_id": config.ADSENSE_PUBLISHER_ID,
        },
        "house_campaign": {
            "enabled": True,
            "title": config.SITE_BANNER_TITLE,
            "text": config.SITE_BANNER_TEXT,
            "cta": config.SITE_BANNER_CTA,
            "url": config.SITE_BANNER_URL,
        },
    }

    monkeypatch.setattr(config, "DISPLAY_ADS_ENABLED", True)
    enabled = client.get("/ads/config").json()
    assert enabled["enabled"] is True
    assert enabled["provider"] == "google_gpt"
    assert enabled["banner_unit_path"] == "/1234567/lecturesift_banner"


def test_analytics_config_requires_opt_in_and_a_valid_public_measurement_id(monkeypatch):
    monkeypatch.setattr(config, "ANALYTICS_ENABLED", False)
    monkeypatch.setattr(config, "GA_MEASUREMENT_ID", "G-4L2CBDSZ48")
    assert client.get("/analytics/config").json() == {
        "enabled": False,
        "provider": None,
        "measurement_id": None,
        "consent_required": True,
        "advertising_signals": False,
        "google_ads": {"enabled": False, "id": None, "signup_label": None, "purchase_label": None},
    }

    monkeypatch.setattr(config, "ANALYTICS_ENABLED", True)
    monkeypatch.setattr(config, "GA_MEASUREMENT_ID", "not-valid")
    assert client.get("/analytics/config").json()["enabled"] is False

    monkeypatch.setattr(config, "GA_MEASUREMENT_ID", "G-4L2CBDSZ48")
    enabled = client.get("/analytics/config").json()
    assert enabled["enabled"] is True
    assert enabled["provider"] == "google_analytics_4"
    assert enabled["measurement_id"] == "G-4L2CBDSZ48"
    assert enabled["advertising_signals"] is False

    monkeypatch.setattr(config, "GOOGLE_ADS_ID", "AW-123456789")
    monkeypatch.setattr(config, "GOOGLE_ADS_SIGNUP_LABEL", "signup-label")
    monkeypatch.setattr(config, "GOOGLE_ADS_PURCHASE_LABEL", "purchase-label")
    ads = client.get("/analytics/config").json()["google_ads"]
    assert ads == {
        "enabled": True,
        "id": "AW-123456789",
        "signup_label": "signup-label",
        "purchase_label": "purchase-label",
    }


def test_guest_trial_is_five_minutes_resumable_and_single_job():
    device = f"device-{uuid.uuid4()}"
    first = client.post("/billing/guest-session", json={"device_id": device})
    assert first.status_code == 200
    body = first.json()
    assert body["guest"] is True
    assert body["account"]["plan"]["code"] == "guest"
    assert body["account"]["remaining_minutes"] == 5
    assert body["trial"] == {
        "used": False,
        "max_minutes": 5.0,
        "remaining_minutes": 5.0,
        "job_id": None,
    }

    resumed = client.post("/billing/guest-session", json={"device_id": device})
    assert resumed.status_code == 200
    assert resumed.json()["resumed"] is True
    assert resumed.json()["account"]["user"]["id"] == body["account"]["user"]["id"]

    user_id = body["account"]["user"]["id"]
    first_job = f"job-{uuid.uuid4()}"
    rollout_service.reserve_guest_job(user_id, first_job, 4.9)
    used = client.post("/billing/guest-session", json={"device_id": device}).json()
    assert used["trial"] == {
        "used": True,
        "max_minutes": 5.0,
        "remaining_minutes": 0,
        "job_id": first_job,
    }
    rollout_status = client.get("/billing/me/rollout", headers=auth(used["token"])).json()
    assert rollout_status["guest"] is True
    assert rollout_status["guest_trial"]["used"] is True
    assert rollout_status["guest_trial"]["remaining_minutes"] == 0
    with pytest.raises(Exception, match="daha önce"):
        rollout_service.reserve_guest_job(user_id, f"job-{uuid.uuid4()}", 1.0)
    other = client.post("/billing/guest-session", json={"device_id": f"device-{uuid.uuid4()}"}).json()
    with pytest.raises(Exception, match="en fazla"):
        rollout_service.reserve_guest_job(other["account"]["user"]["id"], "too-long", 5.5)


def test_profile_email_change_and_session_rotation(monkeypatch):
    old_email, token = new_account()
    profile = client.patch(
        "/billing/me/profile",
        headers=auth(token),
        json={"first_name": "Ada", "last_name": "Lovelace", "phone": "+905551112233"},
    )
    assert profile.status_code == 200
    assert profile.json()["account"]["user"]["name"] == "Ada Lovelace"

    sent = {}
    monkeypatch.setattr(rollout_service, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(
        rollout_service,
        "send_transactional_email",
        lambda to, subject, html, text: sent.update(to=to, subject=subject, html=html, text=text),
    )
    new_email = f"changed-{uuid.uuid4()}@example.com"
    request = client.post(
        "/billing/me/email-change",
        headers=auth(token),
        json={"email": new_email},
    )
    assert request.status_code == 200
    assert request.json()["new_email"] == new_email
    code = re.search(r"\b\d{6}\b", sent["text"]).group(0)

    verified = client.post(
        "/billing/me/email-change/verify",
        headers=auth(token),
        json={"code": code},
    )
    assert verified.status_code == 200
    new_token = verified.json()["token"]
    assert verified.json()["account"]["user"]["email"] == new_email
    assert client.get("/billing/me", headers=auth(token)).status_code == 401
    assert client.get("/billing/me", headers=auth(new_token)).status_code == 200
    assert old_email != new_email


def test_legacy_account_without_profile_can_save_name_and_phone():
    _, token = new_account()
    account = client.get("/billing/me", headers=auth(token)).json()["account"]
    with ENGINE.begin() as connection:
        connection.execute(delete(USER_PROFILES).where(USER_PROFILES.c.user_id == account["user"]["id"]))

    saved = client.patch(
        "/billing/me/profile",
        headers=auth(token),
        json={"first_name": "Ulaş", "last_name": "Fırıncıoğulları", "phone": "+905336651805"},
    )
    assert saved.status_code == 200
    assert saved.json()["account"]["user"]["name"] == "Ulaş Fırıncıoğulları"


def test_contact_messages_are_stored_and_visible_to_admin(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    monkeypatch.setattr(rollout_service, "email_delivery_configured", lambda: False)
    created = client.post(
        "/contact/messages",
        json={
            "name": "Test Kullanıcı",
            "email": f"contact-{uuid.uuid4()}@example.com",
            "topic": "Teknik destek",
            "message": "Yönetici gelen kutusu için test mesajıdır.",
            "order_reference": "LS-TEST-123",
        },
    )
    assert created.status_code == 200
    reference = created.json()["reference"]
    listed = client.get("/billing/admin/contact-messages", headers=auth("admin-secret"))
    assert reference in {item["id"] for item in listed.json()["messages"]}
    resolved = client.post(
        f"/billing/admin/contact-messages/{reference}/status",
        headers=auth("admin-secret"),
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["message"]["status"] == "resolved"


def test_admin_and_user_can_continue_a_secure_support_conversation(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", "support-session-secret")
    monkeypatch.setattr(config, "CONTACT_EMAIL", "support@lecturesift.com")
    monkeypatch.setattr(rollout_service, "email_delivery_configured", lambda: False)
    user_email = f"conversation-{uuid.uuid4()}@example.com"
    created = client.post(
        "/contact/messages",
        json={
            "name": "Destek Kullanıcısı",
            "email": user_email,
            "topic": "Teknik destek",
            "message": "Panel üzerinden iki yönlü konuşma testidir.",
        },
    )
    message_id = created.json()["reference"]

    sent = {}
    monkeypatch.setattr(
        rollout_service,
        "send_transactional_email",
        lambda to, subject, html, text, **kwargs: sent.update(
            to=to, subject=subject, html=html, text=text, kwargs=kwargs
        ) or "resend-message-id",
    )
    replied = client.post(
        f"/billing/admin/contact-messages/{message_id}/reply",
        headers=auth("admin-secret"),
        json={"message": "Merhaba, sorununu inceliyoruz. Bu bağlantıdan yanıt verebilirsin."},
    )
    assert replied.status_code == 200, replied.text
    assert replied.json()["replies"][0]["delivery_status"] == "sent"
    assert sent["to"] == user_email
    assert sent["kwargs"]["reply_to"] == "support@lecturesift.com"
    conversation_url = re.search(r"https://[^\s]+support\.html#conversation=[^\s]+", sent["text"]).group(0)
    query = parse_qs(urlparse(conversation_url).fragment)
    token = query["token"][0]

    denied = client.post(
        f"/contact/conversations/{message_id}/view", json={"token": "wrong-token"}
    )
    assert denied.status_code == 401
    public = client.post(
        f"/contact/conversations/{message_id}/view", json={"token": token}
    )
    assert public.status_code == 200
    assert public.json()["replies"][0]["sender"] == "LectureSift Destek"

    monkeypatch.setattr(rollout_service, "email_delivery_configured", lambda: False)
    user_reply = client.post(
        f"/contact/conversations/{message_id}/replies",
        json={"token": token, "message": "Teşekkürler, ek bilgiyi buradan gönderiyorum."},
    )
    assert user_reply.status_code == 200, user_reply.text
    assert len(user_reply.json()["replies"]) == 2
    assert user_reply.json()["message"]["status"] == "new"

    admin_conversation = client.get(
        f"/billing/admin/contact-messages/{message_id}", headers=auth("admin-secret")
    )
    assert admin_conversation.status_code == 200
    assert [item["direction"] for item in admin_conversation.json()["replies"]] == ["admin", "user"]
    listing = client.get("/billing/admin/contact-messages", headers=auth("admin-secret")).json()
    listed = next(item for item in listing["messages"] if item["id"] == message_id)
    assert listed["reply_count"] == 2
    assert listed["last_reply_at"]


def test_failed_admin_support_email_is_stored_and_reported(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", "support-session-secret")
    monkeypatch.setattr(rollout_service, "email_delivery_configured", lambda: False)
    created = client.post(
        "/contact/messages",
        json={
            "name": "Teslimat Testi",
            "email": f"delivery-{uuid.uuid4()}@example.com",
            "topic": "Teknik destek",
            "message": "E-posta teslimat hatası kalıcı kayda alınmalıdır.",
        },
    )
    message_id = created.json()["reference"]
    monkeypatch.setattr(
        rollout_service,
        "send_transactional_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            rollout_service.EmailDeliveryError("Gönderilemedi.")
        ),
    )
    response = client.post(
        f"/billing/admin/contact-messages/{message_id}/reply",
        headers=auth("admin-secret"),
        json={"message": "Bu yanıt sağlayıcı tarafından reddedilecek."},
    )
    assert response.status_code == 503
    conversation = client.get(
        f"/billing/admin/contact-messages/{message_id}", headers=auth("admin-secret")
    ).json()
    assert conversation["replies"][0]["delivery_status"] == "failed"


def test_manual_order_admin_rejection_and_instagram_approval(monkeypatch):
    _, token = new_account()
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "Test Holder")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "support@example.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "support@example.com")
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")

    order = client.post(
        "/billing/manual-transfer/orders",
        headers=auth(token),
        json={"plan_code": "plus", "interval": "annual", "terms_accepted": True, "early_performance_requested": True},
    )
    assert order.status_code == 200
    reference = order.json()["order"]["reference"]
    assert order.json()["order"]["amount_minor"] == 449000

    pending = client.get("/admin/manual-orders?status=pending", headers=auth("admin-secret"))
    assert pending.status_code == 200
    assert reference in {item["reference"] for item in pending.json()["orders"]}
    rejected = client.post(
        f"/admin/manual-orders/{reference}/decision",
        headers=auth("admin-secret"),
        json={"approve": False},
    )
    assert rejected.status_code == 200
    assert rejected.json()["result"]["status"] == "rejected"

    instagram_handle = f"@lecture_{uuid.uuid4().hex[:12]}"
    claim = client.post(
        "/billing/instagram-reward",
        headers=auth(token),
        json={"handle": instagram_handle},
    )
    assert claim.status_code == 200
    reward_id = claim.json()["reward"]["id"]
    rewards = client.get(
        "/admin/instagram-rewards?status=pending_verification",
        headers=auth("admin-secret"),
    )
    assert reward_id in {item["id"] for item in rewards.json()["rewards"]}
    approval = client.post(
        f"/admin/instagram-rewards/{reward_id}/decision",
        headers=auth("admin-secret"),
        json={"approve": True},
    )
    assert approval.status_code == 200
    account = client.get("/billing/me", headers=auth(token)).json()["account"]
    assert account["credit_minutes"] >= 30
    duplicate = client.post(
        "/billing/instagram-reward",
        headers=auth(token),
        json={"handle": instagram_handle},
    )
    assert duplicate.status_code == 400


def test_refund_request_requires_paid_owned_order_and_tracks_manual_completion(monkeypatch):
    _, token = new_account()
    _, other_token = new_account()
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "Test Holder")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "support@example.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "support@example.com")
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")

    created = client.post(
        "/billing/manual-transfer/orders",
        headers=auth(token),
        json={"plan_code": "plus", "interval": "monthly", "terms_accepted": True, "early_performance_requested": True},
    ).json()["order"]
    reference = created["reference"]
    unpaid = client.post(
        "/billing/me/refund-requests",
        headers=auth(token),
        json={"order_reference": reference, "reason": "The service did not meet my needs."},
    )
    assert unpaid.status_code == 400
    assert client.post(
        f"/admin/manual-orders/{reference}/decision",
        headers=auth("admin-secret"),
        json={"approve": True},
    ).status_code == 200
    not_owner = client.post(
        "/billing/me/refund-requests",
        headers=auth(other_token),
        json={"order_reference": reference, "reason": "This order should not belong to me."},
    )
    assert not_owner.status_code == 400

    requested = client.post(
        "/billing/me/refund-requests",
        headers=auth(token),
        json={"order_reference": reference, "reason": "The service did not meet my needs."},
    )
    assert requested.status_code == 200
    request_id = requested.json()["request"]["id"]
    duplicate = client.post(
        "/billing/me/refund-requests",
        headers=auth(token),
        json={"order_reference": reference, "reason": "A second duplicate request reason."},
    )
    assert duplicate.json()["request"]["id"] == request_id

    listed = client.get("/billing/admin/refund-requests", headers=auth("admin-secret"))
    assert request_id in {item["id"] for item in listed.json()["requests"]}
    approved = client.post(
        f"/billing/admin/refund-requests/{request_id}/decision",
        headers=auth("admin-secret"),
        json={"action": "approve", "note": "Eligible; refund through bank."},
    )
    assert approved.json()["result"]["status"] == "approved_pending_refund"
    completed = client.post(
        f"/billing/admin/refund-requests/{request_id}/decision",
        headers=auth("admin-secret"),
        json={"action": "complete", "note": "Bank transfer completed."},
    )
    assert completed.json()["result"]["status"] == "completed"
    mine = client.get("/billing/me/refund-requests", headers=auth(token)).json()["requests"]
    assert mine[0]["status"] == "completed"


def test_admin_credit_adjustment_is_bounded_and_audited(monkeypatch):
    _, token = new_account()
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    account = client.get("/billing/me", headers=auth(token)).json()["account"]
    user_id = account["user"]["id"]
    before = account["credit_minutes"]
    added = client.post(
        f"/billing/admin/users/{user_id}/credit-adjustment",
        headers=auth("admin-secret"),
        json={"minutes_delta": 25, "reason": "Support compensation"},
    )
    assert added.status_code == 200
    assert added.json()["event"]["balance_after"] == before + 25
    too_much_removed = client.post(
        f"/billing/admin/users/{user_id}/credit-adjustment",
        headers=auth("admin-secret"),
        json={"minutes_delta": -(before + 26), "reason": "Invalid reversal check"},
    )
    assert too_much_removed.status_code == 400
    current = client.get("/billing/me", headers=auth(token)).json()["account"]
    assert current["credit_minutes"] == before + 25
    events = client.get("/billing/admin/credit-events", headers=auth("admin-secret")).json()["events"]
    event = next(item for item in events if item["user_id"] == user_id)
    assert event["reason"] == "Support compensation"
    assert "actor" not in event


def test_admin_can_manage_profile_subscription_sessions_and_close_account(monkeypatch):
    email, token = new_account()
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    monkeypatch.setattr(config, "INSTAGRAM_ADMIN_TOKEN", "instagram-only-secret")
    assert client.get(
        "/billing/admin/credit-events",
        headers=auth("instagram-only-secret"),
    ).status_code == 401
    user_id = client.get("/billing/me", headers=auth(token)).json()["account"]["user"]["id"]
    changed_email = f"managed-{uuid.uuid4()}@example.com"

    updated = client.patch(
        f"/billing/admin/users/{user_id}",
        headers=auth("admin-secret"),
        json={
            "email": changed_email,
            "first_name": "Yönetilen",
            "last_name": "Kullanıcı",
            "phone": "+905551112233",
            "country_code": "DE",
            "preferred_language": "de",
            "email_verified": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["account"]["user"]["email"] == changed_email
    assert updated.json()["account"]["user"]["preferred_language"] == "de"
    assert client.get("/billing/me", headers=auth(token)).status_code == 401

    granted = client.post(
        f"/billing/admin/users/{user_id}/subscription",
        headers=auth("admin-secret"),
        json={"plan_code": "plus", "interval": "monthly", "duration_days": 45},
    )
    assert granted.status_code == 200
    assert granted.json()["account"]["plan"]["code"] == "plus"

    overview = client.get(
        "/billing/admin/overview?limit=250", headers=auth("admin-secret")
    ).json()
    managed = next(item for item in overview["users"] if item["id"] == user_id)
    assert managed["first_name"] == "Yönetilen"
    assert managed["preferred_language"] == "de"
    assert managed["plan_code"] == "plus"
    assert managed["subscription"]["status"] == "active"

    monkeypatch.setattr(config, "BILLING_PROTECTED_EMAILS", {changed_email.casefold()})
    protected_overview = client.get(
        "/billing/admin/overview?limit=250", headers=auth("admin-secret")
    ).json()
    protected_user = next(item for item in protected_overview["users"] if item["id"] == user_id)
    assert protected_user["is_protected"] is True
    protected_close = client.request(
        "DELETE",
        f"/billing/admin/users/{user_id}",
        headers=auth("admin-secret"),
        json={"confirmation_email": changed_email, "reason": "Koruma testi"},
    )
    assert protected_close.status_code == 400
    assert "kapatılamaz" in protected_close.json()["detail"]["message"]
    monkeypatch.setattr(config, "BILLING_PROTECTED_EMAILS", set())

    revoked = client.post(
        f"/billing/admin/users/{user_id}/revoke-sessions",
        headers=auth("admin-secret"),
    )
    assert revoked.status_code == 200
    assert revoked.json()["sessions_revoked"] is True

    wrong_confirmation = client.request(
        "DELETE",
        f"/billing/admin/users/{user_id}",
        headers=auth("admin-secret"),
        json={"confirmation_email": email, "reason": "Kullanıcı talebi"},
    )
    assert wrong_confirmation.status_code == 400

    closed = client.request(
        "DELETE",
        f"/billing/admin/users/{user_id}",
        headers=auth("admin-secret"),
        json={"confirmation_email": changed_email, "reason": "Kullanıcı talebi"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    after_close = client.get(
        "/billing/admin/overview?limit=250", headers=auth("admin-secret")
    ).json()
    assert user_id not in {item["id"] for item in after_close["users"]}

    events = client.get(
        "/billing/admin/account-events?limit=100", headers=auth("admin-secret")
    ).json()["events"]
    actions = {item["action"] for item in events if item["subject_user_id"] == user_id}
    assert {"user_updated", "subscription_changed", "sessions_revoked", "account_closed"} <= actions
    closed_events = [item for item in events if item["subject_user_id"] == user_id]
    assert all(item["subject_email"].endswith("@users.invalid") for item in closed_events)
    assert all(changed_email not in item["summary"] for item in closed_events)


def test_rewarded_ad_sessions_are_opt_in_capped_and_single_use(monkeypatch):
    _, token = new_account()
    monkeypatch.setattr(config, "REWARDED_ADS_ENABLED", True)
    monkeypatch.setattr(config, "REWARDED_AD_UNIT_PATH", "/1234567/lecturesift_rewarded")
    monkeypatch.setattr(config, "REWARDED_AD_MINUTES_PER_VIEW", 3)
    monkeypatch.setattr(config, "REWARDED_AD_DAILY_LIMIT_MINUTES", 6)

    state = client.get("/billing/rewarded-ads", headers=auth(token))
    assert state.status_code == 200
    assert state.json()["rewarded_ads"]["enabled"] is True

    issued = client.post("/billing/rewarded-ads/session", headers=auth(token)).json()["session"]
    claim_payload = {
        "session_id": issued["session_id"],
        "claim_token": issued["claim_token"],
    }
    claimed = client.post("/billing/rewarded-ads/claim", headers=auth(token), json=claim_payload)
    assert claimed.status_code == 200
    assert claimed.json()["minutes_added"] == 3
    duplicate = client.post("/billing/rewarded-ads/claim", headers=auth(token), json=claim_payload)
    assert duplicate.status_code == 400

    second = client.post("/billing/rewarded-ads/session", headers=auth(token)).json()["session"]
    claimed_again = client.post(
        "/billing/rewarded-ads/claim",
        headers=auth(token),
        json={"session_id": second["session_id"], "claim_token": second["claim_token"]},
    )
    assert claimed_again.status_code == 200
    assert claimed_again.json()["rewarded_ads"]["earned_today"] == 6
    assert claimed_again.json()["rewarded_ads"]["enabled"] is False
    assert client.post("/billing/rewarded-ads/session", headers=auth(token)).status_code == 400

    account = client.get("/billing/me", headers=auth(token)).json()["account"]
    assert account["credit_minutes"] >= 6
    exported = client.get("/billing/me/export", headers=auth(token)).json()["export"]
    assert len(exported["rewarded_ad_claims"]) == 2
    serialized = json.dumps(exported)
    assert "claim_token" not in serialized
    assert "token_hash" not in serialized


def test_admin_paginated_users_orders_activity_and_bulk_actions(monkeypatch):
    first_email, first_token = new_account()
    second_email, second_token = new_account()
    first_user = client.get("/billing/me", headers=auth(first_token)).json()["account"]["user"]
    second_user = client.get("/billing/me", headers=auth(second_token)).json()["account"]["user"]
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "Test Holder")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "support@example.com")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_NAME", "LectureSift Test")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_ADDRESS", "Test Address 1")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_COUNTRY", "TR")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_PHONE", "+905551112233")
    monkeypatch.setattr(config, "LEGAL_OPERATOR_EMAIL", "support@example.com")

    assert rollout_service.record_account_activity(
        first_user["id"], "login", "203.0.113.42", "Test Browser/1.0"
    )
    users = client.get(
        f"/billing/admin/users?search={first_email}&page=1&page_size=10",
        headers=auth("admin-secret"),
    )
    assert users.status_code == 200
    assert users.json()["pagination"]["total"] == 1
    listed_user = users.json()["users"][0]
    assert listed_user["last_activity"]["ip_network"] == "203.0.113.0/24"
    assert listed_user["last_activity"]["ip_fingerprint"]
    assert "203.0.113.42" not in json.dumps(listed_user)

    activity = client.get(
        f"/billing/admin/users/{first_user['id']}/activity?limit=10",
        headers=auth("admin-secret"),
    )
    assert activity.status_code == 200
    activity_body = activity.json()
    assert activity_body["privacy"]["full_ip_stored"] is False
    assert activity_body["privacy"]["retention_days"] >= 30
    assert activity_body["activity"][0]["ip_network"] == "203.0.113.0/24"
    assert "203.0.113.42" not in json.dumps(activity_body)

    bulk = client.post(
        "/billing/admin/users/bulk-action",
        headers=auth("admin-secret"),
        json={
            "user_ids": [first_user["id"], second_user["id"]],
            "action": "credit",
            "minutes_delta": 7,
            "reason": "Toplu destek telafisi",
        },
    )
    assert bulk.status_code == 200
    assert bulk.json()["succeeded"] == 2

    order = client.post(
        "/billing/manual-transfer/orders",
        headers=auth(first_token),
        json={"plan_code": "plus", "interval": "monthly", "terms_accepted": True, "early_performance_requested": True},
    ).json()["order"]
    orders = client.get(
        f"/billing/admin/orders?search={order['reference']}&provider=bank_transfer&page_size=10",
        headers=auth("admin-secret"),
    )
    assert orders.status_code == 200
    listed_order = orders.json()["orders"][0]
    assert listed_order["payment_method"] == "bank_transfer"
    assert listed_order["user"]["email"] == first_email
    assert listed_order["created_at"] and listed_order["updated_at"]

    protected_order = create_payment_order(
        first_user["id"], IYZICO_BANK_TRANSFER_PROVIDER, "plus", "monthly", "TRY"
    )
    protected_intent = create_payment_order(
        first_user["id"], IYZICO_BANK_TRANSFER_INTENT_PROVIDER, "plus", "monthly", "TRY"
    )
    card_order = create_payment_order(
        first_user["id"], IYZICO_CARD_PROVIDER, "plus", "monthly", "TRY"
    )
    card_intent = create_payment_order(
        first_user["id"], IYZICO_CARD_INTENT_PROVIDER, "plus", "monthly", "TRY"
    )
    legacy_order = create_payment_order(
        first_user["id"], IYZICO_LEGACY_PROVIDER, "plus", "monthly", "TRY"
    )
    manual_only = client.get(
        f"/billing/admin/orders?search={first_email}&provider=manual_bank_transfer&page_size=10",
        headers=auth("admin-secret"),
    )
    assert manual_only.status_code == 200
    assert manual_only.json()["pagination"]["total"] == 1
    assert manual_only.json()["orders"][0]["provider"] == "bank_transfer"

    for protected_reference in (protected_order["reference"], protected_intent["reference"]):
        protected_only = client.get(
            f"/billing/admin/orders?search={protected_reference}&provider=iyzico_bank_transfer&page_size=10",
            headers=auth("admin-secret"),
        )
        assert protected_only.status_code == 200
        assert protected_only.json()["pagination"]["total"] == 1
        assert protected_only.json()["orders"][0]["provider"] == "iyzico"
        assert protected_only.json()["orders"][0]["payment_method"] == "bank_transfer"

    protected_page = client.get(
        f"/billing/admin/orders?search={first_email}&provider=iyzico_bank_transfer&page_size=10",
        headers=auth("admin-secret"),
    ).json()
    assert protected_page["pagination"]["total"] == 2
    assert {item["reference"] for item in protected_page["orders"]} == {
        protected_order["reference"], protected_intent["reference"]
    }

    card_only = client.get(
        f"/billing/admin/orders?search={first_email}&provider=iyzico_card&page_size=10",
        headers=auth("admin-secret"),
    )
    assert card_only.status_code == 200
    assert card_only.json()["pagination"]["total"] == 2
    assert {item["reference"] for item in card_only.json()["orders"]} == {
        card_order["reference"], card_intent["reference"]
    }
    assert all(item["provider"] == "iyzico" for item in card_only.json()["orders"])
    assert all(item["payment_method"] == "card" for item in card_only.json()["orders"])

    for legacy_filter in ("iyzico", "iyzico_legacy"):
        legacy_only = client.get(
            f"/billing/admin/orders?search={first_email}&provider={legacy_filter}&page_size=10",
            headers=auth("admin-secret"),
        )
        assert legacy_only.status_code == 200
        assert legacy_only.json()["pagination"]["total"] == 1
        assert legacy_only.json()["orders"][0]["reference"] == legacy_order["reference"]
        assert legacy_only.json()["orders"][0]["payment_method"] == "unknown"

    refused = client.post(
        "/billing/admin/users/bulk-action",
        headers=auth("admin-secret"),
        json={"user_ids": [second_user["id"]], "action": "delete", "confirmation": "hayır", "reason": "Test hesabı"},
    )
    assert refused.status_code == 400
    deleted = client.post(
        "/billing/admin/users/bulk-action",
        headers=auth("admin-secret"),
        json={"user_ids": [second_user["id"]], "action": "delete", "confirmation": "SİL", "reason": "Test hesabı"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["succeeded"] == 1
    assert client.get("/billing/me", headers=auth(second_token)).status_code == 401

def test_eta_learns_from_completed_jobs():
    baseline = rollout_service.estimate_eta_seconds(10, 0)
    rollout_service.record_runtime(f"eta-{uuid.uuid4()}", 10, 200, 1000)
    learned = rollout_service.estimate_eta_seconds(10, 0)
    assert baseline >= 45
    assert 150 <= learned <= 350


def test_eta_accounts_for_job_type_source_and_detailed_profile():
    standard = rollout_service.estimate_eta_seconds(20, 0)
    detailed = rollout_service.estimate_eta_seconds(20, 0, summary_style="detailed")
    document = rollout_service.estimate_eta_seconds(20, 0, source_kind="document")
    mp3 = rollout_service.estimate_eta_seconds(20, 0, job_type="audio_export")
    download = rollout_service.estimate_eta_seconds(20, 0, job_type="download_video")

    assert detailed > standard
    assert document < standard
    assert mp3 < document
    assert download <= mp3


def test_job_store_persists_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs_module, "REDIS_URL", "")
    first = jobs_module.JobStore()
    job_dir = tmp_path / "job-one"
    job_dir.mkdir()
    first.create("job-one", job_dir, {"output_language": "tr"})
    first.update("job-one", status="working", percent=44, stage="transcription")
    second = jobs_module.JobStore()
    restored = second.get("job-one")
    assert restored["status"] == "working"
    assert restored["percent"] == 44
    assert second.recoverable()[0]["job_id"] == "job-one"


def test_smart_notes_question_cards_and_versionless_zip(tmp_path: Path, monkeypatch):
    install_pipeline_enhancements()
    archive_calls = []
    make_archive = exports_module.shutil.make_archive

    def tracked_archive(*args, **kwargs):
        archive_calls.append((args, kwargs))
        return make_archive(*args, **kwargs)

    monkeypatch.setattr(exports_module.shutil, "make_archive", tracked_archive)
    result = {
        "title": "Test Ders",
        "summary": "Kapsamlı bir özet.",
        "key_points": ["Ana nokta"],
        "important_terms": [{"term": "Hipnoz", "definition": "Odaklanmış dikkat."}],
        "notes": [{"heading": "Temel", "content": "Açıklama", "bullets": ["Örnek"]}],
        "exam_focus": [],
        "quiz": [],
        "flashcards": [{"front": "Hipnoz", "back": "Odaklanmış dikkat durumudur."}],
        "transcript_original": "Hipnoz odaklanmış dikkattir.",
        "transcript_translated": "",
        "slides": [],
        "options": {"output_formats": ["pdf"], "output_language": "tr"},
    }
    artifacts, zip_path = pipeline.build_artifacts(tmp_path, result, tmp_path / "slides")
    names = {item["file"] for item in artifacts}
    assert "Akilli_Notlar.pdf" in names
    assert "Ders_Notlari.pdf" not in names
    assert zip_path.name == "LectureSift_Study_Pack.zip"
    assert len(archive_calls) == 1
    parsed = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert parsed["flashcards"][0]["front"].endswith("?")
    with zipfile.ZipFile(zip_path) as archive:
        assert "Akilli_Notlar.pdf" in archive.namelist()
        assert all("V4" not in name for name in archive.namelist())


def test_rollout_frontend_and_blueprint_contracts():
    rollout = Path("frontend/rollout.js").read_text(encoding="utf-8")
    css = Path("frontend/rollout.css").read_text(encoding="utf-8")
    blueprint = Path("render.yaml").read_text(encoding="utf-8")
    assert "/billing/guest-session" in rollout
    assert "/billing/me/email-change" in rollout
    assert "Tahmini kalan" in rollout
    assert ".version-pill{display:none" in css
    assert "type: keyvalue" in blueprint
    assert "type: worker" in blueprint
    assert "journal-snapshot" in blueprint
    assert "S3_BUCKET" in blueprint
    assert "LECTURESIFT_REQUIRE_DURABLE_PROCESSING" in blueprint
