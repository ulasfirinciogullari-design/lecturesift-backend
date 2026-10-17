from __future__ import annotations

import json
import math
import os
import shutil
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw, ImageFont

import lecturesift.documents as documents
from lecturesift.errors import LectureSiftError


def _forbidden(*args, **kwargs):
    raise AssertionError("A short sample must not use full-document extraction or OCR")


def _fake_pdf(monkeypatch, tmp_path: Path, texts: list[str]):
    path = tmp_path / "lesson.pdf"
    path.write_bytes(b"%PDF-fake bounded native reader")
    state = SimpleNamespace(opened=[], reads=[], closed_pages=[], closed_text=[], closed=False, fail_read=False)

    class TextPage:
        def __init__(self, index):
            self.index = index

        def count_chars(self):
            return len(texts[self.index])

        def get_text_range(self, *, index, count):
            assert 0 < count <= documents._PDF_SAMPLE_CHUNK_CHARACTERS
            state.reads.append((self.index, index, count))
            if state.fail_read:
                raise RuntimeError("native reader unavailable")
            return texts[self.index][index:index + count]

        def close(self):
            state.closed_text.append(self.index)

    class Page:
        def __init__(self, index):
            self.index = index

        def get_textpage(self):
            return TextPage(self.index)

        def close(self):
            state.closed_pages.append(self.index)

    class PdfDocument:
        def __init__(self, _path):
            assert _path == str(path)

        def __len__(self):
            return len(texts)

        def __getitem__(self, index):
            state.opened.append(index)
            return Page(index)

        def close(self):
            state.closed = True

    monkeypatch.setattr(documents, "PdfReader", lambda *a, **kw: SimpleNamespace(is_encrypted=False, pages=range(len(texts))))
    monkeypatch.setitem(sys.modules, "pypdfium2", SimpleNamespace(PdfDocument=PdfDocument))
    monkeypatch.setattr(documents, "_extract_pdf", _forbidden)
    monkeypatch.setattr(documents, "_extract_pdf_native_pages", _forbidden)
    monkeypatch.setattr(documents, "_ocr_pdf_page", _forbidden)
    return path, state


def test_sample_reads_only_selected_pages_and_accounts_for_scope_header(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, [
        "UNSELECTED INTRODUCTION " * 10,
        "Selected chapter explains conservation of energy in isolated systems.",
        "Selected continuation applies conservation of energy to simple collisions.",
        "UNSELECTED CONCLUSION " * 10,
    ])

    result = documents.extract_documents([path], pdf_sample={"start_page": 2, "max_words": 200, "max_pages": 2})

    assert state.opened == [1, 2]
    assert state.closed and state.closed_pages == [1, 2] and state.closed_text == [1, 2]
    assert "Selected chapter" in result["text"] and "Selected continuation" in result["text"]
    assert "UNSELECTED" not in result["text"]
    assert "PARTIAL PDF SAMPLE: pages 2–3 of 4" in result["text"]
    assert result["sample"] == {
        "start_page": 2, "end_page": 3, "original_pages": 4, "processed_pages": 2,
        "word_limit": 200, "truncated": True,
    }
    assert result["partial_source"] is True
    assert result["documents"][0]["sample"] == result["sample"]
    assert result["documents"][0]["partial_source"] is True
    assert result["pages"] == 2 and result["original_pages"] == 4
    assert result["words"] == len(re.findall(r"[^\W_]+", result["text"])) <= 200
    assert result["credit_minutes"] == math.ceil(result["words"] / documents.DOCUMENT_WORDS_PER_CREDIT_MINUTE)
    assert result["ocr_pages"] == 0 and result["ocr_used"] is False and result["ocr_required"] is False
    assert result["estimated"] is False


def test_dense_pdf_stops_within_first_page_without_reading_full_text(monkeypatch, tmp_path):
    native = " ".join(f"word{i}" for i in range(100_000))
    path, state = _fake_pdf(monkeypatch, tmp_path, [native, "UNREAD NEXT CHAPTER " * 10])
    result = documents.extract_documents([path], pdf_sample={"start_page": 1, "max_words": 200})

    assert result["words"] == 200
    assert result["extracted_words"] == 200
    assert state.opened == [0]
    assert sum(count for _, _, count in state.reads) < 4096
    assert "word1000" not in result["text"] and "UNREAD" not in result["text"]
    assert result["sample"]["truncated"] is True and result["sample"]["processed_pages"] == 1
    assert state.closed and state.closed_pages == state.closed_text == [0]


