import json
import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
FONT_NAME = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

if FONT_PATH.exists():
    pdfmetrics.registerFont(TTFont("LectureSift", str(FONT_PATH)))
    FONT_NAME = "LectureSift"
if FONT_BOLD_PATH.exists():
    pdfmetrics.registerFont(TTFont("LectureSift-Bold", str(FONT_BOLD_PATH)))
    FONT_BOLD = "LectureSift-Bold"


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "LectureTitle",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=22,
            leading=27,
            textColor=colors.HexColor("#172554"),
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "heading": ParagraphStyle(
            "LectureHeading",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#4338CA"),
            spaceBefore=10,
            spaceAfter=7,
        ),
        "body": ParagraphStyle(
            "LectureBody",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=15,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "LectureSmall",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#64748B"),
        ),
    }


def _paragraph(text: str, style) -> Paragraph:
    return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)


def _write_pdf(path: Path, title: str, sections: list[tuple[str, list[str]]]) -> None:
    styles = _styles()
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=17 * mm,
        title=title,
        author="LectureSift",
    )
    story = [_paragraph(title, styles["title"]), _paragraph("LectureSift AI Study Pack", styles["small"]), Spacer(1, 8)]
    for heading, paragraphs in sections:
        if heading:
            story.append(_paragraph(heading, styles["heading"]))
        for value in paragraphs:
            story.append(_paragraph(value, styles["body"]))
    document.build(story)


def _notes_text(pack: dict) -> str:
    blocks: list[str] = []
    if pack.get("key_points"):
        blocks.append("ÖNEMLİ NOKTALAR\n" + "\n".join(f"• {item}" for item in pack["key_points"]))
    if pack.get("important_terms"):
        blocks.append(
            "KAVRAMLAR VE TANIMLAR\n"
            + "\n".join(f"• {item.get('term', '')}: {item.get('definition', '')}" for item in pack["important_terms"])
        )
    for note in pack.get("notes", []):
        value = f"{note.get('heading', '')}\n{note.get('content', '')}"
        bullets = note.get("bullets") or []
        if bullets:
            value += "\n" + "\n".join(f"• {bullet}" for bullet in bullets)
        blocks.append(value.strip())
    if pack.get("exam_focus"):
        blocks.append("SINAV ODAKLI NOKTALAR\n" + "\n".join(f"• {item}" for item in pack["exam_focus"]))
    return "\n\n".join(blocks).strip()


def _quiz_text(quiz: list[dict]) -> str:
    blocks = []
    for index, item in enumerate(quiz, 1):
        options = "\n".join(f"  {chr(65 + option_index)}. {option}" for option_index, option in enumerate(item.get("options", [])))
        answer_index = int(item.get("answer_index", 0))
        blocks.append(
            f"{index}. {item.get('question', '')}\n{options}\n"
            f"Doğru cevap: {chr(65 + answer_index)}\nAçıklama: {item.get('explanation', '')}"
        )
    return "\n\n".join(blocks)


def _flashcards_text(cards: list[dict]) -> str:
    return "\n\n".join(
        f"{index}. Soru: {item.get('front', '')}\nCevap: {item.get('back', '')}"
        for index, item in enumerate(cards, 1)
    )


def build_artifacts(job_dir: Path, result: dict, slides_dir: Path) -> tuple[list[dict], Path]:
    package_dir = job_dir / "package"
    package_dir.mkdir(parents=True, exist_ok=True)

    title = result.get("title") or "LectureSift Ders Paketi"
    summary = result.get("summary", "")
    notes = _notes_text(result)
    original = result.get("transcript_original", "") or "Videoda ses parçası bulunamadı."
    translated = result.get("transcript_translated", "")
    quiz_text = _quiz_text(result.get("quiz", []))
    flashcards_text = _flashcards_text(result.get("flashcards", []))

    text_files = {
        "Ozet.txt": summary,
        "Ders_Notlari.txt": notes,
        "Transkript_Orijinal.txt": original,
        "Quiz.txt": quiz_text,
        "Flashcards.txt": flashcards_text,
    }
    if translated:
        text_files["Transkript_Ceviri.txt"] = translated
    for filename, content in text_files.items():
        (package_dir / filename).write_text(content, encoding="utf-8")

    _write_pdf(package_dir / "Ozet.pdf", f"{title} - Özet", [("Özet", [summary])])
    _write_pdf(package_dir / "Ders_Notlari.pdf", f"{title} - Ders Notları", [("Ders Notları", [notes])])
    _write_pdf(package_dir / "Transkript_Orijinal.pdf", f"{title} - Orijinal Transkript", [("Transkript", [original])])
    _write_pdf(package_dir / "Quiz.pdf", f"{title} - Quiz", [("Sorular ve Yanıtlar", [quiz_text])])
    _write_pdf(package_dir / "Flashcards.pdf", f"{title} - Bilgi Kartları", [("Bilgi Kartları", [flashcards_text])])
    if translated:
        _write_pdf(package_dir / "Transkript_Ceviri.pdf", f"{title} - Çevrilmiş Transkript", [("Transkript", [translated])])

    if slides_dir.exists():
        shutil.copytree(slides_dir, package_dir / "Slaytlar")

    (package_dir / "diagnostics.json").write_text(
        json.dumps(result.get("diagnostics", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    labels = {
        "Ozet.pdf": "Özet (PDF)",
        "Ozet.txt": "Özet (TXT)",
        "Ders_Notlari.pdf": "Ders Notları (PDF)",
        "Ders_Notlari.txt": "Ders Notları (TXT)",
        "Transkript_Orijinal.pdf": "Orijinal Transkript (PDF)",
        "Transkript_Orijinal.txt": "Orijinal Transkript (TXT)",
        "Transkript_Ceviri.pdf": "Çevrilmiş Transkript (PDF)",
        "Transkript_Ceviri.txt": "Çevrilmiş Transkript (TXT)",
        "Quiz.pdf": "Quiz (PDF)",
        "Quiz.txt": "Quiz (TXT)",
        "Flashcards.pdf": "Bilgi Kartları (PDF)",
        "Flashcards.txt": "Bilgi Kartları (TXT)",
    }
    artifacts = []
    for filename, label in labels.items():
        path = package_dir / filename
        if path.exists():
            artifacts.append(
                {
                    "file": filename,
                    "label": label,
                    "format": path.suffix.removeprefix(".").upper(),
                    "size_bytes": path.stat().st_size,
                }
            )

    complete_result = {**result, "artifacts": artifacts}
    (package_dir / "result.json").write_text(
        json.dumps(complete_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (job_dir / "result.json").write_text(
        json.dumps(complete_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    zip_base = job_dir / "LectureSift_Study_Pack_V4"
    shutil.make_archive(str(zip_base), "zip", root_dir=package_dir)
    return artifacts, zip_base.with_suffix(".zip")
