"""PDF parsing runs in a separate process whose time and memory are capped."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

import lecturesift.documents as documents
from lecturesift.errors import LectureSiftError


def _text_pdf(path: Path, pages: list[str]) -> Path:
    pdf = canvas.Canvas(str(path))
    for text in pages:
        pdf.drawString(72, 760, text)
        pdf.showPage()
    pdf.save()
    return path


def test_full_pdf_text_is_read_in_the_isolated_reader(tmp_path, monkeypatch):
    path = _text_pdf(tmp_path / "lesson.pdf", [
        "Energy is conserved in a closed system and only changes form.",
        "Momentum is conserved when no external force acts on the system.",
    ])
    calls = []
    real = documents._read_pdf_isolated

    def spy(path, *, max_pages, with_text):
        calls.append((Path(path).name, max_pages, with_text))
        return real(path, max_pages=max_pages, with_text=with_text)

    monkeypatch.setattr(documents, "_read_pdf_isolated", spy)
    result = documents.extract_documents([path], enable_ocr=False)
    assert calls == [("lesson.pdf", documents.MAX_DOCUMENT_PAGES, True)]
    assert "Energy is conserved" in result["text"] and "Momentum is conserved" in result["text"]
    assert result["pages"] == 2 and result["documents"][0]["text_engine"] == "pdfium"


def test_encrypted_and_oversized_pdfs_keep_their_specific_errors(tmp_path, monkeypatch):
    source = _text_pdf(tmp_path / "plain.pdf", ["A page of native text for the encryption check."] * 3)
    writer = PdfWriter(clone_from=str(source))
    writer.encrypt("secret-password")
    encrypted = tmp_path / "encrypted.pdf"
    with encrypted.open("wb") as stream:
        writer.write(stream)
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([encrypted], enable_ocr=False)
    assert error.value.code == "LS-DOC-05"

    monkeypatch.setattr(documents, "MAX_DOCUMENT_PAGES", 2)
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([source], enable_ocr=False)
    assert error.value.code == "LS-DOC-06"


def test_hanging_pdf_reader_is_killed_at_the_timeout(tmp_path, monkeypatch):
    path = _text_pdf(tmp_path / "lesson.pdf", ["Readable page text for a hanging parser."])
    hanging = tmp_path / "hanging_reader.py"
    hanging.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(documents, "_PDF_READER_CHILD", hanging)
    monkeypatch.setattr(documents, "_PDF_READER_TIMEOUT_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], enable_ocr=False)
    assert time.monotonic() - started < 15
    assert error.value.code == "LS-DOC-04"
    assert error.value.technical_message == "pdf_reader_timeout"


def test_pdf_reader_memory_cap_turns_a_blow_up_into_a_rejection(tmp_path, monkeypatch):
    path = _text_pdf(tmp_path / "lesson.pdf", ["Readable page text for a memory-capped parser."])
    # Far below what pypdf needs, so the capped child cannot finish the parse.
    monkeypatch.setattr(documents, "_pdf_reader_memory_bytes", lambda: 24 * 1024 * 1024)
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], enable_ocr=False)
    assert error.value.code == "LS-DOC-04"
    assert error.value.technical_message.startswith("pdf_reader_failed:")


def test_pdf_reader_memory_cap_follows_the_container(monkeypatch):
    mib = 1024 * 1024
    for container, expected in ((None, 768), (512 * mib, 384), (1024 * mib, 512), (3584 * mib, 768)):
        monkeypatch.setattr(documents, "_container_memory_limit_bytes", lambda value=container: value)
        assert documents._pdf_reader_memory_bytes() == expected * mib


def test_reader_that_cannot_start_is_an_infrastructure_fault_not_a_bad_file(tmp_path, monkeypatch):
    path = _text_pdf(tmp_path / "lesson.pdf", ["Readable page text."])

    def refuse(*args, **kwargs):
        raise OSError("fork failed")

    monkeypatch.setattr(documents.subprocess, "run", refuse)
    with pytest.raises(RuntimeError):
        documents.extract_documents([path], enable_ocr=False)


def test_reader_stops_at_the_character_cap_inside_the_child(tmp_path, monkeypatch):
    path = _text_pdf(tmp_path / "long.pdf", ["A page with enough native text to pass a tiny cap."] * 3)
    monkeypatch.setattr(documents, "MAX_DOCUMENT_CHARACTERS", 60)
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], enable_ocr=False)
    assert error.value.code == "LS-DOC-13"
    assert error.value.technical_message == "Document character limit exceeded: long.pdf"


def test_reader_output_is_capped_before_the_parent_reads_it(tmp_path, monkeypatch):
    path = _text_pdf(tmp_path / "lesson.pdf", ["Readable page text that does not fit a tiny output cap."])
    monkeypatch.setattr(documents, "_pdf_reader_max_output_bytes", lambda _max_pages: 16)
    with pytest.raises(LectureSiftError) as error:
        documents.extract_documents([path], enable_ocr=False)
    assert error.value.code == "LS-DOC-04"
    assert error.value.technical_message.startswith("pdf_reader_failed:")


def test_child_normalizes_exactly_like_the_parent():
    spec = importlib.util.spec_from_file_location("pdf_reader_child_copy", documents._PDF_READER_CHILD)
    child = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(child)
    samples = [
        "", "  a\t\t b  \n\n\n c \x00d\r\n", "\u2028x\x0cy\x1c\n \n z", "\ud800 lone \t surrogate\n",
        "x" * 10 + "\n" * 5 + "  " + "y",
    ]
    for sample in samples:
        assert child._normalize_text(sample) == documents._normalize_text(sample)
        assert documents._normalize_text(child._normalize_text(sample)) == child._normalize_text(sample)
    assert documents.MAX_DOCUMENT_CHARACTERS * 12 < documents._pdf_reader_max_output_bytes(500)