def test_sample_has_hard_ten_page_cap_even_if_caller_requests_more(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, [f"Chapter {i} explains a useful scientific principle." for i in range(15)])
    result = documents.extract_documents([path], pdf_sample={"start_page": 2, "max_words": 1000, "max_pages": 999})
    assert state.opened == list(range(1, 11))
    assert result["pages"] == 10 and result["original_pages"] == 15
    assert result["sample"]["end_page"] == 11 and result["sample"]["truncated"] is True


def test_sample_has_hard_two_thousand_word_cap_even_for_oversized_internal_budget(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["native content " * 5000])
    result = documents.extract_documents([path], pdf_sample={"max_words": 100_000})
    assert result["words"] == 2000
    assert result["sample"]["word_limit"] == 2000 and result["sample"]["truncated"] is True
    assert state.opened == [0] and sum(count for _, _, count in state.reads) < 20_000


@pytest.mark.parametrize("native", ["", "   \n", "---"])
def test_native_only_old_sample_never_skips_scan_or_starts_unbudgeted_ocr(monkeypatch, tmp_path, native):
    path, state = _fake_pdf(monkeypatch, tmp_path, [native, "A later page contains plenty of readable native text."])
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], enable_ocr=True, allow_ocr_pending=True, pdf_sample={"max_words": 1000})
    assert error.value.code == "LS-OCR-02"
    assert state.opened == [0] and state.closed and state.closed_pages == state.closed_text == [0]


def test_short_native_title_page_is_included_before_readable_chapter(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, [
        "Energy",
        "The following chapter explains the conservation of energy in isolated systems.",
    ])
    result = documents.extract_documents([path], pdf_sample={"start_page": 1, "max_words": 200})
    assert state.opened == [0, 1]
    assert "Energy\n\nThe following chapter" in result["text"]
    assert result["sample"]["start_page"] == 1 and result["sample"]["end_page"] == 2
    assert result["pages"] == 2 and result["ocr_used"] is False
    assert state.closed and state.closed_pages == state.closed_text == [0, 1]


def test_five_tiny_headings_do_not_expand_window_to_find_later_chapter(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, [
        "I", "II", "III", "IV", "V",
        "A later chapter has enough text but lies outside the selected five pages.",
    ])
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 1000, "max_pages": 5})
    assert error.value.code == "LS-DOC-14"
    assert state.opened == [0, 1, 2, 3, 4] and state.closed
    assert state.closed_pages == state.closed_text == state.opened


def test_only_a_short_native_heading_is_not_a_readable_sample(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["A short heading"])
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 1000})
    assert error.value.code == "LS-DOC-14" and state.opened == [0] and state.closed


def test_native_reader_failure_closes_resources_without_full_fallback(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["Readable native text would normally be extracted."])
    state.fail_read = True
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 1000, "max_ocr_pages": 5})
    assert error.value.code == "LS-DOC-18"
    assert state.closed and state.closed_pages == state.closed_text == [0]

    diagnostic = json.loads(error.value.technical_message.removeprefix("pdf_sample:"))
    assert diagnostic == {"reason": "native_reader_failed", "page": 1, "exception_type": "RuntimeError"}
    assert "native reader unavailable" not in error.value.technical_message


@pytest.mark.parametrize("start_page", [0, -1, True, 1.5, "1", 3])
def test_sample_rejects_invalid_or_out_of_range_start_before_native_read(monkeypatch, tmp_path, start_page):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["A native text page.", "Another native text page."])
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"start_page": start_page, "max_words": 1000})
    assert error.value.code == "LS-DOC-15" and state.opened == []


@pytest.mark.parametrize("sample", [False, [], {}, {"max_words": True}, {"max_words": 0}, {"max_words": 1}, {"max_words": 1000, "max_pages": 0}])
def test_sample_requires_valid_server_bounds(monkeypatch, tmp_path, sample):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["A native text page."])
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample=sample)
    assert error.value.code == "LS-DOC-16" and state.opened == []


def test_sample_rejects_non_pdf_and_multiple_files_before_extraction(tmp_path):
    text_path = tmp_path / "notes.txt"
    text_path.write_text("Normal text is not an explicit PDF sample.")
    for paths in ([text_path], [tmp_path / "one.pdf", tmp_path / "two.pdf"]):
        with pytest.raises(LectureSiftError) as error:
            documents.extract_documents(paths, pdf_sample={"max_words": 1000})
        assert error.value.code == "LS-DOC-16"


