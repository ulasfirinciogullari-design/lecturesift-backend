"""Precise timestamps / speaker detection: Pro-and-up only, billed at 4x minutes."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import lecturesift.app as app_module
import lecturesift.billing_service as billing_service
from lecturesift.app import app
from lecturesift.billing import PRECISE_TRANSCRIPT_PLAN_CODES, PRECISE_TRANSCRIPT_USAGE_MULTIPLIER
from lecturesift.billing_service import (
    BillingError,
    PRECISE_TRANSCRIPT_PLAN_MESSAGE,
    plan_allows_precise_transcript,
    record_usage,
    register_user,
    require_duration_entitlement,
    require_precise_transcript_plan,
    usage_multiplier_for,
    validate_job_features,
    verify_email,
)


def _registered() -> tuple[str, str]:
    registration = register_user(
        f"precise-{uuid.uuid4()}@example.com", "Strong-test-password1", "Precise", "Test", country_code="TR"
    )
    verified = verify_email(registration["verification_token"])
    return registration["user"]["id"], verified["token"]


def _subscribe(user_id: str, plan_code: str) -> None:
    order = billing_service.create_payment_order(user_id, "test-provider", plan_code, "monthly", "TRY")
    billing_service.complete_payment_order(
        order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"]
    )
    assert billing_service.account_status(user_id)["plan"]["code"] == plan_code


def _features(user_id: str, **flags) -> dict:
    return validate_job_features(
        user_id, quiz_count=0, flashcard_count=0, output_formats=["pdf"], summary_style="detailed", **flags
    )


def test_multiplier_and_plan_set_are_the_documented_pro_and_up_terms():
    assert PRECISE_TRANSCRIPT_USAGE_MULTIPLIER == 4
    assert PRECISE_TRANSCRIPT_PLAN_CODES == {"plus", "pro", "max", "business"}
    assert usage_multiplier_for({}) == usage_multiplier_for(None) == 1
    assert usage_multiplier_for({"transcript_timestamps": True}) == 4
    assert usage_multiplier_for({"speaker_detection": True}) == 4
    assert usage_multiplier_for({"transcript_timestamps": False, "speaker_detection": False}) == 1
    for code in ("free", "guest", "credit", "lite", "test", "ad_free"):
        assert plan_allows_precise_transcript(code) is False, code
        with pytest.raises(BillingError, match="Pro planına"):
            require_precise_transcript_plan(code, transcript_timestamps=True)
        with pytest.raises(BillingError):
            require_precise_transcript_plan(code, speaker_detection=True)
        require_precise_transcript_plan(code)  # Plain transcripts stay available.
    for code in ("plus", "pro", "max", "business"):
        assert plan_allows_precise_transcript(code) is True, code
        require_precise_transcript_plan(code, transcript_timestamps=True, speaker_detection=True)


def test_free_student_and_minute_pack_accounts_are_refused_precise_transcripts():
    user_id, _ = _registered()
    assert _features(user_id)["job_plan"]["code"] == "free"
    for flags in ({"transcript_timestamps": True}, {"speaker_detection": True},
                  {"transcript_timestamps": True, "speaker_detection": True}):
        with pytest.raises(BillingError) as refused:
            _features(user_id, **flags)
        assert str(refused.value) == PRECISE_TRANSCRIPT_PLAN_MESSAGE

    student_id, _ = _registered()
    _subscribe(student_id, "lite")
    with pytest.raises(BillingError, match="Pro planına"):
        _features(student_id, transcript_timestamps=True)
    assert _features(student_id)["job_plan"]["code"] == "lite"

    pack_id, _ = _registered()
    order = billing_service.create_payment_order(pack_id, "test-provider", "credit", "one_time", "TRY")
    billing_service.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"])
    assert _features(pack_id)["job_plan"]["code"] == "credit"
    with pytest.raises(BillingError, match="Pro planına"):
        _features(pack_id, speaker_detection=True)


def test_pro_and_hidden_plus_accounts_keep_precise_transcripts():
    for plan_code in ("pro", "plus"):
        user_id, _ = _registered()
        _subscribe(user_id, plan_code)
        entitlement = _features(user_id, transcript_timestamps=True, speaker_detection=True)
        assert entitlement["job_plan"]["code"] == plan_code


def test_precise_jobs_record_four_times_the_media_minutes():
    user_id, _ = _registered()
    _subscribe(user_id, "pro")
    assert billing_service.account_status(user_id)["remaining_minutes"] == 1500

    record_usage(user_id, f"plain-{uuid.uuid4()}", 10 * 60)
    assert billing_service.account_status(user_id)["used_minutes"] == 10

    precise = {"transcript_timestamps": True, "speaker_detection": False}
    record_usage(user_id, f"precise-{uuid.uuid4()}", 10 * 60, usage_multiplier=usage_multiplier_for(precise))
    status = billing_service.account_status(user_id)
    assert status["used_minutes"] == 50
    assert status["remaining_minutes"] == 1450

    # Retried completions stay idempotent on the job id, multiplier included.
    job_id = f"retry-{uuid.uuid4()}"
    record_usage(user_id, job_id, 7 * 60, usage_multiplier=4)
    record_usage(user_id, job_id, 7 * 60, usage_multiplier=4)
    assert billing_service.account_status(user_id)["used_minutes"] == 78


def test_quota_precheck_counts_precise_minutes_four_times_but_job_length_once():
    user_id, _ = _registered()
    _subscribe(user_id, "pro")
    precise = usage_multiplier_for({"speaker_detection": True})

    # 300 source minutes count as 1,200 of the 1,500-minute allowance.
    require_duration_entitlement(user_id, 300 * 60, usage_multiplier=precise)
    record_usage(user_id, f"used-{uuid.uuid4()}", 400 * 60)
    assert billing_service.account_status(user_id)["remaining_minutes"] == 1100
    with pytest.raises(BillingError) as refused:
        require_duration_entitlement(user_id, 300 * 60, usage_multiplier=precise)
    assert "1200 dakika sayılır" in str(refused.value)
    assert refused.value.notification_context["required_minutes"] == 1200
    # The same source without the diarization options still fits.
    require_duration_entitlement(user_id, 300 * 60)

    # The per-job length limit applies to the real source length (Pro: 360 min).
    with pytest.raises(BillingError, match="tek iş sınırı 360"):
        require_duration_entitlement(user_id, 370 * 60)
    fresh_id, _ = _registered()
    _subscribe(fresh_id, "pro")
    require_duration_entitlement(fresh_id, 360 * 60, usage_multiplier=precise)
    with pytest.raises(BillingError, match="tek iş sınırı 360"):
        require_duration_entitlement(fresh_id, 361 * 60, usage_multiplier=precise)


def test_job_creation_refuses_precise_transcripts_below_pro_but_not_for_documents(tmp_path, monkeypatch):
    created = []

    def job_path(job_id):
        path = tmp_path / job_id
        path.mkdir()
        return path

    monkeypatch.setattr(app_module, "_require_ai_provider", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "_job_path", job_path)
    monkeypatch.setattr(app_module, "JOBS", SimpleNamespace(
        cleanup_expired=lambda: None, create=lambda job_id, job_dir, options, **kwargs: created.append(options),
    ))
    monkeypatch.setattr(app_module, "threading", SimpleNamespace(
        Thread=lambda **kwargs: SimpleNamespace(start=lambda: None),
    ))
    client = TestClient(app)
    user_id, token = _registered()
    headers = {"Authorization": f"Bearer {token}"}
    data = {"quiz_count": "0", "flashcard_count": "0", "output_formats": "pdf",
            "include_transcript": "true", "transcript_timestamps": "true", "speaker_detection": "true"}

    refused = client.post("/jobs", headers=headers, data=data,
                          files={"files": ("lecture.mp4", b"synthetic-media", "video/mp4")})
    assert refused.status_code == 402, refused.text
    assert refused.json()["detail"] == {"code": "LS-BILL-10", "message": PRECISE_TRANSCRIPT_PLAN_MESSAGE}
    assert created == []

    # A document never transcribes, so the flags are dropped instead of refused.
    document = client.post("/jobs", headers=headers, data=data,
                           files={"files": ("notes.pdf", b"%PDF-synthetic", "application/pdf")})
    assert document.status_code == 200, document.text
    assert created[-1]["transcript_timestamps"] is False
    assert created[-1]["speaker_detection"] is False

    _subscribe(user_id, "pro")
    allowed = client.post("/jobs", headers=headers, data=data,
                          files={"files": ("lecture.mp4", b"synthetic-media", "video/mp4")})
    assert allowed.status_code == 200, allowed.text
    assert created[-1]["transcript_timestamps"] is True
    assert created[-1]["speaker_detection"] is True
