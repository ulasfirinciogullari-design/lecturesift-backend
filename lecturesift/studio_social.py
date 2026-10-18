"""Daily studio Reel and carousel for the official LectureSift Instagram account.

Every day gets one lesson: a real, introductory-level exam question from a rotating subject. The
same lesson becomes an ElevenLabs-narrated Reel (morning slot) and a 5-slide carousel (evening
slot), rendered by ``reel_studio``. Rendered files live in social storage and the public API
serves them to Instagram, which fetches media by URL.

  python -m lecturesift.studio_social prepare YYYY-MM-DD   generate and render without publishing
  python -m lecturesift.studio_social run                  publish whatever is due right now
"""

from __future__ import annotations

import io
import json
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError

from . import reel_studio
from .config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    INSTAGRAM_CAROUSEL_HOUR,
    INSTAGRAM_DAILY_AUTOMATION_ENABLED,
    INSTAGRAM_REEL_HOUR,
    OPENAI_API_KEY,
    PUBLIC_BASE_URL,
)
from .daily_social import _assert_target_account, _client, _verify_public_video, _wait_until_ready
from .instagram import InstagramConfigurationError
from .social_storage import read_bytes, read_json, recent_json, write_bytes, write_json

_PREFIX = "social/instagram/studio/"
_TZ = ZoneInfo("Europe/Istanbul")
CAROUSEL_SLIDES = 5

_SUBJECTS = (
    "Biyoloji", "Kimya", "Fizik", "Tarih", "Ekonomi", "Psikoloji", "Anatomi", "İstatistik",
    "Bilgisayar Bilimi", "Coğrafya", "Felsefe", "Matematik", "Sosyoloji", "Hukuka Giriş",
)

# Hand-checked lessons used when the model is unavailable or its draft fails validation.
_CURATED = (
    {"subject": "Biyoloji", "source": "Biyoloji 252 · Mitoz", "file": "biyoloji-ders-kaydi.mp4",
     "question": "Mitozda kromozomlar hücrenin ortasında hangi evrede dizilir?",
     "options": ["Profaz", "Metafaz", "Anafaz", "Telofaz"], "answer": 1},
    {"subject": "Kimya", "source": "Kimya 101 · Asitler ve bazlar", "file": "kimya-sunum.pdf",
     "question": "pH değeri 7'nin altında olan bir çözelti nasıldır?",
     "options": ["Asidik", "Bazik", "Nötr", "Tuzlu"], "answer": 0},
    {"subject": "Tarih", "source": "Tarih 102 · Osmanlı yükselişi", "file": "tarih-ders-notu.pdf",
     "question": "İstanbul hangi yıl fethedildi?",
     "options": ["1071", "1299", "1453", "1517"], "answer": 2},
    {"subject": "Ekonomi", "source": "İktisada Giriş · Arz ve talep", "file": "iktisat-ders-kaydi.mp4",
     "question": "Arz sabitken talep artarsa fiyat genellikle ne olur?",
     "options": ["Artar", "Azalır", "Değişmez", "Sıfırlanır"], "answer": 0},
    {"subject": "Psikoloji", "source": "Psikolojiye Giriş · Koşullanma", "file": "psikoloji-slaytlar.pptx",
     "question": "Pavlov deneyinde zil sesi öğrenmeden sonra neye dönüşür?",
     "options": ["Koşullu uyarıcı", "Koşulsuz uyarıcı", "Koşulsuz tepki", "Pekiştireç"], "answer": 0},
    {"subject": "Bilgisayar Bilimi", "source": "Algoritmalar · Arama", "file": "algoritmalar-ders.mp4",
     "question": "İkili arama (binary search) hangi koşulda doğru çalışır?",
     "options": ["Veri sıralı olmalı", "Veri tekrarsız olmalı", "Dizi kısa olmalı", "Sayılar pozitif olmalı"], "answer": 0},
    {"subject": "Anatomi", "source": "Anatomi · Kalp", "file": "anatomi-ders-kaydi.mp4",
     "question": "Oksijenli kanı vücuda hangi kalp odacığı pompalar?",
     "options": ["Sol karıncık", "Sağ karıncık", "Sol kulakçık", "Sağ kulakçık"], "answer": 0},
    {"subject": "Fizik", "source": "Fizik 101 · Newton yasaları", "file": "fizik-ders-notu.pdf",
     "question": "Newton'un ikinci yasasına göre net kuvvet neye eşittir?",
     "options": ["Kütle × ivme", "Kütle × hız", "Ağırlık ÷ hacim", "Enerji × zaman"], "answer": 0},
)