@pytest.mark.parametrize("guard,expected", [("size", "LS-DOC-02"), ("pages", "LS-DOC-06"), ("signature", "LS-DOC-04"), ("encrypted", "LS-DOC-05")])
def test_sample_preserves_original_file_safety_limits(monkeypatch, tmp_path, guard, expected):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["A native text page."] * 3)
    if guard == "size":
        monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 1)
    elif guard == "pages":
        monkeypatch.setattr(documents, "MAX_DOCUMENT_PAGES", 2)
    elif guard == "signature":
        path.write_bytes(b"not a PDF document")
    else:
        monkeypatch.setattr(documents, "PdfReader", lambda *a, **kw: SimpleNamespace(is_encrypted=True))
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 1000})
    assert error.value.code == expected and state.opened == []


def test_sample_character_cap_bounds_even_one_enormous_native_word(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["enormous" * 100_000])
    monkeypatch.setattr(documents, "_PDF_SAMPLE_MAX_CHARACTERS", 2048)
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 1000})
    assert error.value.code == "LS-DOC-14"
    assert sum(count for _, _, count in state.reads) == 2048
    assert state.closed


def test_sample_preserves_global_character_cap_with_scope_header(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["A chapter explains scientific principles with detailed examples. " * 10])
    monkeypatch.setattr(documents, "MAX_DOCUMENT_CHARACTERS", 300)
    result = documents.extract_documents([path], pdf_sample={"max_words": 1000})
    assert result["characters"] <= 300
    assert result["sample"]["truncated"] is True
    assert sum(count for _, _, count in state.reads) <= 300


def test_bounded_reader_preserves_word_crossing_chunk_boundary(monkeypatch):
    native = "alpha extraordinarilylongword omega is outside the requested word limit"
    reads = []

    def read(*, index, count):
        reads.append((index, count))
        return native[index:index + count]

    page = SimpleNamespace(count_chars=lambda: len(native), get_text_range=read)
    monkeypatch.setattr(documents, "_PDF_SAMPLE_CHUNK_CHARACTERS", 5)
    text, clipped, requested, _ = documents._read_pdf_sample_page(page, word_limit=2, character_budget=1000)
    assert text == "alpha extraordinarilylongword"
    assert clipped is True and requested < len(native)
    assert [offset for offset, _ in reads] == list(range(0, requested, 5))


def test_bounded_reader_uses_internal_indexes_not_returned_string_lengths(monkeypatch):
    reads = []

    def read(*, index, count):
        reads.append((index, count))
        return {0: "alpha ", 5: "beta ", 10: "gamma"}[index]

    monkeypatch.setattr(documents, "_PDF_SAMPLE_CHUNK_CHARACTERS", 5)
    page = SimpleNamespace(count_chars=lambda: 15, get_text_range=read)
    text, clipped, requested, _ = documents._read_pdf_sample_page(page, word_limit=2, character_budget=1000)
    assert text == "alpha beta" and clipped is True
    assert reads == [(0, 5), (5, 5)] and requested == 10


def test_real_native_pdf_sample_keeps_original_page_scope_and_small_file_can_finish(tmp_path, monkeypatch):
    path = tmp_path / ("a descriptive document name " * 5 + ".pdf")
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 760, "First chapter is excluded from this selected sample.")
    pdf.showPage()
    pdf.drawString(72, 760, "Second chapter discusses the conservation of momentum.")
    pdf.save()
    monkeypatch.setattr(documents, "_extract_pdf_native_pages", _forbidden)
    monkeypatch.setattr(documents, "_ocr_pdf_page", _forbidden)

    result = documents.extract_documents([path], pdf_sample={"start_page": 2, "max_words": 200})
    assert "Second chapter" in result["text"] and "First chapter" not in result["text"]
    assert result["pages"] == 1 and result["original_pages"] == 2
    assert result["documents"][0]["name"] == path.name
    assert path.name not in result["text"]
    assert result["words"] <= 200
    assert result["sample"]["end_page"] == 2 and result["sample"]["truncated"] is True

    full_small_sample = documents.extract_documents([path], pdf_sample={"start_page": 1, "max_words": 200})
    assert full_small_sample["sample"]["truncated"] is False
    assert full_small_sample["partial_source"] is True


