from copy import deepcopy
from dataclasses import asdict
import uuid

import pytest

from lecturesift import billing_service as billing
from lecturesift.billing_service import BillingError
from lecturesift.jobs import JOBS
import lecturesift.pipeline as pipeline


@pytest.mark.parametrize('document_mode', [False, True])
@pytest.mark.parametrize(('required_minutes', 'message'), [
    (561, 'hesabında 120 dakika kaldı'),
    (61, 'free planında tek iş sınırı 60 dakikadır'),
])
def test_document_limit_explains_usage_units_without_changing_media_limits_or_balance(
    monkeypatch, document_mode, required_minutes, message,
):
    status = {'plan': asdict(billing.PLAN_BY_CODE['free']), 'remaining_minutes': 120, 'used_minutes': 0}
    original = deepcopy(status)
    monkeypatch.setattr(billing, 'require_job_entitlement', lambda _user_id: status)
    monkeypatch.setattr(billing, 'record_usage', lambda *a, **k: pytest.fail('rejection charged usage'))

    with pytest.raises(BillingError, match=message) as caught:
        billing.require_duration_entitlement('synthetic-user', required_minutes * 60,
            document_mode=document_mode)

    explanation = str(caught.value)
    assert caught.value.notification_context == {
        'required_minutes': required_minutes, 'remaining_minutes': 120, 'max_minutes_per_job': 60,
    }
    if document_mode:
        assert f'Bu belge için {required_minutes} dakika kullanım hakkı gerekiyor' in explanation
        assert ('Daha kısa bir belge yükle' if required_minutes == 561 else 'Belgeyi böl') in explanation
    else:
        assert explanation.startswith(
            f'Bu kaynak yaklaşık {required_minutes} dakika' if required_minutes == 561
            else f'Bu iş yaklaşık {required_minutes} dakika'
        )
        assert 'kullanım hakkı gerekiyor' not in explanation
    assert status == original
    assert billing.require_duration_entitlement('synthetic-user', 60 * 60,
        document_mode=document_mode) == original


def test_actual_document_quota_uses_final_ocr_values(tmp_path, monkeypatch):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"final-ocr-source")
    reservations = []
    checks = []
    monkeypatch.setattr(pipeline, "is_guest_user", lambda user_id: user_id == "guest-user")
    monkeypatch.setattr(
        pipeline,
        "reserve_guest_job",
        lambda user_id, job_id, minutes: reservations.append((user_id, job_id, minutes)),
    )
    monkeypatch.setattr(
        pipeline,
        "require_duration_entitlement",
        lambda user_id, duration, **kwargs: checks.append((user_id, duration, kwargs)) or {},
    )

    pipeline._enforce_actual_document_entitlement(
        "document-job",
        {"billing_user_id": "guest-user"},
        [source],
        {"credit_seconds": 240, "pages": 12, "ocr_pages": 9},
    )

    assert reservations == [("guest-user", "document-job", 4.0)]
    assert checks == [
        (
            "guest-user",
            240.0,
            {
                "source_file_count": 1,
                "source_size_bytes": len(b"final-ocr-source"),
                "document_mode": True,
                "document_pages": 12,
                "ocr_pages": 9,
            },
        )
    ]


