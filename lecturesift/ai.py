import json
import re
from pathlib import Path

from openai import OpenAI

from .config import LANGUAGE_NAMES, OPENAI_API_KEY, SUMMARY_STYLES
from .errors import LectureSiftError


_CLIENT = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


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


def _chunk_text(text: str, maximum: int = 32000) -> list[str]:
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


def translate_transcript(transcript: str, output_language: str) -> str:
    if not transcript.strip() or output_language not in LANGUAGE_NAMES:
        return ""
    language_name = LANGUAGE_NAMES[output_language]
    translated: list[str] = []
    for chunk in _chunk_text(transcript):
        response = _client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate lecture transcripts faithfully. Preserve names, technical terms, "
                        "speaker intent, paragraph breaks, and uncertainty. If the text is already in "
                        "the requested language, return it unchanged. Return only the translated text."
                    ),
                },
                {"role": "user", "content": f"Target language: {language_name}\n\n{chunk}"},
            ],
            temperature=0.1,
        )
        translated.append((response.choices[0].message.content or "").strip())
    return "\n\n".join(translated).strip()


def _safe_json(value: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", value.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


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


def make_study_pack(
    transcript: str,
    output_language: str,
    summary_style: str,
    quiz_count: int,
    flashcard_count: int,
) -> dict:
    if not transcript.strip():
        return empty_study_pack()

    language_name = LANGUAGE_NAMES.get(output_language, "English")
    style = SUMMARY_STYLES.get(summary_style, SUMMARY_STYLES["standard"])
    prompt = f"""
You are LectureSift, an academic study-pack generator.
Use ONLY the lecture transcript below. Never invent facts that are absent.
Output language: {language_name}.
Summary profile: {style}.

Return VALID JSON ONLY with exactly this top-level schema:
{{
  "title": "string",
  "summary": "string",
  "key_points": ["string"],
  "important_terms": [{{"term":"string","definition":"string"}}],
  "notes": [{{"heading":"string","content":"string","bullets":["string"]}}],
  "exam_focus": ["string"],
  "quiz": [{{"question":"string","options":["A","B","C","D"],"answer_index":0,"explanation":"string"}}],
  "flashcards": [{{"front":"string","back":"string"}}]
}}

Requirements:
- Create exactly {quiz_count} non-redundant quiz questions when the source supports them.
- Create up to {flashcard_count} useful, non-redundant flashcards.
- Separate definitions, important distinctions, examples, lecturer emphasis, likely exam points, and difficult concepts.
- Preserve important terminology from the transcript.
- Mark unclear transcript statements as unclear instead of silently correcting them.
- Do not include markdown fences or commentary outside the JSON.

TRANSCRIPT:
{transcript[:100000]}
"""
    response = _client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    value = _safe_json(response.choices[0].message.content or "{}")
    base = empty_study_pack()
    base.update({key: value.get(key, default) for key, default in base.items()})
    return base
