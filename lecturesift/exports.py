import json
import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
from docx.shared import Inches
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as ReportImage
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:
    arabic_reshaper = None
    get_display = None


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
for cid_name in ("STSong-Light", "HeiseiMin-W3", "HYSMyeongJo-Medium"):
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(cid_name))
    except Exception:
        pass


COPY = {
    "tr": {"pack": "LectureSift Yapay Zekâ Çalışma Paketi", "summary": "Özet", "notes": "Akıllı Notlar", "points": "Önemli Noktalar", "terms": "Kavramlar ve Tanımlar", "exam": "Sınav Odaklı Noktalar", "original_transcript": "Orijinal Transkript", "translated_transcript": "Çevrilmiş Transkript", "transcript": "Transkript", "quiz": "Quiz", "questions": "Sorular ve Yanıtlar", "answer": "Doğru cevap", "explanation": "Açıklama", "cards": "Bilgi Kartları", "question": "Soru", "card_answer": "Cevap", "slides": "Çevrilmiş Slaytlar", "original_slides": "Orijinal Slaytlar", "no_slides": "Slayt bulunamadı."},
    "en": {"pack": "LectureSift AI Study Pack", "summary": "Summary", "notes": "Smart Notes", "points": "Key Points", "terms": "Terms and Definitions", "exam": "Exam Focus", "original_transcript": "Original Transcript", "translated_transcript": "Translated Transcript", "transcript": "Transcript", "quiz": "Quiz", "questions": "Questions and Answers", "answer": "Correct answer", "explanation": "Explanation", "cards": "Flashcards", "question": "Question", "card_answer": "Answer", "slides": "Translated Slides", "original_slides": "Original Slides", "no_slides": "No slides were found."},
    "de": {"pack": "LectureSift KI-Lernpaket", "summary": "Zusammenfassung", "notes": "Intelligente Notizen", "points": "Kernpunkte", "terms": "Begriffe und Definitionen", "exam": "Prüfungsfokus", "original_transcript": "Originaltranskript", "translated_transcript": "Übersetztes Transkript", "transcript": "Transkript", "quiz": "Quiz", "questions": "Fragen und Antworten", "answer": "Richtige Antwort", "explanation": "Erklärung", "cards": "Lernkarten", "question": "Frage", "card_answer": "Antwort", "slides": "Übersetzte Folien", "original_slides": "Originalfolien", "no_slides": "Keine Folien gefunden."},
    "fr": {"pack": "Dossier d’étude IA LectureSift", "summary": "Résumé", "notes": "Notes intelligentes", "points": "Points clés", "terms": "Termes et définitions", "exam": "Points d’examen", "original_transcript": "Transcription originale", "translated_transcript": "Transcription traduite", "transcript": "Transcription", "quiz": "Quiz", "questions": "Questions et réponses", "answer": "Bonne réponse", "explanation": "Explication", "cards": "Cartes mémoire", "question": "Question", "card_answer": "Réponse", "slides": "Diapositives traduites", "original_slides": "Diapositives originales", "no_slides": "Aucune diapositive trouvée."},
    "es": {"pack": "Paquete de estudio con IA LectureSift", "summary": "Resumen", "notes": "Apuntes inteligentes", "points": "Puntos clave", "terms": "Términos y definiciones", "exam": "Enfoque de examen", "original_transcript": "Transcripción original", "translated_transcript": "Transcripción traducida", "transcript": "Transcripción", "quiz": "Cuestionario", "questions": "Preguntas y respuestas", "answer": "Respuesta correcta", "explanation": "Explicación", "cards": "Tarjetas", "question": "Pregunta", "card_answer": "Respuesta", "slides": "Diapositivas traducidas", "original_slides": "Diapositivas originales", "no_slides": "No se encontraron diapositivas."},
    "it": {"pack": "Pacchetto di studio IA LectureSift", "summary": "Riassunto", "notes": "Appunti intelligenti", "points": "Punti chiave", "terms": "Termini e definizioni", "exam": "Focus d’esame", "original_transcript": "Trascrizione originale", "translated_transcript": "Trascrizione tradotta", "transcript": "Trascrizione", "quiz": "Quiz", "questions": "Domande e risposte", "answer": "Risposta corretta", "explanation": "Spiegazione", "cards": "Flashcard", "question": "Domanda", "card_answer": "Risposta", "slides": "Slide tradotte", "original_slides": "Slide originali", "no_slides": "Nessuna slide trovata."},
    "pt": {"pack": "Pacote de estudo com IA LectureSift", "summary": "Resumo", "notes": "Notas inteligentes", "points": "Pontos principais", "terms": "Termos e definições", "exam": "Foco para prova", "original_transcript": "Transcrição original", "translated_transcript": "Transcrição traduzida", "transcript": "Transcrição", "quiz": "Quiz", "questions": "Perguntas e respostas", "answer": "Resposta correta", "explanation": "Explicação", "cards": "Flashcards", "question": "Pergunta", "card_answer": "Resposta", "slides": "Slides traduzidos", "original_slides": "Slides originais", "no_slides": "Nenhum slide encontrado."},
    "ru": {"pack": "Учебный пакет LectureSift AI", "summary": "Резюме", "notes": "Умные заметки", "points": "Ключевые положения", "terms": "Термины и определения", "exam": "Подготовка к экзамену", "original_transcript": "Оригинальная расшифровка", "translated_transcript": "Переведённая расшифровка", "transcript": "Расшифровка", "quiz": "Тест", "questions": "Вопросы и ответы", "answer": "Правильный ответ", "explanation": "Объяснение", "cards": "Карточки", "question": "Вопрос", "card_answer": "Ответ", "slides": "Переведённые слайды", "original_slides": "Оригинальные слайды", "no_slides": "Слайды не найдены."},
    "ar": {"pack": "حزمة دراسة LectureSift بالذكاء الاصطناعي", "summary": "الملخص", "notes": "ملاحظات ذكية", "points": "النقاط الأساسية", "terms": "المصطلحات والتعريفات", "exam": "التركيز للاختبار", "original_transcript": "النص الأصلي", "translated_transcript": "النص المترجم", "transcript": "النص", "quiz": "اختبار", "questions": "الأسئلة والإجابات", "answer": "الإجابة الصحيحة", "explanation": "الشرح", "cards": "بطاقات المراجعة", "question": "السؤال", "card_answer": "الإجابة", "slides": "الشرائح المترجمة", "original_slides": "الشرائح الأصلية", "no_slides": "لم يتم العثور على شرائح."},
    "zh": {"pack": "LectureSift AI 学习包", "summary": "摘要", "notes": "智能笔记", "points": "要点", "terms": "术语与定义", "exam": "考试重点", "original_transcript": "原始文字稿", "translated_transcript": "翻译文字稿", "transcript": "文字稿", "quiz": "测验", "questions": "问题与答案", "answer": "正确答案", "explanation": "解析", "cards": "闪卡", "question": "问题", "card_answer": "答案", "slides": "翻译幻灯片", "original_slides": "原始幻灯片", "no_slides": "未找到幻灯片。"},
    "ja": {"pack": "LectureSift AI 学習パック", "summary": "要約", "notes": "スマートノート", "points": "重要ポイント", "terms": "用語と定義", "exam": "試験対策", "original_transcript": "元の文字起こし", "translated_transcript": "翻訳文字起こし", "transcript": "文字起こし", "quiz": "クイズ", "questions": "問題と解答", "answer": "正解", "explanation": "解説", "cards": "フラッシュカード", "question": "問題", "card_answer": "答え", "slides": "翻訳スライド", "original_slides": "元のスライド", "no_slides": "スライドが見つかりません。"},
    "ko": {"pack": "LectureSift AI 학습 패키지", "summary": "요약", "notes": "스마트 노트", "points": "핵심 포인트", "terms": "용어와 정의", "exam": "시험 핵심", "original_transcript": "원문 스크립트", "translated_transcript": "번역 스크립트", "transcript": "스크립트", "quiz": "퀴즈", "questions": "문제와 정답", "answer": "정답", "explanation": "해설", "cards": "플래시카드", "question": "문제", "card_answer": "답", "slides": "번역 슬라이드", "original_slides": "원본 슬라이드", "no_slides": "슬라이드를 찾지 못했습니다."},
    "hi": {"pack": "LectureSift AI अध्ययन पैक", "summary": "सारांश", "notes": "स्मार्ट नोट्स", "points": "मुख्य बिंदु", "terms": "शब्द और परिभाषाएँ", "exam": "परीक्षा केंद्रित बिंदु", "original_transcript": "मूल प्रतिलेख", "translated_transcript": "अनूदित प्रतिलेख", "transcript": "प्रतिलेख", "quiz": "क्विज़", "questions": "प्रश्न और उत्तर", "answer": "सही उत्तर", "explanation": "व्याख्या", "cards": "फ्लैशकार्ड", "question": "प्रश्न", "card_answer": "उत्तर", "slides": "अनूदित स्लाइड", "original_slides": "मूल स्लाइड", "no_slides": "कोई स्लाइड नहीं मिली।"},
}