def test_actual_document_quota_rejects_before_study_generation(tmp_path, monkeypatch):
    source = tmp_path / "oversized.txt"
    source.write_text("lecture notes", encoding="utf-8")
    job_id = "actual-document-quota"
    options = {
        "billing_user_id": "registered-user",
        "source_language": "en",
        "output_language": "en",
        "summary_style": "standard",
        "quiz_count": 0,
        "flashcard_count": 0,
        "document_mode": True,
    }
    JOBS.create(job_id, tmp_path / job_id, options)
    generated = []
    monkeypatch.setattr(
        pipeline,
        "extract_documents",
        lambda *args, **kwargs: {
            "text": "extracted",
            "documents": [],
            "characters": 9,
            "words": 2,
            "credit_seconds": 3_660,
            "credit_minutes": 61,
            "pages": 2,
            "ocr_pages": 2,
            "ocr_required": True,
            "ocr_used": True,
        },
    )
    monkeypatch.setattr(
        pipeline,
        "_enforce_actual_document_entitlement",
        lambda *args, **kwargs: (_ for _ in ()).throw(BillingError("Gerçek OCR kotayı aştı.")),
    )
    monkeypatch.setattr(
        pipeline,
        "_make_selected_study_pack",
        lambda *args, **kwargs: generated.append(True),
    )

    pipeline._process_job(job_id, [source], options)

    finished = JOBS.get(job_id)
    assert generated == []
    assert finished["status"] == "error"
    assert finished["error_code"] == "LS-BILL-10"
    assert finished["error"] == "Gerçek OCR kotayı aştı."


def test_synthetic_guest_eighteen_minute_document_is_accepted_without_consuming_usage(tmp_path):
    from lecturesift import rollout_service

    created = rollout_service.create_or_resume_guest(f"document-trial-{uuid.uuid4()}")
    user_id = created["account"]["user"]["id"]
    source = tmp_path / "lesson.pdf"
    source.write_bytes(b"synthetic-native-document")
    job_id = f"guest-eighteen-minutes-{uuid.uuid4()}"

    pipeline._enforce_actual_document_entitlement(
        job_id,
        {"billing_user_id": user_id},
        [source],
        {"credit_seconds": 18 * 60, "pages": 18, "ocr_pages": 0},
    )

    trial = rollout_service.guest_trial_status(user_id)
    assert trial["used"] is True
    assert trial["job_id"] == job_id
    assert trial["max_minutes"] == 20
    # Reservation is separate from the eventual successful-usage charge.
    assert billing.account_status(user_id)["used_minutes"] == 0


@pytest.mark.parametrize("document_mode", [False, True])
def test_registered_free_accepts_sixty_minutes_and_rejects_sixty_one(document_mode):
    created = billing.register_user(
        f"free-duration-{uuid.uuid4()}@example.com",
        "Strong-free-duration-password1", "Duration", "Test",
    )
    billing.verify_email(created["verification_token"])
    user_id = created["user"]["id"]
    accepted = billing.require_duration_entitlement(user_id, 60 * 60, document_mode=document_mode)
    assert accepted["remaining_minutes"] == 120
    with pytest.raises(BillingError, match="tek iş sınırı 60 dakikadır"):
        billing.require_duration_entitlement(user_id, 61 * 60, document_mode=document_mode)
    unchanged = billing.account_status(user_id)
    assert unchanged["used_minutes"] == 0
    assert unchanged["remaining_minutes"] == 120


def test_guest_rejected_twenty_one_minute_document_preserves_twenty_minute_trial(tmp_path):
    from lecturesift import rollout_service

    created = rollout_service.create_or_resume_guest(f"document-boundary-{uuid.uuid4()}")
    user_id = created["account"]["user"]["id"]
    source = tmp_path / "lesson.pdf"
    source.write_bytes(b"synthetic-native-document")
    options = {"billing_user_id": user_id}
    with pytest.raises(BillingError, match="hesabında 20 dakika kaldı"):
        pipeline._enforce_actual_document_entitlement(
            f"too-long-{uuid.uuid4()}", options, [source],
            {"credit_seconds": 21 * 60, "pages": 20, "ocr_pages": 0},
        )
    trial = rollout_service.guest_trial_status(user_id)
    assert trial["used"] is False and trial["remaining_minutes"] == 20
    accepted_job = f"fits-{uuid.uuid4()}"
    pipeline._enforce_actual_document_entitlement(
        accepted_job, options, [source],
        {"credit_seconds": 20 * 60, "pages": 20, "ocr_pages": 0},
    )
    assert rollout_service.guest_trial_status(user_id)["job_id"] == accepted_job
    assert billing.account_status(user_id)["used_minutes"] == 0