def test_normal_extraction_does_not_become_a_sample_without_explicit_option(tmp_path, monkeypatch):
    path = tmp_path / "normal.pdf"
    path.write_bytes(b"%PDF-normal extraction mocked")
    monkeypatch.setattr(documents, "_extract_pdf", lambda *a, **kw: ("Full source remains the normal default.", {"pages": 10}))
    monkeypatch.setattr(documents, "_extract_pdf_sample", _forbidden)
    result = documents.extract_documents([path])
    assert result["pages"] == 10
    assert "DOCUMENT 1: normal.pdf" in result["text"]
    assert "sample" not in result and "partial_source" not in result


def test_scanned_sample_preflight_marks_pending_without_ocr_or_guessing_beyond_bound(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["UNSELECTED", "", "", "", "UNSELECTED"])
    result = documents.extract_documents(
        [path], enable_ocr=False, allow_ocr_pending=True,
        pdf_sample={"start_page": 2, "max_words": 400, "max_pages": 3, "max_ocr_pages": 2},
    )
    assert state.opened == [1, 2, 3]  # Third scan is inspected but never included/OCRed.
    assert result["sample"]["start_page"] == 2 and result["sample"]["end_page"] == 3
    assert result["sample"]["processed_pages"] == result["pages"] == 2
    assert result["ocr_required"] is True and result["estimated"] is True
    assert result["ocr_used"] is False and result["ocr_pages"] == 2
    assert result["words"] == 400 and result["credit_minutes"] == 2
    assert "UNSELECTED" not in result["text"]
    assert state.closed


def test_scanned_sample_ocr_stays_in_selected_range_and_stops_before_ocr_budget(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["UNSELECTED", "", "", "", "LATER NATIVE CHAPTER"])
    calls = []
    progress = []
    def ocr(path, page, language):
        calls.append((page, language))
        return page, f"Scanned page {page} explains the conservation of energy with useful examples.", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    result = documents.extract_documents(
        [path], source_language="en", progress_callback=lambda done, total: progress.append((done, total)),
        pdf_sample={"start_page": 2, "max_words": 400, "max_pages": 4, "max_ocr_pages": 2},
    )
    assert calls == [(1, "en"), (2, "en")]
    assert state.opened == [1, 2, 3] and state.closed
    assert result["sample"]["end_page"] == 3 and result["sample"]["truncated"] is True
    assert result["ocr_pages"] == 2 and result["ocr_used"] is True
    assert result["estimated"] is False and result["ocr_required"] is False
    assert result["documents"][0]["ocr_language"] == "eng"
    assert "LATER NATIVE" not in result["text"] and "UNSELECTED" not in result["text"]
    assert progress == [(1, 2), (2, 2)]


def test_blank_page_stays_in_scope_and_no_longer_discards_following_native_text(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["", "A readable chapter explains energy conservation with worked examples."])
    calls = []
    def ocr(path, page, language):
        calls.append(page)
        return page, "", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    result = documents.extract_documents([path], pdf_sample={"max_words": 200, "max_ocr_pages": 1})
    assert calls == [0] and state.opened == [0, 1]
    assert result["sample"]["start_page"] == 1 and result["sample"]["end_page"] == 2
    assert result["pages"] == 2 and result["ocr_pages"] == 1
    assert "A readable chapter" in result["text"]


def test_dense_ocr_sample_obeys_same_unicode_word_budget_and_does_not_read_next_page(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["", "UNREAD NEXT PAGE"])
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda *args: (0, "enerji değişimi gözlem " * 4000, "tur"))
    result = documents.extract_documents([path], pdf_sample={"max_words": 200, "max_ocr_pages": 5})
    assert result["words"] == result["extracted_words"] == 200
    assert result["sample"]["word_limit"] == 200 and result["sample"]["end_page"] == 1
    assert result["sample"]["truncated"] is True and state.opened == [0]
    assert "UNREAD" not in result["text"]
    assert result["text"].split()[-1] in {"enerji", "değişimi", "gözlem"}


def test_sample_ocr_hard_cap_is_five_even_for_oversized_trusted_option(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, [""] * 10)
    calls = []
    def ocr(path, page, language):
        calls.append(page)
        return page, "This scanned page contains enough useful text for the bounded sample.", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    result = documents.extract_documents([path], pdf_sample={"max_words": 2000, "max_ocr_pages": 999})
    assert calls == list(range(5)) and state.opened == list(range(6))
    assert result["sample"]["end_page"] == 5 and result["sample"]["truncated"] is True
    assert result["ocr_pages"] == 5


