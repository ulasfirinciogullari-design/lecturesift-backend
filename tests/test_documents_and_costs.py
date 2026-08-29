from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pptx import Presentation
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

import lecturesift.app as app_module
import lecturesift.costs as costs
from lecturesift import config
from lecturesift.app import app
from lecturesift.billing_service import register_user, verify_email
from lecturesift.documents import extract_documents
from lecturesift.errors import LectureSiftError
from lecturesift.jobs import JOBS
from lecturesift.rollout_routes import install_rollout_routes


def _account_headers() -> dict[str, str]:
    created = register_user(
        f"document-{uuid.uuid4()}@example.com",
        "Strong-test-password1",
        "Document",
        "User",
    )
    token = verify_email(created["verification_token"])["token"]
    return {"Authorization": f"Bearer {token}"}


def test_text_docx_pptx_and_pdf_extraction(tmp_path: Path):
    text_path = tmp_path / "notes.txt"
    text_path.write_text(" ".join(f"word{index}" for index in range(205)), encoding="utf-8")

    docx_path = tmp_path / "lesson.docx"
    document = Document()
    document.add_heading("Energy", level=1)
    document.add_paragraph("Potential and kinetic energy are related.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Unit"
    table.cell(0, 1).text = "Joule"
    document.save(docx_path)

    pptx_path = tmp_path / "slides.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Conservation law"
    slide.placeholders[1].text = "Energy changes form but is conserved."
    presentation.save(pptx_path)

    pdf_path = tmp_path / "handout.pdf"
    pdf = canvas.Canvas(str(pdf_path))
    pdf.drawString(72, 760, "Work transfers energy between systems.")
    pdf.save()

    result = extract_documents([text_path, docx_path, pptx_path, pdf_path])
    assert result["credit_minutes"] >= 2
    assert result["words"] >= 205
    assert [item["type"] for item in result["documents"]] == ["txt", "docx", "pptx", "pdf"]
    assert "Potential and kinetic energy" in result["text"]
    assert "Conservation law" in result["text"]
    assert "Work transfers energy" in result["text"]


def test_image_only_pdf_returns_actionable_ocr_error(tmp_path: Path):
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(LectureSiftError) as caught:
        extract_documents([path])
    assert caught.value.code == "LS-DOC-12"
    assert "OCR" in caught.value.user_message


def test_document_upload_records_document_quota_before_background_processing(tmp_path: Path, monkeypatch):
    captured: dict = {}

    class DeferredThread:
        def __init__(self, target, args, daemon):
            captured.update(target=target, args=args, daemon=daemon)

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(app_module, "WORK_DIR", tmp_path)
    monkeypatch.setattr(app_module.threading, "Thread", DeferredThread)
    response = TestClient(app).post(
        "/jobs",
        files={"file": ("lecture.txt", b"energy work force power " * 80, "text/plain")},
        headers=_account_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    job = JOBS.get(body["job_id"])
    assert body["source_layout"] == "documents"
    assert body["document_words"] >= 300
    assert body["billable_minutes"] >= 2
    assert job["source_type"] == "document"
    assert captured["started"] is True
    assert captured["args"][2]["document_credit_seconds"] == body["billable_minutes"] * 60
    shutil.rmtree(Path(job["job_dir"]), ignore_errors=True)


def test_document_and_video_cannot_be_mixed_in_one_job():
    response = TestClient(app).post(
        "/jobs",
        files=[
            ("files", ("lesson.txt", b"lesson text", "text/plain")),
            ("files", ("lesson.mp4", b"video", "video/mp4")),
        ],
        headers=_account_headers(),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "LS-UPLOAD-05"


def test_cost_ledger_attributes_provider_usage_without_storing_payload(monkeypatch):
    job_id = f"cost-{uuid.uuid4()}"
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=500,
            prompt_tokens_details=SimpleNamespace(cached_tokens=200),
        )
    )
    try:
        with costs.cost_context(job_id, str(uuid.uuid4())):
            wrote = costs.record_openai_response("gpt-4o-mini", response, "study_pack")
            costs.record_cost(
                provider="test",
                service="guard",
                resource="metadata",
                quantity=1,
                unit="event",
                price_usd=0,
                pricing_source="test",
                pricing_effective_at="2026-08-29",
                metadata={"request_kind": "study_pack", "prompt": "NEVER STORE THIS"},
            )
        assert wrote is True
        with costs.ENGINE.connect() as connection:
            rows = connection.execute(
                costs.COST_EVENTS.select().where(costs.COST_EVENTS.c.job_id == job_id)
            ).mappings().all()
        assert len(rows) == 4
        assert sum(int(row["cost_microusd"]) for row in rows) == 435
        assert all("NEVER STORE THIS" not in row["metadata_json"] for row in rows)
        assert all("prompt" not in json.loads(row["metadata_json"]) for row in rows)
    finally:
        costs.init_cost_database()
        with costs.ENGINE.begin() as connection:
            connection.execute(costs.COST_EVENTS.delete().where(costs.COST_EVENTS.c.job_id == job_id))


def test_admin_cost_endpoint_is_protected_and_separates_external_invoices(monkeypatch):
    install_rollout_routes(app)
    monkeypatch.setattr(config, "ADMIN_ADMIN", "cost-admin-secret")
    monkeypatch.setattr(costs, "_fx_rate", lambda: (42.0, "test rate"))
    client = TestClient(app)
    assert client.get("/billing/admin/costs").status_code == 401
    response = client.get(
        "/billing/admin/costs?days=30&limit=10",
        headers={"Authorization": "Bearer cost-admin-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["currency"]["source"] == "test rate"
    assert {item["provider"] for item in body["external_invoice_sources"]} >= {
        "iyzico / PayTR",
        "Google Ads",
        "Cloudflare R2",
    }
    assert "kesin kaynaktır" in body["disclaimer"]
