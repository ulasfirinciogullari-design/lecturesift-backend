import base64
import hashlib
import hmac
import uuid
from pathlib import Path

from PIL import Image

import lecturesift.ai as ai
import lecturesift.visual_translation as visual_translation
from lecturesift import config
from lecturesift.billing import PLAN_BY_CODE, public_catalog
from lecturesift.billing_service import account_status, register_user, verify_email
from lecturesift.commerce import (
    accept_payment_event,
    commerce_entitlements,
    create_purchase,
    preview_result_for_user,
    require_download_access,
)
from lecturesift.paytr import validate_callback
from lecturesift.storage import ObjectStorage


def _user() -> dict:
    registered = register_user(
        f"commerce-{uuid.uuid4()}@example.com",
        "Strong-test-password1",
        "Commerce",
        "User",
    )
    verified = verify_email(registered["verification_token"])
    return verified["user"]


def test_free_preview_has_no_download_and_mini_unlock_exists():
    catalog = public_catalog("TRY")
    plans = {plan["code"]: plan for plan in catalog["plans"]}
    assert catalog["free_downloads"] is False
    assert catalog["lowest_download_plan"] == "mini"
    assert plans["free"]["entitlements"]["download_enabled"] is False
    assert plans["free"]["export_enabled"] is False
    assert plans["mini"]["kind"] == "one_time"
    assert plans["mini"]["display_price"]["amount_minor"] == 4900
    assert plans["plus"]["interval_prices"]["annual"]["amount_minor"] == 69900 * 10
    assert plans["plus"]["annual_savings_months"] == 2


def test_unpaid_result_is_limited_and_download_access_is_denied():
    user = _user()
    result = {
        "summary": "s" * 5000,
        "key_points": [f"point-{index}" for index in range(10)],
        "important_terms": [{"term": str(index), "definition": "definition"} for index in range(10)],
        "notes": [{"heading": f"n-{index}", "content": "c" * 1000, "bullets": ["a", "b", "c", "d", "e"]} for index in range(8)],
        "quiz": [{"question": f"q-{index}", "options": ["a", "b", "c", "d"], "answer_index": 1, "explanation": "why"} for index in range(10)],
        "flashcards": [{"front": f"f-{index}", "back": "answer"} for index in range(10)],
        "slides": [{"file": f"slide-{index}.jpg"} for index in range(8)],
        "artifacts": [{"file": "Ozet.pdf"}],
        "transcript_original": "t" * 10000,
    }
    preview = preview_result_for_user(user["id"], "missing-history-job", result)
    assert preview["download_locked"] is True
    assert preview["preview_limited"] is True
    assert preview["unlock_plan"] == "mini"
    assert preview["artifacts"] == []
    assert len(preview["slides"]) == 3
    assert len(preview["quiz"]) == 3
    try:
        require_download_access(user["id"])
    except Exception as exc:
        assert "Mini" in str(exc)
    else:
        raise AssertionError("free account unexpectedly received download access")


def test_paid_event_is_idempotent_and_grants_mini_rights():
    user = _user()
    purchase = create_purchase(user["id"], "mini", "one_time", "TRY")
    first = accept_payment_event(
        provider="paytr",
        reference=purchase["reference"],
        status="success",
        amount_minor=purchase["amount_minor"],
        provider_reference="provider-test",
        event_identity="same-callback",
    )
    second = accept_payment_event(
        provider="paytr",
        reference=purchase["reference"],
        status="success",
        amount_minor=purchase["amount_minor"],
        provider_reference="provider-test",
        event_identity="same-callback",
    )
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert account_status(user["id"])["credit_minutes"] == PLAN_BY_CODE["mini"].minutes
    assert commerce_entitlements(user["id"])["download_enabled"] is True
    assert commerce_entitlements(user["id"])["visual_translation"] is True


def test_paytr_callback_hash_is_validated(monkeypatch):
    monkeypatch.setattr(config, "PAYTR_MERCHANT_ID", "100001")
    monkeypatch.setattr(config, "PAYTR_MERCHANT_KEY", "secret-key")
    monkeypatch.setattr(config, "PAYTR_MERCHANT_SALT", "secret-salt")
    oid, status, amount = "LSPTEST123", "success", "4900"
    material = oid + config.PAYTR_MERCHANT_SALT + status + amount
    digest = hmac.new(config.PAYTR_MERCHANT_KEY.encode(), material.encode(), hashlib.sha256).digest()
    callback_hash = base64.b64encode(digest).decode()
    parsed = validate_callback({"merchant_oid": oid, "status": status, "total_amount": amount, "hash": callback_hash})
    assert parsed["merchant_oid"] == oid
    assert parsed["amount_minor"] == 4900


def test_storage_namespaces_transient_sources_and_single_output_zip():
    storage = ObjectStorage()
    source = storage.source_key("job-1", "audio", 1, Path("lecture.mp4"))
    output = storage.output_key("job-1", Path("LectureSift_Study_Pack.zip"))
    assert source == "transient/jobs/job-1/sources/audio_001.mp4"
    assert output == "outputs/jobs/job-1/LectureSift_Study_Pack.zip"
    assert "/sources/" not in output


def test_visual_translation_renders_copy_and_preserves_original(tmp_path, monkeypatch):
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    source = slides_dir / "slide.jpg"
    Image.new("RGB", (800, 450), (245, 245, 245)).save(source)
    monkeypatch.setattr(
        visual_translation,
        "_extract_regions",
        lambda path, language: [{"box": [100, 100, 900, 350], "translation": "Çevrilmiş başlık"}],
    )
    slides, diagnostics = visual_translation.translate_slide_images(
        [{"file": source.name, "timestamp": "00:10"}], slides_dir, "tr"
    )
    assert source.exists()
    assert diagnostics["translated_slides"] == 1
    assert diagnostics["translated_regions"] == 1
    assert (slides_dir / slides[0]["translated_file"]).exists()


def test_long_transcript_uses_every_chunk_without_truncation(monkeypatch):
    seen = []

    def section(chunk, **kwargs):
        seen.append(chunk["text"])
        index = kwargs["index"]
        return {
            "section_index": index,
            "heading": f"Section {index}",
            "summary": f"Summary {index}",
            "key_points": [],
            "important_terms": [],
            "notes": [],
            "exam_focus": [],
            "quiz": [],
            "flashcards": [],
            "start_second": chunk.get("start_second"),
            "end_second": chunk.get("end_second"),
        }

    monkeypatch.setattr(ai, "_section_analysis", section)
    monkeypatch.setattr(
        ai,
        "_final_synthesis",
        lambda analyses, **kwargs: {
            "title": "Complete lecture",
            "summary": "All sections",
            "key_points": [],
            "important_terms": [],
            "notes": [],
            "exam_focus": [],
            "quiz": [],
            "flashcards": [],
        },
    )
    transcript = "A" * 130000
    pack = ai.make_study_pack(transcript, "en", "standard", 10, 20)
    assert len(seen) > 5
    assert sum(len(chunk) for chunk in seen) == len(transcript)
    assert pack["source_coverage"]["characters"] == 130000
    assert pack["source_coverage"]["truncated"] is False
