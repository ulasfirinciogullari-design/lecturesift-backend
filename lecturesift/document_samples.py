"""Trusted budgets and public provenance for explicitly selected PDF samples."""

import json
import math
from functools import lru_cache
from pathlib import Path

from .billing_service import BillingError
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
    word_limit = min(1000, math.floor(minutes) * int(words_per_minute))
    pages = min(5, int(limits["max_document_pages"]))
    if word_limit < 1 or pages < 1:
        raise BillingError("Kısa örnek için kullanım hakkın kalmadı. Hesabını veya planını kontrol et.")
    return {"start_page": start_page, "max_words": word_limit, "max_pages": pages}


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
