"""Guest boundary checks use tiny sources and synthetic size metadata."""

from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest

from main import app
from lecturesift import app as api
from lecturesift import billing_service as billing, document_samples, pipeline, rollout_service, tasks


@pytest.fixture
def guest():
    return rollout_service.create_or_resume_guest(f"guest-boundary-{uuid.uuid4()}")


def test_public_guest_limits_match_resumed_account_without_changing_other_plans(guest):
    catalog = TestClient(app).get("/billing/plans").json()
    limits = catalog["guest_trial_limits"]
    assert catalog["guest_trial_minutes"] == 30
    assert limits == guest["account"]["plan"]["entitlements"]["limits"]
    assert {key: limits[key] for key in (
        "max_minutes_per_job", "max_files_per_job", "max_media_upload_mb",
        "max_document_upload_mb", "max_document_pages", "max_ocr_pages",
    )} == {
        "max_minutes_per_job": 30, "max_files_per_job": 1, "max_media_upload_mb": 150,
        "max_document_upload_mb": 35, "max_document_pages": 40, "max_ocr_pages": 8,
    }
    assert all(plan["code"] != "guest" for plan in catalog["plans"])
    assert next(plan for plan in catalog["plans"] if plan["code"] == "free")["minutes"] == 120
    sample = document_samples.sample_request(
        {**guest["account"], "job_plan": guest["account"]["plan"]}, 1, 200, guest["trial"],
    )
    assert sample == {"start_page": 1, "max_words": 2000, "max_pages": 10, "max_ocr_pages": 5}


@pytest.mark.parametrize("exceeded", ["duration", "file_count", "file_size"])
def test_media_boundary_rejection_preserves_trial_and_valid_retry_reserves_once(guest, exceeded):
    user_id = guest["account"]["user"]["id"]
    arguments = {
        "duration": 30 * 60, "source_file_count": 1, "source_size_bytes": 150 * 1024 * 1024,
        "document_mode": False, "document_pages": 0, "ocr_pages": 0,
    }
    field = {
        "duration": "duration", "file_count": "source_file_count", "file_size": "source_size_bytes",
    }[exceeded]
    rejected = {**arguments, field: arguments[field] + 1}
    with pytest.raises(billing.BillingError):
        tasks._enforce_uploaded_job_quota(user_id, f"rejected-{uuid.uuid4()}", **rejected)
    assert rollout_service.guest_trial_status(user_id)["remaining_minutes"] == 30
    assert billing.account_status(user_id)["used_minutes"] == 0

    accepted_job = f"accepted-{uuid.uuid4()}"
    tasks._enforce_uploaded_job_quota(user_id, accepted_job, **arguments)
    tasks._enforce_uploaded_job_quota(user_id, accepted_job, **arguments)
    with pytest.raises(billing.BillingError, match="daha önce"):
        tasks._enforce_uploaded_job_quota(user_id, f"second-{uuid.uuid4()}", **arguments)
    assert rollout_service.guest_trial_status(user_id)["job_id"] == accepted_job
    assert billing.account_status(user_id)["used_minutes"] == 0


@pytest.mark.parametrize("sample", [False, True])
def test_reserved_guest_is_rejected_before_creating_or_processing_another_pdf(guest, monkeypatch, sample):
    user_id = guest["account"]["user"]["id"]
    reserved_job = f"in-progress-{uuid.uuid4()}"
    rollout_service.reserve_guest_job(user_id, reserved_job, 1)
    def forbidden(*args, **kwargs):
        pytest.fail("An already reserved guest reached job storage or processing")
    monkeypatch.setattr(api, "_require_ai_provider", forbidden)
    monkeypatch.setattr(api, "_job_path", forbidden)
    monkeypatch.setattr(api, "process_job", forbidden)
    response = TestClient(app).post(
        "/jobs", headers={"Authorization": "Bearer " + guest["token"]},
        data={"quiz_count": "0", "flashcard_count": "0", "summary_style": "detailed",
              "output_formats": "pdf", "document_sample": str(sample).lower()},
        files={"files": ("lesson.pdf", b"%PDF-synthetic-header", "application/pdf")},
    )
    assert response.status_code == 402, response.text
    assert response.json()["detail"]["code"] == "LS-GUEST-05"
    assert rollout_service.guest_trial_status(user_id)["job_id"] == reserved_job
    assert billing.account_status(user_id)["used_minutes"] == 0


@pytest.mark.parametrize("exceeded", ["duration", "file_size", "pages", "ocr_pages"])
def test_actual_document_boundaries_preserve_trial_before_accepting_exact_limit(guest, exceeded):
    user_id = guest["account"]["user"]["id"]
    data = {"credit_seconds": 30 * 60, "pages": 40, "ocr_pages": 8}
    bad_data = dict(data)
    if exceeded != "file_size":
        bad_data[{"duration": "credit_seconds", "pages": "pages", "ocr_pages": "ocr_pages"}[exceeded]] += 1
    source_size = 35 * 1024 * 1024
    def source(size):
        return SimpleNamespace(exists=lambda: True, stat=lambda: SimpleNamespace(st_size=size))

    with pytest.raises(billing.BillingError):
        pipeline._enforce_actual_document_entitlement(
            f"rejected-{uuid.uuid4()}", {"billing_user_id": user_id},
            [source(source_size + int(exceeded == "file_size"))], bad_data,
        )
    assert rollout_service.guest_trial_status(user_id)["used"] is False
    assert billing.account_status(user_id)["used_minutes"] == 0

    accepted_job = f"accepted-{uuid.uuid4()}"
    pipeline._enforce_actual_document_entitlement(
        accepted_job, {"billing_user_id": user_id}, [source(source_size)], data,
    )
    assert rollout_service.guest_trial_status(user_id)["job_id"] == accepted_job
    assert billing.account_status(user_id)["used_minutes"] == 0
