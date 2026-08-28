import json
import re
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lecturesift.jobs as jobs_module
import lecturesift.rollout_service as rollout_service
from lecturesift import config, pipeline
from lecturesift.billing_service import register_user, verify_email
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


def test_guest_trial_is_five_minutes_resumable_and_single_job():
    device = f"device-{uuid.uuid4()}"
    first = client.post("/billing/guest-session", json={"device_id": device})
    assert first.status_code == 200
    body = first.json()
    assert body["guest"] is True
    assert body["account"]["plan"]["code"] == "guest"
    assert body["account"]["remaining_minutes"] == 5

    resumed = client.post("/billing/guest-session", json={"device_id": device})
    assert resumed.status_code == 200
    assert resumed.json()["resumed"] is True
    assert resumed.json()["account"]["user"]["id"] == body["account"]["user"]["id"]

    user_id = body["account"]["user"]["id"]
    first_job = f"job-{uuid.uuid4()}"
    rollout_service.reserve_guest_job(user_id, first_job, 4.9)
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


def test_manual_order_admin_rejection_and_instagram_approval(monkeypatch):
    _, token = new_account()
    monkeypatch.setattr(config, "BILLING_BANK_IBAN", "TR000000000000000000000000")
    monkeypatch.setattr(config, "BILLING_BANK_ACCOUNT_HOLDER", "Test Holder")
    monkeypatch.setattr(config, "BILLING_SUPPORT_EMAIL", "support@example.com")
    monkeypatch.setattr(config, "BILLING_ADMIN_TOKEN", "admin-secret")

    order = client.post(
        "/billing/manual-transfer/orders",
        headers=auth(token),
        json={"plan_code": "plus", "interval": "annual"},
    )
    assert order.status_code == 200
    reference = order.json()["order"]["reference"]
    assert order.json()["order"]["amount_minor"] == 699000

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


def test_eta_learns_from_completed_jobs():
    baseline = rollout_service.estimate_eta_seconds(10, 0)
    rollout_service.record_runtime(f"eta-{uuid.uuid4()}", 10, 200, 1000)
    learned = rollout_service.estimate_eta_seconds(10, 0)
    assert baseline >= 45
    assert 150 <= learned <= 350


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


def test_smart_notes_question_cards_and_versionless_zip(tmp_path: Path):
    install_pipeline_enhancements()
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