def _copy(language: str) -> dict:
    return COPY.get(language, COPY["en"])


def _pdf_fonts(language: str) -> tuple[str, str]:
    if language == "zh":
        return "STSong-Light", "STSong-Light"
    if language == "ja":
        return "HeiseiMin-W3", "HeiseiMin-W3"
    if language == "ko":
        return "HYSMyeongJo-Medium", "HYSMyeongJo-Medium"
    return FONT_NAME, FONT_BOLD


def _shape(text: str, language: str) -> str:
    value = str(text or "")
    if language == "ar" and arabic_reshaper and get_display:
        try:
            return get_display(arabic_reshaper.reshape(value))
        except Exception:
            return value
    return value


def _styles(language: str) -> dict:
    base = getSampleStyleSheet()
    font_name, font_bold = _pdf_fonts(language)
    alignment = TA_RIGHT if language == "ar" else None
    return {
        "title": ParagraphStyle("LectureTitle", parent=base["Title"], fontName=font_bold, fontSize=22, leading=27, textColor=colors.HexColor("#172554"), alignment=TA_RIGHT if language == "ar" else TA_CENTER, spaceAfter=14),
        "heading": ParagraphStyle("LectureHeading", parent=base["Heading2"], fontName=font_bold, fontSize=14, leading=18, textColor=colors.HexColor("#4338CA"), alignment=alignment, spaceBefore=10, spaceAfter=7),
        "body": ParagraphStyle("LectureBody", parent=base["BodyText"], fontName=font_name, fontSize=10.5, leading=15, textColor=colors.HexColor("#1F2937"), alignment=alignment, spaceAfter=6),
        "small": ParagraphStyle("LectureSmall", parent=base["BodyText"], fontName=font_name, fontSize=8.5, leading=12, textColor=colors.HexColor("#64748B"), alignment=alignment),
    }


