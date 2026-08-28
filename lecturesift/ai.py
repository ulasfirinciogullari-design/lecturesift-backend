import json
import re
from pathlib import Path

from openai import OpenAI

from .config import LANGUAGE_NAMES, OPENAI_API_KEY, SUMMARY_STYLES
from .errors import LectureSiftError


_CLIENT = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
LONG_TRANSCRIPT_THRESHOLD = 85_000
STUDY_SECTION_CHARACTERS = 28_000
MAX_SYNTHESIS_CHARACTERS = 85_000


def _client() -> OpenAI:
    if not _CLIENT:
        raise LectureSiftError(
            "LS-AI-03",
            "Yapay zekâ servisi henüz yapılandırılmamış.",
            "OPENAI_API_KEY is not configured.",
            503,
        )
    return _CLIENT


def transcribe(audio_path: Path, language: str) -> str:
    with open(audio_path, "rb") as stream:
        arguments = {"model": "gpt-4o-mini-transcribe", "file": stream}
        if language and language != "auto":
            arguments["language"] = language
        response = _client().audio.transcriptions.create(**arguments)
    return getattr(response, "text", str(response)).strip()


def transcribe_timed(audio_path: Path, language: str) -> dict:
    """Transcribe with provider-reported speaker and segment timestamps.

    This uses the diarization model only when the separately costed precise
    timestamp feature is enabled by the caller.
    """
    with open(audio_path, "rb") as stream:
        arguments = {
            "model": "gpt-4o-transcribe-diarize",
            "file": stream,
            "response_format": "diarized_json",
            "chunking_strategy": "auto",
        }
        if language and language != "auto":
            arguments["language"] = language
        response = _client().audio.transcriptions.create(**arguments)
    segments = []
    for item in getattr(response, "segments", None) or []:
        text = str(getattr(item, "text", "") or "").strip()
        if not text:
            continue
        segments.append(
            {
                "start": max(0.0, float(getattr(item, "start", 0) or 0)),
                "end": max(0.0, float(getattr(item, "end", 0) or 0)),
                "speaker": str(getattr(item, "speaker", "") or "") or None,
                "text": text,
                "precision": "provider_segment",
            }
        )
    return {
        "text": str(getattr(response, "text", "") or "").strip(),
        "segments": segments,
        "duration": max(0.0, float(getattr(response, "duration", 0) or 0)),
        "mode": "provider_segments",
    }


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
    insistence = (
        "This is a retry because the previous answer appeared incomplete. Translate every sentence; do not summarize."
        if attempt
        else "Translate every sentence and paragraph without summarizing, shortening, or omitting repetitions."
    )
    response = _client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You translate lecture transcripts faithfully and completely. Preserve names, technical terms, "
                    "speaker intent, paragraph order, uncertainty, examples, and repetitions that carry meaning. "
                    "If the source is already in the target language, mark same_language true and return it unchanged. "
                    "Return JSON only with keys same_language (boolean), complete (boolean), and translation (string). "
                    + insistence
                ),
            },
            {"role": "user", "content": f"Target language: {language_name}\n\nSOURCE:\n{chunk}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    value = _safe_json(response.choices[0].message.content or "{}")
    translated = str(value.get("translation") or "").strip()
    same_language = bool(value.get("same_language"))
    complete = bool(value.get("complete"))
    # Different writing systems can have very different lengths, so this is
    # only a severe-truncation guard rather than a strict translation metric.
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
            raise LectureSiftError(
                "LS-AI-04",
                "Transkript çevirisi eksiksiz tamamlanamadı. İşlem yarım çeviri üretmeden durduruldu.",
                "Translation chunk failed completeness validation.",
                502,
            )
        translated_chunks.append(translated)
    return "\n\n".join(translated_chunks).strip()


def empty_study_pack() -> dict:
    return {
        "title": "LectureSift",
        "summary": "",
        "key_points": [],
        "important_terms": [],
        "notes": [],
        "exam_focus": [],
        "quiz": [],
        "flashcards": [],
    }


def _normalize_flashcards(items: object, language: str, maximum: int) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(items, list):
        return normalized
    for item in items:
        if not isinstance(item, dict):
            continue
        front = " ".join(str(item.get("front") or item.get("question") or "").split())
        back = " ".join(str(item.get("back") or item.get("answer") or "").split())
        if not front or not back:
            continue
        if not front.rstrip().endswith(("?", "？", "؟")):
            front = f"{front} nedir?" if language == "tr" else f"What is {front}?"
        normalized.append({"front": front, "back": back})
        if len(normalized) >= maximum:
            break
    return normalized


def _request_study_pack(
    source: str,
    output_language: str,
    summary_style: str,
    quiz_count: int,
    flashcard_count: int,
    *,
    source_label: str = "TRANSCRIPT",
    source_context: str = "Use ONLY the lecture transcript below. Never invent facts that are absent.",
    summary_target: str | None = None,
    extra_requirements: str = "",
    max_tokens: int = 6000,
) -> dict:
    language_name = LANGUAGE_NAMES.get(output_language, "English")
    style = SUMMARY_STYLES.get(summary_style, SUMMARY_STYLES["standard"])
    target_words = summary_target or {
        "short": "about 250-450 words when the source is substantial",
        "standard": "about 700-1400 words when the source is substantial",
        "detailed": "about 1400-2600 words when the source is substantial",
        "exam": "about 700-1600 words when the source is substantial",
        "five_minute": "about 350-650 words when the source is substantial",
    }.get(summary_style, "about 700-1400 words when the source is substantial")
    prompt = f"""
You are LectureSift, an academic study-pack generator.
{source_context}
Output language: {language_name}.
Summary profile: {style}.
Summary target: {target_words}. Scale down only when the source is genuinely short; never pad with invented material.

Return VALID JSON ONLY with exactly this top-level schema:
{{
  "title": "string",
  "summary": "string",
  "key_points": ["string"],
  "important_terms": [{{"term":"string","definition":"string"}}],
  "notes": [{{"heading":"string","content":"string","bullets":["string"]}}],
  "exam_focus": ["string"],
  "quiz": [{{"question":"string","options":["A","B","C","D"],"answer_index":0,"explanation":"string"}}],
  "flashcards": [{{"front":"a complete direct question ending with a question mark","back":"a direct self-contained answer"}}]
}}

Requirements:
- The summary must cover every major topic, definition, distinction, mechanism, example, lecturer emphasis, and conclusion supported by the source. Do not collapse a substantial lecture into a few sentences.
- Create exactly {quiz_count} non-redundant quiz questions when the source supports them.
- Create up to {flashcard_count} useful, non-redundant question-and-answer flashcards. Every front must be a real question; never output a bare term as the front.
- Separate definitions, important distinctions, examples, lecturer emphasis, likely exam points, and difficult concepts.
- Preserve important terminology and the order of the source.
- Mark unclear source statements as unclear instead of silently correcting them.
- Do not include markdown fences or commentary outside the JSON.
{extra_requirements}

{source_label}:
{source}
"""
    response = _client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=max_tokens,
    )
    value = _safe_json(response.choices[0].message.content or "{}")
    base = empty_study_pack()
    base.update({key: value.get(key, default) for key, default in base.items()})
    base["flashcards"] = _normalize_flashcards(base.get("flashcards"), output_language, flashcard_count)
    return base


