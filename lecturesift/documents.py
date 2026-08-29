"""Safe text extraction for supported study documents."""

from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from pypdf import PdfReader
from pptx import Presentation

from .config import (
    DOCUMENT_EXTENSIONS,
    DOCUMENT_WORDS_PER_CREDIT_MINUTE,
    MAX_DOCUMENT_BYTES,
    MAX_DOCUMENT_CHARACTERS,
    MAX_DOCUMENT_PAGES,
)
from .errors import LectureSiftError


def _normalize_text(value: str) -> str:
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


def _validate_size(path: Path) -> None:
    size = path.stat().st_size
    if size <= 0:
        raise LectureSiftError("LS-DOC-01", "Belge boş görünüyor.", f"Empty document: {path.name}", 400)
    if size > MAX_DOCUMENT_BYTES:
        raise LectureSiftError(
            "LS-DOC-02",
            f"Bir belge en fazla {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB olabilir.",
            f"Document exceeds size limit: {path.name}",
            413,
        )


def _validate_office_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 10_000:
                raise ValueError("too many archive members")
            total_uncompressed = 0
            for member in members:
                name = member.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise ValueError("unsafe archive path")
                total_uncompressed += max(0, member.file_size)
                if member.compress_size and member.file_size / member.compress_size > 250:
                    raise ValueError("unsafe compression ratio")
            if total_uncompressed > 200 * 1024 * 1024:
                raise ValueError("archive expansion limit")
    except (zipfile.BadZipFile, ValueError) as exc:
        raise LectureSiftError(
            "LS-DOC-03",
            "Word veya PowerPoint belgesi bozuk ya da güvenli açılamıyor.",
            f"Unsafe Office archive {path.name}: {exc}",
            400,
        ) from exc


def _extract_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    with path.open("rb") as stream:
        signature = stream.read(5)
    if signature != b"%PDF-":
        raise LectureSiftError("LS-DOC-04", "PDF dosyası geçerli görünmüyor.", f"Invalid PDF signature: {path.name}", 400)
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise LectureSiftError("LS-DOC-05", "Şifreli PDF dosyaları desteklenmiyor.", "Encrypted PDF", 400)
        if len(reader.pages) > MAX_DOCUMENT_PAGES:
            raise LectureSiftError(
                "LS-DOC-06",
                f"PDF en fazla {MAX_DOCUMENT_PAGES} sayfa olabilir.",
                f"PDF page limit exceeded: {len(reader.pages)}",
                413,
            )
        parts = [_normalize_text(page.extract_text() or "") for page in reader.pages]
    except LectureSiftError:
        raise
    except Exception as exc:
        raise LectureSiftError("LS-DOC-04", "PDF güvenli biçimde okunamadı.", str(exc), 400) from exc
    return "\n\n".join(part for part in parts if part), {"pages": len(reader.pages)}


def _extract_docx(path: Path) -> tuple[str, dict[str, Any]]:
    _validate_office_archive(path)
    try:
        document = Document(str(path))
        parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
    except Exception as exc:
        raise LectureSiftError("LS-DOC-07", "Word belgesi okunamadı.", str(exc), 400) from exc
    return _normalize_text("\n\n".join(parts)), {"paragraphs": len(document.paragraphs), "tables": len(document.tables)}


def _extract_pptx(path: Path) -> tuple[str, dict[str, Any]]:
    _validate_office_archive(path)
    try:
        presentation = Presentation(str(path))
        if len(presentation.slides) > MAX_DOCUMENT_PAGES:
            raise LectureSiftError(
                "LS-DOC-06",
                f"Sunum en fazla {MAX_DOCUMENT_PAGES} slayt olabilir.",
                f"Presentation slide limit exceeded: {len(presentation.slides)}",
                413,
            )
        sections: list[str] = []
        for index, slide in enumerate(presentation.slides, 1):
            lines = []
            for shape in slide.shapes:
                text = getattr(shape, "text", "")
                if text and text.strip():
                    lines.append(text.strip())
            if lines:
                sections.append(f"SLIDE {index}\n" + "\n".join(lines))
    except LectureSiftError:
        raise
    except Exception as exc:
        raise LectureSiftError("LS-DOC-08", "PowerPoint sunumu okunamadı.", str(exc), 400) from exc
    return _normalize_text("\n\n".join(sections)), {"slides": len(presentation.slides)}


def _extract_text(path: Path) -> tuple[str, dict[str, Any]]:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise LectureSiftError("LS-DOC-09", "Metin dosyası geçerli görünmüyor.", "Binary content in text file", 400)
    for encoding in ("utf-8-sig", "utf-16", "cp1254", "latin-1"):
        try:
            return _normalize_text(raw.decode(encoding)), {"encoding": encoding}
        except UnicodeDecodeError:
            continue
    raise LectureSiftError("LS-DOC-09", "Metin dosyasının karakter kodlaması okunamadı.", "Unknown text encoding", 400)


def extract_documents(paths: list[Path]) -> dict[str, Any]:
    if not paths:
        raise LectureSiftError("LS-DOC-10", "En az bir belge ekle.", "No document paths", 400)
    sections: list[str] = []
    metadata: list[dict[str, Any]] = []
    for index, path in enumerate(paths, 1):
        _validate_size(path)
        suffix = path.suffix.casefold()
        if suffix not in DOCUMENT_EXTENSIONS:
            raise LectureSiftError("LS-DOC-11", "Bu belge biçimi desteklenmiyor.", f"Unsupported document: {suffix}", 400)
        if suffix == ".pdf":
            text, details = _extract_pdf(path)
        elif suffix == ".docx":
            text, details = _extract_docx(path)
        elif suffix == ".pptx":
            text, details = _extract_pptx(path)
        else:
            text, details = _extract_text(path)
        if not text.strip():
            raise LectureSiftError(
                "LS-DOC-12",
                "Belgeden metin çıkarılamadı. Taranmış PDF ise önce OCR uygulanmış bir sürüm yükle.",
                f"No extractable text: {path.name}",
                422,
            )
        sections.append(f"DOCUMENT {index}: {path.name}\n{text}")
        metadata.append({"name": path.name, "type": suffix.lstrip("."), **details, "characters": len(text)})
    combined = "\n\n".join(sections)
    if len(combined) > MAX_DOCUMENT_CHARACTERS:
        raise LectureSiftError(
            "LS-DOC-13",
            "Belgelerin toplam metni güvenli işleme sınırını aşıyor. Kaynağı bölerek yeniden yükle.",
            f"Document character limit exceeded: {len(combined)}",
            413,
        )
    words = len(re.findall(r"[^\W_]+", combined, flags=re.UNICODE))
    credit_minutes = max(1, math.ceil(words / DOCUMENT_WORDS_PER_CREDIT_MINUTE))
    return {
        "text": combined,
        "documents": metadata,
        "characters": len(combined),
        "words": words,
        "credit_minutes": credit_minutes,
        "credit_seconds": credit_minutes * 60,
    }