def _paragraph(text: str, style, language: str) -> Paragraph:
    return Paragraph(escape(_shape(str(text or ""), language)).replace("\n", "<br/>"), style)


def _write_pdf(path: Path, title: str, sections: list[tuple[str, list[str]]], language: str) -> None:
    styles = _styles(language)
    copy = _copy(language)
    document = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=17 * mm, bottomMargin=17 * mm, title=title, author="LectureSift")
    story = [_paragraph(title, styles["title"], language), _paragraph(copy["pack"], styles["small"], language), Spacer(1, 8)]
    for heading, paragraphs in sections:
        if heading:
            story.append(_paragraph(heading, styles["heading"], language))
        for value in paragraphs:
            story.append(_paragraph(value, styles["body"], language))
    document.build(story)


def _write_docx(path: Path, title: str, sections: list[tuple[str, list[str]]], language: str) -> None:
    copy = _copy(language)
    document = Document()
    document.add_heading(title, 0)
    document.add_paragraph(copy["pack"])
    for heading, paragraphs in sections:
        if heading:
            document.add_heading(heading, level=1)
        for value in paragraphs:
            for paragraph in str(value or "").split("\n"):
                document.add_paragraph(paragraph)
    document.save(path)


def _notes_text(pack: dict, language: str) -> str:
    copy = _copy(language)
    blocks: list[str] = []
    if pack.get("key_points"):
        blocks.append(copy["points"].upper() + "\n" + "\n".join(f"• {item}" for item in pack["key_points"]))
    if pack.get("important_terms"):
        blocks.append(copy["terms"].upper() + "\n" + "\n".join(f"• {item.get('term', '')}: {item.get('definition', '')}" for item in pack["important_terms"]))
    for note in pack.get("notes", []):
        value = f"{note.get('heading', '')}\n{note.get('content', '')}"
        bullets = note.get("bullets") or []
        if bullets:
            value += "\n" + "\n".join(f"• {bullet}" for bullet in bullets)
        blocks.append(value.strip())
    if pack.get("exam_focus"):
        blocks.append(copy["exam"].upper() + "\n" + "\n".join(f"• {item}" for item in pack["exam_focus"]))
    return "\n\n".join(blocks).strip()