def _digest_source(digests: list[dict]) -> str:
    return json.dumps(digests, ensure_ascii=False, separators=(",", ":"))


def _digest_groups(digests: list[dict], maximum: int = 52_000) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_size = 2
    for digest in digests:
        size = len(json.dumps(digest, ensure_ascii=False, separators=(",", ":"))) + 1
        if current and current_size + size > maximum:
            groups.append(current)
            current = []
            current_size = 2
        current.append(digest)
        current_size += size
    if current:
        groups.append(current)
    return groups


def _compact_digests(
    digests: list[dict],
    output_language: str,
    summary_style: str,
    quiz_count: int,
    flashcard_count: int,
) -> list[dict]:
    compacted = digests
    for _ in range(3):
        if len(_digest_source(compacted)) <= MAX_SYNTHESIS_CHARACTERS:
            return compacted
        groups = _digest_groups(compacted)
        next_level: list[dict] = []
        for index, group in enumerate(groups, 1):
            next_level.append(
                _request_study_pack(
                    _digest_source(group),
                    output_language,
                    summary_style,
                    max(2, min(quiz_count, 8)),
                    max(4, min(flashcard_count, 12)),
                    source_label=f"ORDERED DIGEST GROUP {index} OF {len(groups)}",
                    source_context=(
                        "The source contains faithful digests of consecutive lecture sections. "
                        "Merge them without omitting unique facts and without adding outside knowledge."
                    ),
                    summary_target="about 700-1200 words",
                    extra_requirements="Keep the entire JSON concise enough for a later final synthesis.",
                    max_tokens=4000,
                )
            )
        if len(next_level) >= len(compacted) and len(next_level) == 1:
            compacted = next_level
            break
        compacted = next_level
    if len(_digest_source(compacted)) > MAX_SYNTHESIS_CHARACTERS:
        raise LectureSiftError(
            "LS-AI-05",
            "Uzun dersin tüm bölümleri güvenli sınırlar içinde birleştirilemedi. Hiçbir bölümü kesmeden işlem durduruldu.",
            "Hierarchical study-pack digests exceeded the safe synthesis input size.",
            502,
        )
    return compacted


def make_study_pack(
    transcript: str,
    output_language: str,
    summary_style: str,
    quiz_count: int,
    flashcard_count: int,
) -> dict:
    if not transcript.strip():
        return empty_study_pack()
    if len(transcript) <= LONG_TRANSCRIPT_THRESHOLD:
        return _request_study_pack(
            transcript,
            output_language,
            summary_style,
            quiz_count,
            flashcard_count,
        )

    sections = _chunk_text(transcript, STUDY_SECTION_CHARACTERS)
    section_quiz = max(2, (quiz_count + len(sections) - 1) // len(sections) + 1)
    section_cards = max(4, (flashcard_count + len(sections) - 1) // len(sections) + 2)
    digests: list[dict] = []
    for index, section in enumerate(sections, 1):
        digests.append(
            _request_study_pack(
                section,
                output_language,
                summary_style,
                section_quiz,
                section_cards,
                source_label=f"TRANSCRIPT SECTION {index} OF {len(sections)}",
                source_context=(
                    "Use ONLY this consecutive lecture section. Preserve every distinct topic needed for a later "
                    "whole-lecture synthesis; never assume that another section will recover omitted facts."
                ),
                summary_target="about 350-700 words",
                extra_requirements="Keep the entire section JSON under roughly 1800 words.",
                max_tokens=3500,
            )
        )

    compacted = _compact_digests(
        digests,
        output_language,
        summary_style,
        quiz_count,
        flashcard_count,
    )
    return _request_study_pack(
        _digest_source(compacted),
        output_language,
        summary_style,
        quiz_count,
        flashcard_count,
        source_label="ORDERED SECTION DIGESTS",
        source_context=(
            "The source contains faithful digests of every consecutive section of one lecture. Synthesize one "
            "coherent study pack in the original order. Preserve unique facts from every section, remove only true "
            "duplicates, and never add outside knowledge."
        ),
        extra_requirements=(
            "Distribute quiz questions and flashcards across the whole lecture rather than concentrating on the "
            "opening sections."
        ),
    )
