"""Read one PDF's structure and native text in a separate, resource-capped process.

documents.py starts this file with ``python -I`` (no user site directory,
no PYTHON* environment variables, no script directory on sys.path), so it
imports only the standard library, pypdf and pypdfium2, never the LectureSift
package.
The parent enforces the wall-clock timeout; this process caps its own address
space and CPU time before it touches the file, so a pathological PDF fails
here instead of hanging or exhausting the worker that handles every job.

Usage: python -I pdf_reader_child.py <path> <max_pages> <memory_bytes> <cpu_seconds> <mode>
       <max_characters> <max_output_bytes>
``mode`` is ``inspect`` (encryption and page count) or ``text`` (also the
normalized native text of every page). Text extraction stops as soon as the
pages exceed ``max_characters`` (error ``character_limit``), and stdout, a
file the parent provides, is capped at ``max_output_bytes``, so the parent
never reads more than that. One JSON object is written to stdout.
"""

from __future__ import annotations

import json
import re
import resource
import sys


class _CharacterLimit(Exception):
    """The PDF's text is longer than one job may process."""


def _normalize_text(value: str) -> str:
    """Identical to lecturesift.documents._normalize_text (pinned by a test)."""
    value = value.replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    output: list[str] = []
    blank = False
    for line in lines:
        if line:
            output.append(line)
            blank = False
        elif output and not blank:
            output.append("")
            blank = True
    return "\n".join(output).strip()


def _bounded(pages, max_characters: int) -> list[str]:
    """Normalize page texts, stopping once their joined length passes the cap.

    The parent joins non-empty pages with a blank line and may only add OCR
    text, so passing the cap here means the job's text would pass it too.
    """
    texts: list[str] = []
    total = 0
    for raw in pages:
        text = _normalize_text(raw or "")
        if text:
            total += len(text) + (2 if total else 0)
            if total > max_characters:
                raise _CharacterLimit
        texts.append(text)
    return texts


def _limit(kind: int, value: int) -> None:
    _soft, hard = resource.getrlimit(kind)
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
    resource.setrlimit(kind, (value, hard))


def _pdfium_pages(document, page_count: int):
    for page_index in range(page_count):
        page = document[page_index]
        try:
            text_page = page.get_textpage()
            try:
                yield text_page.get_text_range() or ""
            finally:
                text_page.close()
        finally:
            page.close()


def _native_pages(path: str, page_count: int, max_characters: int) -> tuple[list[str], str]:
    """PDFium's native text engine first, with pypdf as a compatibility fallback."""
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(path)
        try:
            if len(document) != page_count:
                raise ValueError("PDF page count changed between readers")
            return _bounded(_pdfium_pages(document, page_count), max_characters), "pdfium"
        finally:
            document.close()
    except (MemoryError, _CharacterLimit):
        raise
    except Exception:
        from pypdf import PdfReader

        reader = PdfReader(path, strict=False)
        pages = (page.extract_text() or "" for page in reader.pages)
        return _bounded(pages, max_characters), "pypdf"


def main(argv: list[str]) -> int:
    path, max_pages, memory_bytes, cpu_seconds, mode, max_characters, max_output_bytes = argv[1:8]
    _limit(resource.RLIMIT_AS, int(memory_bytes))
    _limit(resource.RLIMIT_CPU, int(cpu_seconds))
    # stdout is a regular file: writing past this fails instead of growing it.
    _limit(resource.RLIMIT_FSIZE, int(max_output_bytes))
    try:
        from pypdf import PdfReader

        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            result: dict = {"ok": True, "encrypted": True}
        else:
            pages = len(reader.pages)
            result = {"ok": True, "encrypted": False, "pages": pages}
            if mode == "text" and pages <= int(max_pages):
                texts, engine = _native_pages(path, pages, int(max_characters))
                result.update(texts=texts, engine=engine)
    except _CharacterLimit:
        result = {"ok": False, "error": "character_limit"}
    except MemoryError:
        result = {"ok": False, "error": "memory_limit"}
    except Exception as exc:  # The parent maps every reader failure to one public error.
        result = {"ok": False, "error": type(exc).__name__[:80]}
    # ASCII escapes keep lone surrogates from odd PDF encodings intact for
    # the parent's own normalization instead of failing the write here.
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