def _quiz_text(quiz: list[dict], language: str) -> str:
    copy = _copy(language)
    blocks = []
    for index, item in enumerate(quiz, 1):
        options = "\n".join(f"  {chr(65 + option_index)}. {option}" for option_index, option in enumerate(item.get("options", [])))
        answer_index = int(item.get("answer_index", 0))
        blocks.append(f"{index}. {item.get('question', '')}\n{options}\n{copy['answer']}: {chr(65 + answer_index)}\n{copy['explanation']}: {item.get('explanation', '')}")
    return "\n\n".join(blocks)


def _flashcards_text(cards: list[dict], language: str) -> str:
    copy = _copy(language)
    return "\n\n".join(f"{index}. {copy['question']}: {item.get('front', '')}\n{copy['card_answer']}: {item.get('back', '')}" for index, item in enumerate(cards, 1))


def _slide_filename(slide: dict, translated: bool) -> str:
    return str(slide.get("translated_file") or slide.get("file") or "") if translated else str(slide.get("file") or "")


def _write_slides_pdf(path: Path, title: str, slides: list[dict], slides_dir: Path, language: str, translated: bool) -> None:
    styles = _styles(language)
    copy = _copy(language)
    document = SimpleDocTemplate(str(path), pagesize=landscape(A4), rightMargin=15 * mm, leftMargin=15 * mm, topMargin=12 * mm, bottomMargin=12 * mm, title=f"{title} - {copy['slides'] if translated else copy['original_slides']}", author="LectureSift")
    story = []
    max_width, max_height = 260 * mm, 155 * mm
    included = 0
    for slide in slides:
        image_path = slides_dir / _slide_filename(slide, translated)
        if not image_path.exists():
            continue
        width, height = ImageReader(str(image_path)).getSize()
        scale = min(max_width / width, max_height / height)
        story.append(_paragraph(f"{title} — {slide.get('timestamp', '')}", styles["heading"], language))
        story.append(ReportImage(str(image_path), width=width * scale, height=height * scale))
        included += 1
        if included < len(slides):
            story.append(PageBreak())
    if not story:
        story.append(_paragraph(copy["no_slides"], styles["body"], language))
    document.build(story)


def _write_slides_docx(path: Path, title: str, slides: list[dict], slides_dir: Path, language: str, translated: bool) -> None:
    copy = _copy(language)
    document = Document()
    document.add_heading(f"{title} - {copy['slides'] if translated else copy['original_slides']}", 0)
    included = 0
    for slide in slides:
        image_path = slides_dir / _slide_filename(slide, translated)
        if not image_path.exists():
            continue
        document.add_heading(slide.get("timestamp", ""), level=1)
        document.add_picture(str(image_path), width=Inches(6.5))
        included += 1
        if included < len(slides):
            document.add_page_break()
    document.save(path)


def _artifact(path: Path, label: str) -> dict:
    return {"file": path.name, "label": label, "format": path.suffix.removeprefix(".").upper(), "size_bytes": path.stat().st_size}


def _save_result(job_dir: Path, result: dict, artifacts: list[dict]) -> None:
    (job_dir / "result.json").write_text(json.dumps({**result, "artifacts": artifacts}, ensure_ascii=False, indent=2), encoding="utf-8")


