import json
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


def _chunk_text(text: str, maximum: int = 24000) -> list[str]:
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


def _translate_chunk(chunk: str, language_name: str) -> str:
    best = ""
    for attempt in range(2):
        response = _client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate the ENTIRE lecture transcript chunk faithfully. Do not summarize, omit, shorten, or merge content. "
                        "Preserve names, technical terms, paragraph breaks, examples, repetitions that carry meaning, and uncertainty. "
                        "If the text is already in the requested language, copy the entire text unchanged. Return only the complete text."
                    ),
                },
                {"role": "user", "content": f"Target language: {language_name}\nAttempt: {attempt + 1}\n\n{chunk}"},
            ],
            temperature=0.0,
        )
        candidate = (response.choices[0].message.content or "").strip()
        if len(candidate) > len(best):
            best = candidate
        if candidate and len(candidate) >= max(80, int(len(chunk) * 0.45)):
            return candidate
    return best


def translate_transcript(transcript: str, output_language: str) -> str:
    if not transcript.strip() or output_language not in LANGUAGE_NAMES:
        return ""
    language_name = LANGUAGE_NAMES[output_language]
    source_chunks = _chunk_text(transcript)
    translated = [_translate_chunk(chunk, language_name) for chunk in source_chunks]
    if any(not item.strip() for item in translated):
        raise LectureSiftError("LS-AI-04", "Transkript çevirisi eksik döndü; işlem güvenli şekilde durduruldu.", "One or more translation chunks were empty.", 502)
    result = "\n\n".join(translated).strip()
    if len(result) < max(100, int(len(transcript) * 0.38)):
        raise LectureSiftError("LS-AI-04", "Transkript çevirisi eksik döndü; işlem güvenli şekilde durduruldu.", "Translation length completeness check failed.", 502)
    return result


def _safe_json(value: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", value.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def empty_study_pack() -> dict:
    return {"title": "LectureSift", "summary": "", "key_points": [], "important_terms": [], "notes": [], "exam_focus": [], "quiz": [], "flashcards": []}


def _normalize_flashcards(cards: object, limit: int) -> list[dict]:
    normalized: list[dict] = []
    for item in cards if isinstance(cards, list) else []:
        if not isinstance(item, dict):
            continue
        front = str(item.get("front") or item.get("question") or "").strip()
        back = str(item.get("back") or item.get("answer") or "").strip()
        if not front or not back:
            continue
        if not front.endswith("?"):
            front = f"{front.rstrip('.:;')}?"
        normalized.append({"front": front, "back": back})
        if len(normalized) >= limit:
            break
    return normalized


def make_study_pack(transcript: str, output_language: str, summary_style: str, quiz_count: int, flashcard_count: int) -> dict:
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
  "flashcards": [{{"front":"question ending with ?","back":"direct answer"}}]
}}

Requirements:
- The summary must cover ALL major themes in the transcript. For standard/detailed/exam profiles, do not return a tiny abstract; write a genuinely comprehensive multi-paragraph summary.
- Create exactly {quiz_count} non-redundant quiz questions when the source supports them.
- Create up to {flashcard_count} useful, non-redundant flashcards.
- EVERY flashcard front must be an explicit study question ending with a question mark; the back must directly answer it.
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
    base["flashcards"] = _normalize_flashcards(base.get("flashcards"), flashcard_count)
    return base
