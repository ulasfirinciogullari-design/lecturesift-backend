"""Trusted budgets and public provenance for explicitly selected PDF samples."""

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from .billing_service import BillingError
from . import config
from .config import PDF_SAMPLE_MAX_PAGES, PDF_SAMPLE_MAX_WORDS
from .errors import LectureSiftError


def sample_request(entitlement: dict, start_page: int, words_per_minute: int,
                   guest_trial: dict | None = None) -> dict:
    if start_page < 1:
        raise LectureSiftError("LS-DOC-15", "Başlangıç sayfası en az 1 olmalı.", status_code=400)
    limits = entitlement["job_plan"]["entitlements"]["limits"]
    minutes = float(limits["max_minutes_per_job"])
    remaining = entitlement.get("remaining_minutes")
    if remaining is not None:
        minutes = min(minutes, float(remaining))
    if guest_trial is not None:
        minutes = min(minutes, float(guest_trial.get("remaining_minutes") or 0))
    # Billing rounds UP to whole minutes; never promise a fractional allowance.
    word_limit = min(PDF_SAMPLE_MAX_WORDS, math.floor(minutes) * int(words_per_minute))
    pages = min(PDF_SAMPLE_MAX_PAGES, int(limits["max_document_pages"]))
    if word_limit < 1 or pages < 1:
        raise BillingError("Kısa örnek için kullanım hakkın kalmadı. Hesabını veya planını kontrol et.")
    return {"start_page": start_page, "max_words": word_limit, "max_pages": pages,
            "max_ocr_pages": min(ocr_sample_cap(), max(0, int(limits.get("max_ocr_pages", 0))))}


def ocr_sample_cap() -> int:
    """Advertise and enforce the same server OCR ceiling for short samples."""
    return min(config.PDF_SAMPLE_MAX_OCR_PAGES, config.OCR_MAX_PAGES) if config.OCR_ENABLED else 0


def failure_metadata(technical_message: object) -> dict:
    """Expose only our finite diagnostic vocabulary, never raw exception text."""
    if not isinstance(technical_message, str) or not technical_message.startswith("pdf_sample:"):
        return {}
    if len(technical_message) > 512:
        return {}
    try:
        data = json.loads(technical_message.removeprefix("pdf_sample:"))
    except ValueError:
        return {}
    reasons = {"no_readable_text", "native_reader_failed", "page_count_mismatch", "ocr_budget_exhausted"}
    if not isinstance(data, dict) or not isinstance(data.get("reason"), str) or data["reason"] not in reasons:
        return {}
    result = {"reason": data["reason"]}
    page = data.get("page")
    if type(page) is int and 1 <= page <= config.MAX_DOCUMENT_PAGES:
        result["page"] = page
    # Class names are useful only for the native reader failure. All exception
    # messages, paths, source text and arbitrary fields are deliberately absent.
    kind = data.get("exception_type")
    if data["reason"] == "native_reader_failed" and isinstance(kind, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", kind):
        result["exception_type"] = kind
    return result


def extraction_options(options: dict) -> dict:
    sample = options.get("pdf_sample")
    # Preserve malformed explicit choices so the extractor rejects them. An
    # empty/false option must never silently turn into full-document processing.
    return {"pdf_sample": sample} if sample is not None else {}


def sample_metadata(document_data: dict) -> dict:
    if not document_data.get("partial_source"):
        return {}
    return {"partial_source": True, "document_sample": dict(document_data["sample"])}


@lru_cache(maxsize=1)
def _copy() -> dict:
    return json.loads(Path(__file__).with_name("document_sample_copy.json").read_text(encoding="utf-8"))


def scope_notice(language: str, sample: dict) -> str:
    copy = _copy()
    text = copy.get(language, copy["en"])["scope"]
    return text.format(start=sample["start_page"], end=sample["end_page"],
                       total=sample["original_pages"], limit=sample["word_limit"])