def build_artifacts(job_dir: Path, result: dict, slides_dir: Path) -> tuple[list[dict], Path]:
    package_dir = job_dir / "package"
    package_dir.mkdir(parents=True, exist_ok=True)
    formats = set(result.get("options", {}).get("output_formats") or ["pdf"])
    language = str(result.get("options", {}).get("output_language") or "tr")
    copy = _copy(language)
    title = result.get("title") or "LectureSift"
    original = result.get("transcript_original", "") or "—"
    translated = result.get("transcript_translated", "")
    notes_text = _notes_text(result, language)
    quiz_text = _quiz_text(result.get("quiz", []), language)
    cards_text = _flashcards_text(result.get("flashcards", []), language)
    documents = [
        ("Ozet", copy["summary"], f"{title} - {copy['summary']}", [(copy["summary"], [result.get("summary", "")])], result.get("summary", "")),
        ("Ders_Notlari", copy["notes"], f"{title} - {copy['notes']}", [(copy["notes"], [notes_text])], notes_text),
        ("Transkript_Orijinal", copy["original_transcript"], f"{title} - {copy['original_transcript']}", [(copy["transcript"], [original])], original),
        ("Quiz", copy["quiz"], f"{title} - {copy['quiz']}", [(copy["questions"], [quiz_text])], quiz_text),
        ("Flashcards", copy["cards"], f"{title} - {copy['cards']}", [(copy["cards"], [cards_text])], cards_text),
    ]
    if translated:
        documents.insert(3, ("Transkript_Ceviri", copy["translated_transcript"], f"{title} - {copy['translated_transcript']}", [(copy["transcript"], [translated])], translated))

    artifacts: list[dict] = []
    for stem, label, document_title, sections, plain_text in documents:
        if "pdf" in formats:
            path = package_dir / f"{stem}.pdf"
            _write_pdf(path, document_title, sections, language)
            artifacts.append(_artifact(path, f"{label} (PDF)"))
        if "docx" in formats:
            path = package_dir / f"{stem}.docx"
            _write_docx(path, document_title, sections, language)
            artifacts.append(_artifact(path, f"{label} (Word)"))
        if "txt" in formats:
            path = package_dir / f"{stem}.txt"
            path.write_text(str(plain_text or ""), encoding="utf-8")
            artifacts.append(_artifact(path, f"{label} (TXT)"))

    slides = result.get("slides", [])
    if slides:
        translated_slides = any(slide.get("translated_file") for slide in slides)
        if "pdf" in formats:
            path = package_dir / "Slaytlar.pdf"
            _write_slides_pdf(path, title, slides, slides_dir, language, translated=True)
            artifacts.append(_artifact(path, f"{copy['slides'] if translated_slides else copy['original_slides']} (PDF)"))
            if translated_slides:
                original_path = package_dir / "Slaytlar_Orijinal.pdf"
                _write_slides_pdf(original_path, title, slides, slides_dir, language, translated=False)
                artifacts.append(_artifact(original_path, f"{copy['original_slides']} (PDF)"))
        if "docx" in formats:
            path = package_dir / "Slaytlar.docx"
            _write_slides_docx(path, title, slides, slides_dir, language, translated=True)
            artifacts.append(_artifact(path, f"{copy['slides'] if translated_slides else copy['original_slides']} (Word)"))
            if translated_slides:
                original_path = package_dir / "Slaytlar_Orijinal.docx"
                _write_slides_docx(original_path, title, slides, slides_dir, language, translated=False)
                artifacts.append(_artifact(original_path, f"{copy['original_slides']} (Word)"))
        if "txt" in formats:
            path = package_dir / "Slaytlar.txt"
            path.write_text("\n".join(f"{item.get('timestamp', '')} — {_slide_filename(item, True)}" for item in slides), encoding="utf-8")
            artifacts.append(_artifact(path, f"{copy['slides']} (TXT)"))

    _save_result(job_dir, result, artifacts)
    zip_base = job_dir / "LectureSift_Study_Pack_V4"
    shutil.make_archive(str(zip_base), "zip", root_dir=package_dir)
    return artifacts, zip_base.with_suffix(".zip")


def build_binary_artifact(job_dir: Path, result: dict, source: Path, filename: str, label: str) -> tuple[list[dict], Path]:
    package_dir = job_dir / "package"
    package_dir.mkdir(parents=True, exist_ok=True)
    destination = package_dir / filename
    shutil.copy2(source, destination)
    artifacts = [_artifact(destination, label)]
    _save_result(job_dir, result, artifacts)
    internal = package_dir / "_lecturesift"
    internal.mkdir(parents=True, exist_ok=True)
    (internal / "result.json").write_text(json.dumps({**result, "artifacts": artifacts}, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_base = job_dir / "LectureSift_Download"
    shutil.make_archive(str(zip_base), "zip", root_dir=package_dir)
    return artifacts, zip_base.with_suffix(".zip")