_HOOKS = (
    ("{subject} notlarını", "3. kez mi okuyorsun?", "3. kez", "notlarını üçüncü kez mi okuyorsun? Dur."),
    ("Sınava 1 gün kala", "{subject} mi çalışıyorsun?", "1 gün", "Sınava bir gün kala mı çalışıyorsun? Bunu dene."),
    ("Bu soruyu", "5 saniyede bil.", "5 saniyede", "Bu soruyu beş saniyede bilebilir misin?"),
)

_BANNED = ("garanti", "mucize", "viral", "%100", "saniyede hazır", "kesin başarı", "ezberle geç")

_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "file": {"type": "string"},
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "answer": {"type": "integer"},
        "hook_line1": {"type": "string"},
        "hook_line2": {"type": "string"},
        "hook_highlight": {"type": "string"},
        "voice_hook": {"type": "string"},
        "caption_body": {"type": "string"},
        "topic_hashtag": {"type": "string"},
    },
    "required": ["source", "file", "question", "options", "answer", "hook_line1", "hook_line2",
                 "hook_highlight", "voice_hook", "caption_body", "topic_hashtag"],
    "additionalProperties": False,
}

_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"correct_index": {"type": "integer"}, "unambiguous": {"type": "boolean"}},
    "required": ["correct_index", "unambiguous"],
    "additionalProperties": False,
}


def _key(day: date, name: str) -> str:
    return f"{_PREFIX}{day.isoformat()}/{name}"


def _row(day: date) -> dict | None:
    return read_json(_key(day, "post.json"))


def marker(day: date, kind: str) -> str:
    return f"LectureSift · studio-{kind} · {day.isoformat()}"


def subject_for(day: date) -> str:
    return _SUBJECTS[day.toordinal() % len(_SUBJECTS)]


# ---------------------------------------------------------------- lesson content

def _check(text: str, field: str, low: int, high: int) -> str:
    value = " ".join(str(text or "").split())
    if not low <= len(value) <= high:
        raise ValueError(f"{field} must be {low}-{high} characters")
    if "#" in value or "@" in value or "http" in value.casefold():
        raise ValueError(f"{field} must not contain hashtags, mentions or links")
    if any(term in value.casefold() for term in _BANNED):
        raise ValueError(f"{field} makes an unsupported claim")
    return value


def build_row(lesson: dict, day: date, hook: tuple | None = None) -> dict:
    """Complete studio row (screens, narration, caption) from a validated lesson."""
    subject = lesson["subject"]
    line1, line2, highlight, voice_hook = hook or _HOOKS[day.toordinal() % len(_HOOKS)]
    line1, line2 = line1.format(subject=subject), line2.format(subject=subject)
    if voice_hook.startswith("notlarını"):
        voice_hook = f"{subject} {voice_hook}"
    answer = lesson["options"][lesson["answer"]]
    hashtag = lesson.get("topic_hashtag") or "#" + re.sub(r"\W", "", subject)
    body = lesson.get("caption_body") or (
        "Aynı notu tekrar okumak yerine kendine tek bir soru sor. Bilemediğin yer, tekrar etmen gereken yerdir."
    )
    caption = (
        f"{line1} {line2}\n\n{body}\n\n"
        "LectureSift ders kaydını, PDF'ini ya da slaytlarını özet, quiz ve flashcard'lara çevirir. "
        "Kaç saniyede bildin? Yoruma yaz 👇\n\n"
        f"{hashtag} #DersÇalışma #AktifHatırlama #Üniversite #LectureSift\n"
        f"Voice: AI-generated.\n{{marker}}"
    )
    return {
        "subject": subject,
        "source": lesson["source"],
        "file": lesson["file"],
        "hook": [line1, line2],
        "hook_highlight": highlight if highlight in line2 else "",
        "problem": "Tekrar okumak ≠ hatırlamak",
        "question": lesson["question"],
        "options": list(lesson["options"]),
        "answer": int(lesson["answer"]),
        "cta": "Dersini yükle, çalışma paketin hazır.",
        "voice": [
            {"scene": "hook", "text": voice_hook},
            {"scene": "problem", "text": "Tekrar okumak, hatırladığını sanmanı sağlar. Ama sınavda sana soru sorulur."},
            {"scene": "product", "text": "Ders kaydını ya da PDF'ini LectureSift'e yükle. Notlar, özet, test ve kartlar tek pakette hazır."},
            {"scene": "quiz", "text": f"Hadi dene. {lesson['question']}"},
            {"scene": "answer", "text": f"Cevap: {answer}."},
            {"scene": "cta", "text": "Bütün dersi değil, unuttuğun yeri tekrar et. LectureSift'i ücretsiz dene."},
        ],
        "caption_template": caption,
        "format": "studio-v1",
    }


