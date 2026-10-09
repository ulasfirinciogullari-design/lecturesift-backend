from copy import deepcopy
from dataclasses import asdict

import pytest

from lecturesift import billing_service as billing
from lecturesift.billing_service import BillingError
from lecturesift.jobs import JOBS
import lecturesift.pipeline as pipeline


@pytest.mark.parametrize('document_mode', [False, True])
@pytest.mark.parametrize(('required_minutes', 'message'), [
    (561, 'hesabında 60 dakika kaldı'),
    (31, 'free planında tek iş sınırı 30 dakikadır'),
])
def test_document_limit_explains_usage_units_without_changing_media_limits_or_balance(
    monkeypatch, document_mode, required_minutes, message,
):
    status = {'plan': asdict(billing.PLAN_BY_CODE['free']), 'remaining_minutes': 60, 'used_minutes': 0}
    original = deepcopy(status)
    monkeypatch.setattr(billing, 'require_job_entitlement', lambda _user_id: status)
    monkeypatch.setattr(billing, 'record_usage', lambda *a, **k: pytest.fail('rejection charged usage'))

    with pytest.raises(BillingError, match=message) as caught:
        billing.require_duration_entitlement('synthetic-user', required_minutes * 60,
            document_mode=document_mode)

    explanation = str(caught.value)
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
    assert billing.require_duration_entitlement('synthetic-user', 30 * 60,
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
