"""AI transcription, complete translation, and hierarchical study-pack generation."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from openai import OpenAI

from .config import LANGUAGE_NAMES, OPENAI_API_KEY, SUMMARY_STYLES
from .errors import LectureSiftError


_CLIENT = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def _client() -> OpenAI:
    if not _CLIENT:
        raise LectureSiftError("LS-AI-03", "Yapay zekâ servisi henüz yapılandırılmamış.", "OPENAI_API_KEY is not configured.", 503)
    return _CLIENT


def transcribe(audio_path: Path, language: str) -> str:
    with open(audio_path, "rb") as stream:
        arguments = {"model": "gpt-4o-mini-transcribe", "file": stream}
        if language and language != "auto":
            arguments["language"] = language
        response = _client().audio.transcriptions.create(**arguments)
    return getattr(response, "text", str(response)).strip()


def _chunk_text(text: str, maximum: int = 12000) -> list[str]:
    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > maximum:
        split = remaining.rfind("\n", 0, maximum)
        if split < maximum // 2:
            split = remaining.rfind(". ", 0, maximum)
        if split < maximum // 2:
            split = maximum
        chunks.append(remaining[:split].strip())
        remaining = remaining[split:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _safe_json(value: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", value.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def _translate_chunk(chunk: str, language_name: str, attempt: int) -> tuple[str, bool]:
    insistence = "This is a retry because the previous answer appeared incomplete. Translate every sentence; do not summarize." if attempt else "Translate every sentence and paragraph without summarizing, shortening, or omitting repetitions."
    response = _client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You translate lecture transcripts faithfully and completely. Preserve names, technical terms, speaker intent, paragraph order, uncertainty, examples, and repetitions that carry meaning. If the source is already in the target language, mark same_language true and return it unchanged. Return JSON only with keys same_language (boolean), complete (boolean), and translation (string). " + insistence},
            {"role": "user", "content": f"Target language: {language_name}\n\nSOURCE:\n{chunk}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    value = _safe_json(response.choices[0].message.content or "{}")
    translated = str(value.get("translation") or "").strip()
    same_language = bool(value.get("same_language"))
    complete = bool(value.get("complete"))
    plausible_length = same_language or len(translated) >= max(20, int(len(chunk) * 0.22))
    return (chunk if same_language else translated), bool(complete and translated and plausible_length)


def translate_transcript(transcript: str, output_language: str) -> str:
    if not transcript.strip() or output_language not in LANGUAGE_NAMES:
        return ""
    language_name = LANGUAGE_NAMES[output_language]
    translated_chunks: list[str] = []
    for chunk in _chunk_text(transcript):
        translated = ""
        complete = False
        for attempt in range(2):
            translated, complete = _translate_chunk(chunk, language_name, attempt)
            if complete:
                break
        if not complete:
            raise LectureSiftError("LS-AI-04", "Transkript çevirisi eksiksiz tamamlanamadı. İşlem yarım çeviri üretmeden durduruldu.", "Translation chunk failed completeness validation.", 502)
        translated_chunks.append(translated)
    return "\n\n".join(translated_chunks).strip()


def empty_study_pack() -> dict:
    return {
        "title": "LectureSift", "summary": "", "key_points": [],
        "important_terms": [], "notes": [], "exam_focus": [], "quiz": [],
        "flashcards": [], "sections": [],
        "source_coverage": {"complete": True, "chunks": 0, "characters": 0},
    }


def _normalize_flashcards(items: object, language: str, maximum: int) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(items, list):
        return normalized
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        front = " ".join(str(item.get("front") or item.get("question") or "").split())
        back = " ".join(str(item.get("back") or item.get("answer") or "").split())
        if not front or not back:
            continue
        if not front.rstrip().endswith(("?", "？", "؟")):
            front = f"{front} nedir?" if language == "tr" else f"What is {front}?"
        key = (front.casefold(), back.casefold())
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"front": front, "back": back})
        if len(normalized) >= maximum:
            break
    return normalized


def _normalize_quiz(items: object, maximum: int) -> list[dict]:
    normalized: list[dict] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return normalized
    for item in items:
        if not isinstance(item, dict):
            continue
        question = " ".join(str(item.get("question") or "").split())
        options = [str(value).strip() for value in item.get("options") or [] if str(value).strip()]
        if not question or len(options) != 4 or question.casefold() in seen:
            continue
        seen.add(question.casefold())
        try:
            answer_index = max(0, min(3, int(item.get("answer_index", 0))))
        except (TypeError, ValueError):
            answer_index = 0
        normalized.append({"question": question, "options": options, "answer_index": answer_index, "explanation": str(item.get("explanation") or "").strip()})
        if len(normalized) >= maximum:
            break
    return normalized


def _source_chunks(transcript: str, transcript_segments: list[dict] | None) -> list[dict]:
    maximum = 18000
    if transcript_segments:
        chunks: list[dict] = []
        buffer: list[str] = []
        start_second: float | None = None
        end_second = 0.0
        characters = 0
        for segment in transcript_segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            if buffer and characters + len(text) > maximum:
                chunks.append({"text": "\n\n".join(buffer), "start_second": start_second or 0.0, "end_second": end_second})
                buffer = []
                characters = 0
                start_second = None
            if start_second is None:
                start_second = float(segment.get("start_second") or 0)
            end_second = float(segment.get("end_second") or start_second or 0)
            buffer.append(text)
            characters += len(text)
        if buffer:
            chunks.append({"text": "\n\n".join(buffer), "start_second": start_second or 0.0, "end_second": end_second})
        if chunks:
            return chunks
    return [{"text": value, "start_second": None, "end_second": None} for value in _chunk_text(transcript, maximum)]


def _target_words(summary_style: str) -> str:
    return {"short": "about 250-450 words when the source is substantial", "standard": "about 700-1400 words when the source is substantial", "detailed": "about 1400-2600 words when the source is substantial", "exam": "about 700-1600 words when the source is substantial", "five_minute": "about 350-650 words when the source is substantial"}.get(summary_style, "about 700-1400 words when the source is substantial")


def _section_analysis(chunk: dict, *, index: int, total: int, language_name: str, quiz_count: int, flashcard_count: int) -> dict:
    prompt = f"""