def validate_lesson(data: dict, subject: str, recent_questions: list[str]) -> dict:
    options = [_check(o, "option", 1, 24) for o in data.get("options") or []]
    if len(options) != 4 or len({o.casefold() for o in options}) != 4:
        raise ValueError("options must be 4 distinct answers")
    answer = data.get("answer")
    if not isinstance(answer, int) or not 0 <= answer < 4:
        raise ValueError("answer must be an index from 0 to 3")
    question = _check(data.get("question"), "question", 20, 95)
    if not question.endswith("?"):
        raise ValueError("question must end with a question mark")
    if any(question.casefold() == old.casefold() for old in recent_questions):
        raise ValueError("question repeats a recent post")
    line2 = _check(data.get("hook_line2"), "hook_line2", 6, 26)
    highlight = " ".join(str(data.get("hook_highlight") or "").split())
    if highlight and highlight not in line2:
        raise ValueError("hook_highlight must appear in hook_line2")
    voice_hook = _check(data.get("voice_hook"), "voice_hook", 15, 110)
    if re.search(r"\d", voice_hook) or re.search(r"\d", question):
        # The narrator reads these lines; written numbers are read inconsistently in Turkish.
        if re.search(r"\d", voice_hook):
            raise ValueError("voice_hook must spell numbers as words")
    tag = str(data.get("topic_hashtag") or "")
    if not re.fullmatch(r"#[^\s#@]{2,30}", tag):
        raise ValueError("topic_hashtag must be one hashtag")
    return {
        "subject": subject,
        "source": _check(data.get("source"), "source", 6, 34),
        "file": _check(data.get("file"), "file", 6, 30),
        "question": question,
        "options": options,
        "answer": answer,
        "hook": (_check(data.get("hook_line1"), "hook_line1", 6, 22), line2, highlight, voice_hook),
        "caption_body": _check(data.get("caption_body"), "caption_body", 60, 260),
        "topic_hashtag": tag,
    }


def _verify_answer(client: OpenAI, lesson: dict) -> bool:
    """A second, independent call must pick the same option before a question is published."""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini", temperature=0, max_tokens=60,
            response_format={"type": "json_schema", "json_schema": {"name": "verify", "strict": True, "schema": _VERIFY_SCHEMA}},
            messages=[
                {"role": "system", "content": "You are a strict university teaching assistant. Answer the multiple-choice question."},
                {"role": "user", "content": json.dumps({"question": lesson["question"], "options": lesson["options"]}, ensure_ascii=False)},
            ],
        )
        result = json.loads(response.choices[0].message.content or "{}")
    except (OpenAIError, ValueError, IndexError):
        return False
    return bool(result.get("unambiguous")) and result.get("correct_index") == lesson["answer"]