def test_sample_global_ocr_budget_is_respected(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, [""] * 4)
    monkeypatch.setattr(documents, "OCR_MAX_PAGES", 1)
    calls = []
    def ocr(path, page, language):
        calls.append(page)
        return page, "A readable scanned chapter explains the conservation of energy.", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    result = documents.extract_documents([path], pdf_sample={"max_words": 400, "max_ocr_pages": 5})
    assert calls == [0] and result["ocr_pages"] == 1 and result["sample"]["end_page"] == 1


@pytest.mark.parametrize("budget", [-1, True, "5", None, 1.5])
def test_sample_rejects_invalid_ocr_budget_before_reading(monkeypatch, tmp_path, budget):
    path, state = _fake_pdf(monkeypatch, tmp_path, [""])
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 200, "max_ocr_pages": budget})
    assert error.value.code == "LS-DOC-16" and state.opened == []


def test_empty_ocr_sample_reports_safe_empty_reason_without_expanding_window(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["", "", "A native chapter outside the selected pages."])
    calls = []
    def ocr(path, page, language):
        calls.append(page)
        return page, "", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 200, "max_pages": 2, "max_ocr_pages": 2})
    assert error.value.code == "LS-DOC-14"
    assert json.loads(error.value.technical_message.removeprefix("pdf_sample:")) == {"reason": "no_readable_text", "page": 2}
    assert calls == state.opened == [0, 1]


def test_real_pdf_with_blank_cover_preserves_cover_and_native_chapter(tmp_path, monkeypatch):
    path = tmp_path / "blank-cover.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.showPage()
    pdf.drawString(72, 760, "The second page explains energy conservation in closed systems.")
    pdf.save()
    calls = []
    def ocr(path, page, language):
        calls.append(page)
        return page, "", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    monkeypatch.setattr(documents, "_extract_pdf_native_pages", _forbidden)
    result = documents.extract_documents([path], pdf_sample={"max_words": 200, "max_ocr_pages": 1})
    assert calls == [0]
    assert "second page explains" in result["text"]
    assert result["sample"]["start_page"] == 1 and result["sample"]["end_page"] == 2
    assert result["pages"] == 2


@pytest.mark.skipif(not shutil.which(documents.OCR_COMMAND), reason="Tesseract is not installed")
def test_real_ocr_scanned_pdf_sample_reads_selected_page_only(tmp_path, monkeypatch):
    image_path = tmp_path / "synthetic-scan.png"
    image = Image.new("RGB", (1800, 1200), "white")
    font_name = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf") if os.name == "nt" else "DejaVuSans.ttf"
    font = ImageFont.truetype(font_name, 58)
    draw = ImageDraw.Draw(image)
    draw.text((80, 220), "ENERGY IS CONSERVED IN A CLOSED SYSTEM", fill="black", font=font)
    draw.text((80, 330), "WORK TRANSFERS ENERGY BETWEEN OBJECTS", fill="black", font=font)
    image.save(image_path)
    image.close()
    path = tmp_path / "synthetic-three-pages.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 760, "UNSELECTED FIRST CHAPTER")
    pdf.showPage()
    pdf.drawImage(str(image_path), 36, 180, width=540, height=360)
    pdf.showPage()
    pdf.drawString(72, 760, "UNSELECTED LAST CHAPTER")
    pdf.save()
    original_ocr = documents._ocr_pdf_page
    calls = []
    def bounded_ocr(path, page, language):
        calls.append(page)
        assert page == 1
        return original_ocr(path, page, language)
    monkeypatch.setattr(documents, "_ocr_pdf_page", bounded_ocr)
    monkeypatch.setattr(documents, "_extract_pdf_native_pages", _forbidden)
    options = {"start_page": 2, "max_pages": 1, "max_words": 200, "max_ocr_pages": 1}
    preflight = documents.extract_documents([path], enable_ocr=False, allow_ocr_pending=True, pdf_sample=options)
    assert preflight["estimated"] is True and preflight["words"] == 200 and calls == []
    result = documents.extract_documents([path], source_language="en", pdf_sample=options)
    assert "ENERGY IS CONSERVED" in result["text"].upper()
    assert "UNSELECTED" not in result["text"] and calls == [1]
    assert result["sample"]["start_page"] == result["sample"]["end_page"] == 2
    assert result["ocr_pages"] == 1 and result["ocr_used"] is True and result["words"] <= 200


