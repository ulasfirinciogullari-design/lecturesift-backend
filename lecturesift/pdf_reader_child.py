"""Read one PDF's structure and native text in a separate, resource-capped process.

documents.py starts this file with ``python -I`` (no user site directory,
no PYTHON* environment variables, no script directory on sys.path), so it
imports only the standard library, pypdf and pypdfium2, never the LectureSift
package.
The parent enforces the wall-clock timeout; this process caps its own address
space and CPU time before it touches the file, so a pathological PDF fails
here instead of hanging or exhausting the worker that handles every job.

Usage: python -I pdf_reader_child.py <path> <max_pages> <memory_bytes> <cpu_seconds> <mode>
``mode`` is ``inspect`` (encryption and page count) or ``text`` (also the
native text of every page). One JSON object is written to stdout.
"""

from __future__ import annotations

import json
import resource
import sys


def _limit(kind: int, value: int) -> None:
    _soft, hard = resource.getrlimit(kind)
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
    resource.setrlimit(kind, (value, hard))


def _native_pages(path: str, page_count: int) -> tuple[list[str], str]:
    """PDFium's native text engine first, with pypdf as a compatibility fallback."""
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(path)
        try:
            if len(document) != page_count:
                raise ValueError("PDF page count changed between readers")
            parts: list[str] = []
            for page_index in range(page_count):
                page = document[page_index]
                try:
                    text_page = page.get_textpage()
                    try:
                        parts.append(text_page.get_text_range() or "")
                    finally:
                        text_page.close()
                finally:
                    page.close()
            return parts, "pdfium"
        finally:
            document.close()
    except MemoryError:
        raise
    except Exception:
        from pypdf import PdfReader

        reader = PdfReader(path, strict=False)
        return [page.extract_text() or "" for page in reader.pages], "pypdf"


def main(argv: list[str]) -> int:
    path, max_pages, memory_bytes, cpu_seconds, mode = argv[1:6]
    _limit(resource.RLIMIT_AS, int(memory_bytes))
    _limit(resource.RLIMIT_CPU, int(cpu_seconds))
    try:
        from pypdf import PdfReader

        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            result: dict = {"ok": True, "encrypted": True}
        else:
            pages = len(reader.pages)
            result = {"ok": True, "encrypted": False, "pages": pages}
            if mode == "text" and pages <= int(max_pages):
                texts, engine = _native_pages(path, pages)
                result.update(texts=texts, engine=engine)
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