def generate_lesson(day: date, recent_questions: list[str]) -> tuple[dict, tuple | None]:
    subject = subject_for(day)
    curated = [c for c in _CURATED if c["question"] not in recent_questions] or list(_CURATED)
    fallback = curated[day.toordinal() % len(curated)]
    if not OPENAI_API_KEY:
        return fallback, None
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=45, max_retries=1)
    messages = [
        {"role": "system", "content": (
            "You write one Turkish Instagram lesson for LectureSift, a tool that turns lecture recordings, PDFs "
            "and slides into notes, summaries, quizzes and flashcards. Pick ONE well-established, introductory "
            "university-level fact in the given subject that has a single unambiguous answer found in standard "
            "textbooks; no recent events, statistics, opinions or contested dates. Write a multiple-choice "
            "question with 4 short options (max 24 characters each) and the index of the correct one. "
            "source: a realistic course label like 'Kimya 101 · Asitler' (max 34 chars). file: a plausible "
            "lowercase file name like 'kimya-sunum.pdf' (max 30 chars). hook_line1 (max 22 chars) and "
            "hook_line2 (max 26 chars) form a scroll-stopping Turkish hook about how students study this "
            "subject; hook_highlight is 1-2 words copied from hook_line2. voice_hook is the same hook as "
            "natural spoken Turkish with numbers written as words. caption_body: 1-2 helpful sentences about "
            "active recall for this topic, no emoji, no hashtags. topic_hashtag: one Turkish hashtag for the "
            "subject. Never promise grades, speed or guaranteed results."
        )},
        {"role": "user", "content": json.dumps({"subject": subject, "avoid_questions": recent_questions[:40]}, ensure_ascii=False)},
    ]
    for _ in range(3):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini", temperature=0.7, max_tokens=700,
                response_format={"type": "json_schema", "json_schema": {"name": "lesson", "strict": True, "schema": _SCHEMA}},
                messages=messages,
            )
        except OpenAIError:
            return fallback, None
        choice = response.choices[0]
        content = choice.message.content or "{}"
        try:
            lesson = validate_lesson(json.loads(content), subject, recent_questions)
        except (ValueError, TypeError) as exc:
            messages += [{"role": "assistant", "content": content}, {"role": "user", "content": f"Fix this: {exc}. Return JSON only."}]
            continue
        if _verify_answer(client, lesson):
            hook = lesson.pop("hook")
            return lesson, hook
        messages += [{"role": "assistant", "content": content},
                     {"role": "user", "content": "That question was ambiguous or the marked answer was wrong. Choose a different, clearer fact."}]
    return fallback, None


# ---------------------------------------------------------------- render and store

def _recent_questions() -> list[str]:
    return [row.get("question", "") for row in recent_json(_PREFIX, limit=60)]


def prepare(day: date) -> dict:
    """Generate, narrate and render the day's Reel, cover and carousel once; later calls reuse them."""
    existing = _row(day)
    if existing and existing.get("rendered"):
        return existing
    if not (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID):
        raise InstagramConfigurationError("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID are required for studio Reels")
    if existing:
        row = existing
    else:
        lesson, hook = generate_lesson(day, _recent_questions())
        row = build_row(lesson, day, hook)
        row.update({"date": day.isoformat(), "rendered": False, "reel_media_id": None, "carousel_media_id": None})
        write_json(_key(day, "post.json"), row)
    narration = reel_studio.narrate(row, voice=True)
    if narration.duration > 58:
        raise RuntimeError("Studio Reel narration is longer than the Reel format allows")
    with tempfile.TemporaryDirectory(prefix="lecturesift-studio-") as folder:
        video = reel_studio.render_reel(row, narration, Path(folder) / "reel.mp4")
        write_bytes(_key(day, "reel.mp4"), video.read_bytes(), "video/mp4")
    write_bytes(_key(day, "cover.jpg"), _jpeg(reel_studio.render_cover(row, narration)), "image/jpeg")
    for index, slide in enumerate(reel_studio.render_carousel(row), start=1):
        write_bytes(_key(day, f"carousel-{index}.jpg"), _jpeg(slide), "image/jpeg")
    row = {**row, "rendered": True, "seconds": round(narration.duration, 2)}
    write_json(_key(day, "post.json"), row)
    return row


def _jpeg(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92, optimize=True)
    return buffer.getvalue()


