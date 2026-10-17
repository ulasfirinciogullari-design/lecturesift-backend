"""Sample quota, dispatcher and provenance contracts; run in GitHub Actions."""
from copy import deepcopy
import math
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lecturesift import app as api
from lecturesift import config
from lecturesift import document_samples as samples
from lecturesift import durable_runtime, tasks, pipeline, rollout_service, exports
from lecturesift.billing_service import BillingError
from lecturesift.errors import LectureSiftError


def entitlement(remaining=60, per_job=30, pages=50, ocr_pages=5):
    return {"remaining_minutes": remaining, "download_enabled": False,
            "plan": {"history_days": 1},
            "job_plan": {"entitlements": {"limits": {
                "max_minutes_per_job": per_job, "max_document_pages": pages,
                "max_files_per_job": 3, "max_media_upload_mb": 50,
                "max_document_upload_mb": 25, "max_ocr_pages": ocr_pages,
            }}}}


@pytest.mark.parametrize("remaining,per_job,trial,rate,expected", [
    (60, 30, None, 200, 2000),
    (2.9, 30, None, 200, 400),
    (None, 2000, None, 200, 2000),
    (60, 30, {"remaining_minutes": 1}, 200, 200),
    (60, 1, None, 100, 100),
])
def test_budget_comes_from_real_remaining_and_whole_minute_billing(remaining, per_job, trial, rate, expected):
    assert samples.sample_request(entitlement(remaining, per_job), 3, rate, trial) == {
        "start_page": 3, "max_words": expected, "max_pages": 10, "max_ocr_pages": 5,
    }


def test_consumed_guest_or_fractional_allowance_cannot_start_sample():
    with pytest.raises(BillingError):
        samples.sample_request(entitlement(), 1, 200, {"remaining_minutes": 0})
    with pytest.raises(BillingError):
        samples.sample_request(entitlement(0.9), 1, 200)
    with pytest.raises(LectureSiftError) as error:
        samples.sample_request(entitlement(), 0, 200)
    assert error.value.code == "LS-DOC-15"


@pytest.mark.parametrize("plan_cap,server_cap,enabled,expected", [
    (30, 150, True, 5), (2, 150, True, 2), (30, 3, True, 3),
    (0, 150, True, 0), (5, 150, False, 0),
])
def test_sample_ocr_budget_respects_account_server_and_feature_limits(monkeypatch, plan_cap, server_cap, enabled, expected):
    monkeypatch.setattr(config, "OCR_MAX_PAGES", server_cap)
    monkeypatch.setattr(config, "OCR_ENABLED", enabled)
    selection = samples.sample_request(entitlement(ocr_pages=plan_cap), 1, 200)
    assert selection["max_ocr_pages"] == expected


def test_missing_legacy_ocr_entitlement_cannot_grant_scanned_pages():
    account = entitlement()
    del account["job_plan"]["entitlements"]["limits"]["max_ocr_pages"]
    assert samples.sample_request(account, 1, 200)["max_ocr_pages"] == 0


@pytest.fixture
def sample_client(monkeypatch, tmp_path):
    captured = []
    api.app.dependency_overrides[api._billing_user] = lambda: {"id": "sample-user"}
    monkeypatch.setattr(api, "validate_job_features", lambda *args, **kw: entitlement())
    monkeypatch.setattr(api, "_require_ai_provider", lambda *args, **kw: None)
    monkeypatch.setattr(api, "WORK_DIR", tmp_path)
    monkeypatch.setattr(config, "DOCUMENT_SAMPLE_ENABLED", True)
    monkeypatch.setattr(config, "OCR_ENABLED", True)
    monkeypatch.setattr(config, "OCR_MAX_PAGES", 150)
    monkeypatch.setattr(rollout_service, "guest_trial_status", lambda _id: {"remaining_minutes": 2})
    def job_path(job_id):
        path = tmp_path / job_id
        path.mkdir()
        return path
    monkeypatch.setattr(api, "_job_path", job_path)
    monkeypatch.setattr(api, "JOBS", SimpleNamespace(cleanup_expired=lambda: None,
                                                   create=lambda *a, **kw: None))
    monkeypatch.setattr(api, "threading", SimpleNamespace(Thread=lambda **kw: SimpleNamespace(
        start=lambda: captured.append(kw["args"]))))
    try:
        yield TestClient(api.app), captured
    finally:
        api.app.dependency_overrides.pop(api._billing_user, None)


