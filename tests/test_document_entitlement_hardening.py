from lecturesift.billing_service import BillingError
from lecturesift.jobs import JOBS
import lecturesift.pipeline as pipeline


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