def asset(day: date, name: str) -> bytes | None:
    allowed = {"reel.mp4", "cover.jpg", *(f"carousel-{i}.jpg" for i in range(1, CAROUSEL_SLIDES + 1))}
    if name not in allowed:
        return None
    return read_bytes(_key(day, name))


def asset_url(day: date, name: str) -> str:
    base = (PUBLIC_BASE_URL or "https://api.lecturesift.com").rstrip("/")
    return f"{base}/instagram/studio/{day.isoformat()}/{name}"


# ---------------------------------------------------------------- publish

def _caption(row: dict, day: date, kind: str) -> str:
    return row["caption_template"].replace("{marker}", marker(day, kind))


def _already_published(client, row: dict, day: date, kind: str) -> bool:
    field = f"{kind}_media_id"
    if row.get(field):
        return True
    recent = client.get_recent_media(limit=50).get("data", [])
    found = next((item for item in recent if marker(day, kind) in (item.get("caption") or "")), None)
    if found and found.get("id"):
        write_json(_key(day, "post.json"), {**row, field: found["id"]})
        return True
    return False


def publish_reel(day: date) -> dict:
    client = _client()
    _assert_target_account(client)
    row = prepare(day)
    if _already_published(client, row, day, "reel"):
        return {"status": "already_published", "kind": "studio_reel", "date": day.isoformat()}
    video_url = asset_url(day, "reel.mp4")
    _verify_public_video(video_url)
    container = client.create_media_container(
        media_url=video_url, caption=_caption(row, day, "reel"), media_type="REELS",
        cover_url=asset_url(day, "cover.jpg"),
    )
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    write_json(_key(day, "post.json"), {**row, "reel_media_id": published.get("id")})
    return {"status": "published", "kind": "studio_reel", "date": day.isoformat(), "media_id": published.get("id")}


def publish_carousel(day: date) -> dict:
    client = _client()
    _assert_target_account(client)
    row = prepare(day)
    if _already_published(client, row, day, "carousel"):
        return {"status": "already_published", "kind": "studio_carousel", "date": day.isoformat()}
    children = []
    for index in range(1, CAROUSEL_SLIDES + 1):
        item = client.create_carousel_item(image_url=asset_url(day, f"carousel-{index}.jpg"))
        _wait_until_ready(client, item["id"])
        children.append(item["id"])
    container = client.create_carousel(children=children, caption=_caption(row, day, "carousel"))
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    write_json(_key(day, "post.json"), {**row, "carousel_media_id": published.get("id")})
    return {"status": "published", "kind": "studio_carousel", "date": day.isoformat(), "media_id": published.get("id")}


def run_scheduled(now: datetime | None = None) -> list[dict]:
    """Publish what is due in Istanbul time: the Reel from INSTAGRAM_REEL_HOUR, the carousel from INSTAGRAM_CAROUSEL_HOUR."""
    if not INSTAGRAM_DAILY_AUTOMATION_ENABLED:
        return [{"status": "disabled"}]
    local = (now or datetime.now(_TZ)).astimezone(_TZ)
    day = local.date()
    row = _row(day) or {}
    results = []
    if local.hour >= INSTAGRAM_REEL_HOUR and not row.get("reel_media_id"):
        results.append(publish_reel(day))
    if local.hour >= INSTAGRAM_CAROUSEL_HOUR and not (_row(day) or {}).get("carousel_media_id"):
        results.append(publish_carousel(day))
    return results or [{"status": "nothing_due", "date": day.isoformat()}]


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "prepare":
            row = prepare(date.fromisoformat(argv[1]))
            print(json.dumps({"status": "prepared", "question": row["question"], "seconds": row.get("seconds")}, ensure_ascii=False))
            return 0
        if argv == ["run"]:
            print(json.dumps(run_scheduled(), ensure_ascii=False))
            return 0
    except (InstagramConfigurationError, RuntimeError, ValueError, KeyError) as exc:
        print(f"Instagram studio failed: {exc}", file=sys.stderr)
        return 1
    print("Usage: python -m lecturesift.studio_social prepare YYYY-MM-DD | run", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