def test_api_captures_only_server_budget_and_explicit_selection(sample_client):
    client, captured = sample_client
    response = client.post("/jobs", data={"document_sample": "true", "document_start_page": "3",
        "max_words": "999999", "max_ocr_pages": "999999", "quiz_count": "0", "flashcard_count": "0"},
        files={"files": ("book.pdf", b"%PDF-synthetic-upload", "application/pdf")})
    assert response.status_code == 200, response.text
    advertised = client.get("/billing/plans").json()["document_sample"]
    assert advertised["max_words"] == 2000 and advertised["max_pages"] == 10
    assert advertised["supports_ocr"] is True and advertised["native_text_only"] is False
    assert advertised["max_ocr_pages"] == 5
    assert captured[0][2]["pdf_sample"] == {"start_page": 3,
        "max_words": min(advertised["max_words"], 2 * config.DOCUMENT_WORDS_PER_CREDIT_MINUTE),
        "max_pages": advertised["max_pages"], "max_ocr_pages": 5}


@pytest.mark.parametrize("filename,start,code", [
    ("notes.txt", "1", "LS-DOC-16"), ("book.pdf", "0", "LS-DOC-15"),
])
def test_bad_sample_input_fails_before_dispatch(sample_client, filename, start, code):
    client, captured = sample_client
    response = client.post("/jobs", data={"document_sample": "true", "document_start_page": start,
        "quiz_count": "0", "flashcard_count": "0"}, files={"files": (filename, b"synthetic", "application/pdf")})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code
    assert not captured


def test_full_document_upload_does_not_silently_become_sample(sample_client):
    client, captured = sample_client
    response = client.post("/jobs", data={"quiz_count": "0", "flashcard_count": "0"},
        files={"files": ("book.pdf", b"synthetic", "application/pdf")})
    assert response.status_code == 200, response.text
    assert "pdf_sample" not in captured[0][2]


def test_unreleased_sample_is_rejected_and_catalog_does_not_advertise_it(sample_client, monkeypatch):
    client, captured = sample_client
    monkeypatch.setattr(config, "DOCUMENT_SAMPLE_ENABLED", False)
    response = client.post("/jobs", data={"document_sample": "true", "quiz_count": "0", "flashcard_count": "0"},
        files={"files": ("book.pdf", b"synthetic", "application/pdf")})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "LS-DOC-17"
    assert not captured
    assert client.get("/billing/plans").json()["document_sample"]["enabled"] is False


def test_disabled_ocr_is_not_advertised_or_granted(sample_client, monkeypatch):
    client, captured = sample_client
    monkeypatch.setattr(config, "OCR_ENABLED", False)
    capability = client.get("/billing/plans").json()["document_sample"]
    assert capability["max_ocr_pages"] == 0
    assert capability["supports_ocr"] is False and capability["native_text_only"] is True
    response = client.post("/jobs", data={"document_sample": "true", "max_ocr_pages": "5",
        "quiz_count": "0", "flashcard_count": "0"},
        files={"files": ("book.pdf", b"%PDF-synthetic-upload", "application/pdf")})
    assert response.status_code == 200
    assert captured[0][2]["pdf_sample"]["max_ocr_pages"] == 0


@pytest.mark.parametrize("malformed", [{}, False, "true"])
def test_malformed_sample_cannot_fall_back_to_whole_document(malformed, tmp_path):
    from lecturesift.documents import extract_documents
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-synthetic")
    forwarded = samples.extraction_options({"pdf_sample": malformed})
    assert forwarded == {"pdf_sample": malformed}
    with pytest.raises(LectureSiftError) as error:
        extract_documents([path], **forwarded)
    assert error.value.code == "LS-DOC-16"


@pytest.mark.parametrize("module,function", [
    (durable_runtime, "_preflight_documents"), (tasks, "_preflight_worker_documents"),
])
def test_both_dispatchers_preserve_sample_budget_and_scope(monkeypatch, tmp_path, module, function):
    selection = {"start_page": 3, "max_words": 400, "max_pages": 5}
    scope = {"start_page": 3, "end_page": 4, "original_pages": 120,
             "processed_pages": 2, "word_limit": 400, "truncated": True}
    data = {"credit_seconds": 120, "words": 400, "ocr_required": False,
            "pages": 2, "ocr_pages": 0, "estimated": False, "credit_minutes": 2,
            "partial_source": True, "sample": scope}
    calls, updates = [], []
    monkeypatch.setattr(module, "extract_documents", lambda *a, **kw: calls.append(kw) or data)
    monkeypatch.setattr(module.JOBS, "update", lambda job_id, **kw: updates.append(kw))
    options = {"pdf_sample": deepcopy(selection)}
    assert getattr(module, function)("sample-job", [tmp_path / "book.pdf"], options) == 120
    assert calls[0]["pdf_sample"] == selection
    assert calls[0]["enable_ocr"] is False
    assert updates[0]["document_pages"] == 2
    assert updates[0]["partial_source"] is True
    assert updates[0]["document_sample"] == scope