def test_sample_reader_page_count_mismatch_has_distinct_safe_diagnostic(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["Readable native page text for a bounded sample."])
    monkeypatch.setattr(documents, "PdfReader", lambda *a, **kw: SimpleNamespace(is_encrypted=False, pages=range(2)))
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 200, "max_ocr_pages": 5})
    assert error.value.code == "LS-DOC-18"
    assert json.loads(error.value.technical_message.removeprefix("pdf_sample:")) == {"reason": "page_count_mismatch", "page": 1}
    assert state.opened == [] and state.closed


def test_sample_respects_ocr_disabled_even_for_previously_authorized_budget(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, [""])
    monkeypatch.setattr(documents, "OCR_ENABLED", False)
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], pdf_sample={"max_words": 200, "max_ocr_pages": 5})
    assert error.value.code == "LS-OCR-02"
    assert json.loads(error.value.technical_message.removeprefix("pdf_sample:")) == {"reason": "ocr_budget_exhausted", "page": 1}
    assert state.opened == [0] and state.closed


def test_ocr_sample_preserves_character_cap_including_scope_header(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["", "UNREAD NEXT PAGE"])
    monkeypatch.setattr(documents, "MAX_DOCUMENT_CHARACTERS", 300)
    monkeypatch.setattr(documents, "_ocr_pdf_page", lambda *args: (0, "energy conservation requires complete words " * 500, "eng"))
    result = documents.extract_documents([path], pdf_sample={"max_words": 2000, "max_ocr_pages": 5})
    assert result["characters"] <= 300 and state.opened == [0]
    assert result["sample"]["truncated"] is True
    assert result["text"].split()[-1] in {"energy", "conservation", "requires", "complete", "words"}


def test_numbered_scanned_page_uses_ocr_body_instead_of_only_native_page_number(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["UNSELECTED", "1", "UNSELECTED"])
    calls = []
    def ocr(path, page, language):
        calls.append(page)
        return page, "This scanned lesson explains conservation of energy and momentum with examples.", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    options = {"start_page": 2, "max_pages": 1, "max_words": 200, "max_ocr_pages": 1}
    preflight = documents.extract_documents([path], enable_ocr=False, allow_ocr_pending=True, pdf_sample=options)
    assert preflight["ocr_required"] is True and preflight["ocr_pages"] == 1
    assert preflight["words"] == 200 and calls == []
    result = documents.extract_documents([path], pdf_sample=options)
    assert calls == [1] and state.opened == [1, 1]
    assert "conservation of energy" in result["text"] and "UNSELECTED" not in result["text"]
    assert result["ocr_used"] is True and result["sample"]["start_page"] == result["sample"]["end_page"] == 2


def test_native_title_survives_blank_ocr_and_keeps_following_chapter(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["Energy", "The following chapter explains conservation of energy in isolated systems."])
    calls = []
    def ocr(path, page, language):
        calls.append(page)
        return page, "", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    result = documents.extract_documents([path], pdf_sample={"max_words": 200, "max_ocr_pages": 1})
    assert calls == [0] and state.opened == [0, 1]
    assert "Energy\n\nThe following chapter" in result["text"]
    assert result["sample"]["start_page"] == 1 and result["sample"]["end_page"] == 2
    assert result["documents"][0]["native_text_pages"] == 2
    assert result["ocr_pages"] == 1


def test_sparse_native_page_after_ocr_budget_stops_scope_before_overlay_only_page(monkeypatch, tmp_path):
    path, state = _fake_pdf(monkeypatch, tmp_path, ["1", "2", "3", "Later readable native chapter is outside the available OCR sample."])
    calls = []
    def ocr(path, page, language):
        calls.append(page)
        return page, "The scanned body provides scientific examples explaining energy conservation.", "eng"
    monkeypatch.setattr(documents, "_ocr_pdf_page", ocr)
    result = documents.extract_documents([path], pdf_sample={"max_words": 400, "max_pages": 4, "max_ocr_pages": 2})
    assert calls == [0, 1] and state.opened == [0, 1, 2]
    assert result["sample"]["end_page"] == 2 and result["sample"]["truncated"] is True
    assert "Later readable" not in result["text"]