You are analyzing section {index} of {total} from one lecture. Use only this
section. Output language: {language_name}. Return JSON only:
{{
  "heading":"string",
  "summary":"complete section summary",
  "key_points":["string"],
  "important_terms":[{{"term":"string","definition":"string"}}],
  "notes":[{{"heading":"string","content":"string","bullets":["string"]}}],
  "exam_focus":["string"],
  "quiz":[{{"question":"string","options":["A","B","C","D"],"answer_index":0,"explanation":"string"}}],
  "flashcards":[{{"front":"question?","back":"answer"}}]
}}
Preserve all definitions, distinctions, mechanisms, examples, lecturer emphasis,
uncertainty, and conclusions. Do not invent or silently correct unclear claims.
Generate up to {quiz_count} useful quiz candidates and {flashcard_count}
flashcard candidates.

SECTION:
{chunk["text"]}
"""
    response = _client().chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}, temperature=0.15)
    value = _safe_json(response.choices[0].message.content or "{}")
    return {**value, "section_index": index, "start_second": chunk.get("start_second"), "end_second": chunk.get("end_second")}


def _final_synthesis(section_analyses: list[dict], *, language_name: str, summary_style: str, quiz_count: int, flashcard_count: int) -> dict:
    style = SUMMARY_STYLES.get(summary_style, SUMMARY_STYLES["standard"])
    source = json.dumps(section_analyses, ensure_ascii=False, separators=(",", ":"))
    prompt = f"""
You are LectureSift. Synthesize the section analyses below into one coherent,
complete academic study pack. Every statement must be traceable to the supplied
section analyses. Output language: {language_name}.
Summary profile: {style}.
Summary target: {_target_words(summary_style)}.

Return JSON only with exactly:
{{
  "title":"string",
  "summary":"string",
  "key_points":["string"],
  "important_terms":[{{"term":"string","definition":"string"}}],
  "notes":[{{"heading":"string","content":"string","bullets":["string"]}}],
  "exam_focus":["string"],
  "quiz":[{{"question":"string","options":["A","B","C","D"],"answer_index":0,"explanation":"string"}}],
  "flashcards":[{{"front":"complete direct question?","back":"direct answer"}}]
}}
Requirements:
- Cover every major topic across all sections and preserve lecture order.
- Remove only true duplication; do not drop later sections.
- Create exactly {quiz_count} non-redundant questions when supported, distributed across the whole lecture.
- Create up to {flashcard_count} non-redundant question-and-answer cards.
- Mark unclear source claims as unclear rather than repairing them.

SECTION ANALYSES:
{source}
"""
    response = _client().chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}, temperature=0.15)
    return _safe_json(response.choices[0].message.content or "{}")


def make_study_pack(transcript: str, output_language: str, summary_style: str, quiz_count: int, flashcard_count: int, transcript_segments: list[dict] | None = None) -> dict:
    if not transcript.strip():
        return empty_study_pack()
    language_name = LANGUAGE_NAMES.get(output_language, "English")
    chunks = _source_chunks(transcript, transcript_segments)
    section_quiz = max(2, math.ceil(quiz_count / max(1, len(chunks))) + 1)
    section_cards = max(4, math.ceil(flashcard_count / max(1, len(chunks))) + 2)
    analyses = [_section_analysis(chunk, index=index, total=len(chunks), language_name=language_name, quiz_count=section_quiz, flashcard_count=section_cards) for index, chunk in enumerate(chunks, 1)]
    try:
        value = _final_synthesis(analyses, language_name=language_name, summary_style=summary_style, quiz_count=quiz_count, flashcard_count=flashcard_count)
    except Exception as exc:
        raise LectureSiftError("LS-AI-05", "Uzun dersin bölüm sonuçları tek çalışma paketinde birleştirilemedi.", f"Hierarchical synthesis failed: {exc}", 502) from exc
    base = empty_study_pack()
    base.update({key: value.get(key, default) for key, default in base.items() if key not in {"sections", "source_coverage"}})
    base["quiz"] = _normalize_quiz(base.get("quiz"), quiz_count)
    base["flashcards"] = _normalize_flashcards(base.get("flashcards"), output_language, flashcard_count)
    base["sections"] = [{"index": item.get("section_index"), "heading": item.get("heading") or f"Section {item.get('section_index')}", "summary": item.get("summary") or "", "start_second": item.get("start_second"), "end_second": item.get("end_second")} for item in analyses]
    base["source_coverage"] = {"complete": True, "chunks": len(chunks), "characters": len(transcript), "truncated": False}
    return base