def test_rejected_readable_document_does_not_consume_guest_trial(monkeypatch, tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"synthetic")
    monkeypatch.setattr(pipeline, "is_guest_user", lambda _id: True)
    monkeypatch.setattr(pipeline, "reserve_guest_job", lambda *a: pytest.fail("rejection consumed trial"))
    def reject(*args, **kwargs):
        raise BillingError("No remaining allowance")
    monkeypatch.setattr(pipeline, "require_duration_entitlement", reject)
    with pytest.raises(BillingError):
        pipeline._enforce_actual_document_entitlement("job", {"billing_user_id": "guest"},
            [source], {"credit_seconds": 60, "pages": 1})


def test_all_sample_exports_include_scope_even_without_transcript(monkeypatch, tmp_path):
    scope = {"start_page": 3, "end_page": 4, "original_pages": 120, "word_limit": 400}
    notice = samples.scope_notice("en", scope)
    seen = []
    def write(path, title, sections):
        assert sections[0] == ("", [notice])
        path.write_text(notice)
        seen.append(path.suffix)
    monkeypatch.setattr(exports, "_write_pdf", write)
    monkeypatch.setattr(exports, "_write_docx", write)
    result = {"partial_source": True, "source_scope_notice": notice, "title": "Sample",
              "summary": "Example answer", "options": {"output_formats": ["pdf", "docx", "txt"],
              "include_transcript": False}, "quiz": [{"question": "Q", "answer": "A"}]}
    exports.build_artifacts(tmp_path, result, tmp_path / "slides")
    assert ".pdf" in seen and ".docx" in seen
    for path in (tmp_path / "package").glob("*.txt"):
        assert path.read_text().startswith(notice)


def test_native_sample_runs_through_pipeline_and_keeps_scope(monkeypatch, tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    source = tmp_path / 'book.pdf'
    canvas = Canvas(str(source))
    for text in ['UNSELECTEDPRIVATE ' * 15, 'Selected energy lesson source ' * 12]:
        canvas.drawString(30, 700, text)
        canvas.showPage()
    canvas.save()
    job_dir = tmp_path / 'job'
    job_dir.mkdir()
    state = {'job_id': 'sample-pipeline', 'job_dir': str(job_dir)}
    monkeypatch.setattr(pipeline, 'JOBS', SimpleNamespace(get=lambda _: state,
        update=lambda _, **values: state.update(values)))
    selections, results, usage = [], [], []
    def generate(text, options):
        selections.append(text)
        return {'title': 'Sample lesson', 'summary': 'Energy summary', 'quiz': [], 'flashcards': []}
    def artifacts(directory, result, slides):
        results.append(deepcopy(result))
        return [], directory / 'sample.zip'
    monkeypatch.setattr(pipeline, '_make_selected_study_pack', generate)
    monkeypatch.setattr(pipeline, '_enforce_actual_document_entitlement', lambda *a, **k: None)
    monkeypatch.setattr(pipeline, '_record_billing_usage', lambda *a, **k: usage.append(k))
    monkeypatch.setattr(pipeline, 'build_artifacts', artifacts)
    pipeline._process_job('sample-pipeline', [source], {'document_mode': True,
        'output_language': 'en', 'include_transcript': False,
        'pdf_sample': {'start_page': 2, 'max_words': 200, 'max_pages': 5}})
    assert state['status'] == 'done', state
    assert state['partial_source'] is True
    assert state['document_sample']['start_page'] == 2
    assert state['document_sample']['original_pages'] == 2
    assert len(selections) == len(results) == len(usage) == 1
    assert 'UNSELECTEDPRIVATE' not in selections[0]
    assert 'Selected energy lesson source' in selections[0]
    assert results[0]['partial_source'] is True
    assert results[0]['sources']['document_sample'] == state['document_sample']
    assert results[0]['source_scope_notice'] == samples.scope_notice('en', state['document_sample'])
    assert results[0]['transcript'] == ''
    assert 0 < usage[0]['duration_seconds'] <= math.ceil(200 / config.DOCUMENT_WORDS_PER_CREDIT_MINUTE) * 60
