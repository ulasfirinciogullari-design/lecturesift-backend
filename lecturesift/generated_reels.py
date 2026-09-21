"""Fresh narrated Reels once the pre-produced editorial season is exhausted."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError
from sqlalchemy import Column, Date, DateTime, LargeBinary, MetaData, String, Table, Text, select, update
from sqlalchemy.exc import IntegrityError

from .config import INSTAGRAM_DAILY_AUTOMATION_ENABLED, OPENAI_API_KEY, PUBLIC_BASE_URL
from .daily_social import (
    DailyTip, _TIPS, _assert_target_account, _client, _verify_public_video, _wait_until_ready,
    render_tip_reel, render_tip_reel_cover,
)
from .evergreen_social import _PILLARS, _engine, _generate, _recent_titles
from .instagram import InstagramConfigurationError


_META = MetaData()
_REELS = Table(
    "instagram_generated_reels", _META,
    Column("day", Date, primary_key=True),
    Column("title", String(100), nullable=False),
    Column("body", String(180), nullable=False),
    Column("title_tr", String(100), nullable=False),
    Column("body_tr", String(180), nullable=False),
    Column("steps_json", Text, nullable=False),
    Column("keyword", String(80), nullable=False),
    Column("caption", Text, nullable=False),
    Column("voice_mp3", LargeBinary, nullable=False),
    Column("voice_seconds", String(20), nullable=False),
    Column("published_media_id", String(100)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


@lru_cache(maxsize=1)
def _ready():
    engine = _engine()
    _META.create_all(engine, tables=[_REELS])
    return engine


def _row(day: date):
    with _ready().connect() as connection:
        return connection.execute(select(_REELS).where(_REELS.c.day == day)).mappings().first()


def _tip_from_row(row) -> DailyTip:
    return DailyTip(
        title=row["title"], body=row["body"], title_tr=row["title_tr"],
        body_tr=row["body_tr"], caption=row["caption"],
        steps=tuple(json.loads(row["steps_json"])),
    )


def cover_for_day(day: date) -> bytes | None:
    row = _row(day)
    return render_tip_reel_cover(_tip_from_row(row)) if row else None


def video_for_day(day: date) -> bytes | None:
    row = _row(day)
    if not row:
        return None
    return _render_saved_video(day)


@lru_cache(maxsize=2)
def _render_saved_video(day: date) -> bytes:
    row = _row(day)
    return render_tip_reel(_tip_from_row(row), bytes(row["voice_mp3"]), float(row["voice_seconds"]))


def _recent_reel_titles() -> list[str]:
    with _ready().connect() as connection:
        return list(connection.execute(select(_REELS.c.title).order_by(_REELS.c.day.desc()).limit(90)).scalars())


def _speech(data: dict) -> tuple[bytes, float]:
    if not OPENAI_API_KEY:
        raise InstagramConfigurationError("OpenAI key is required for narrated Reels")
    script = (
        f"{data['title']}. {data['body']} "
        + " ".join(f"Step {index}: {step}" for index, step in enumerate(data["steps"], 1))
        + " Try this after your next lecture."
    )
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=60, max_retries=1)
    with tempfile.TemporaryDirectory(prefix="lecturesift-voice-") as folder:
        audio_path = Path(folder) / "voice.mp3"
        try:
            with client.audio.speech.with_streaming_response.create(
                model="gpt-4o-mini-tts", voice="coral", input=script,
                instructions="Clear, warm study coach. Speak briskly and naturally; pause slightly between steps.",
            ) as response:
                response.stream_to_file(audio_path)
        except OpenAIError as exc:
            raise InstagramConfigurationError("Reel narration service is unavailable") from exc
        audio = audio_path.read_bytes()
        if len(audio) < 10_000:
            raise RuntimeError("Generated Reel narration is incomplete")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, timeout=10, check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError("Generated Reel narration duration is unavailable")
        seconds = float(probe.stdout.decode().strip())
        if not 3 <= seconds <= 60:
            raise RuntimeError("Generated Reel narration duration is outside the approved range")
        return audio, seconds


def _ensure_post(day: date):
    existing = _row(day)
    if existing:
        return existing
    morning_titles = _recent_titles()
    recent = morning_titles[:2] + _recent_reel_titles()[:60] + [tip[0] for tip in _TIPS] + morning_titles[2:]
    data = _generate(day, recent, pillar_offset=3)
    voice, seconds = _speech(data)
    _, hashtags = _PILLARS[(day.toordinal() + 3) % len(_PILLARS)]
    marker = f"LectureSift · reel · {day.isoformat()}"
    caption = (
        f"{data['title']} | {data['keyword']}\n\n{data['body']}\n\n"
        + "\n".join(f"{index}. {step}" for index, step in enumerate(data["steps"], 1))
        + f"\n\n🇹🇷 {data['title_tr']}: {data['body_tr']}\n\n"
        + "Save this for your next study session. Voice: AI-generated.\n\n"
        + f"{hashtags}\n{marker}"
    )
    try:
        with _ready().begin() as connection:
            connection.execute(_REELS.insert().values(
                day=day, title=data["title"], body=data["body"], title_tr=data["title_tr"],
                body_tr=data["body_tr"], steps_json=json.dumps(data["steps"], ensure_ascii=False),
                keyword=data["keyword"], caption=caption, voice_mp3=voice,
                voice_seconds=f"{seconds:.3f}", created_at=datetime.now(ZoneInfo("UTC")),
            ))
    except IntegrityError:
        pass
    return _row(day)


def publish_generated_reel(day: date | None = None) -> dict:
    if not INSTAGRAM_DAILY_AUTOMATION_ENABLED:
        return {"status": "disabled"}
    selected_day = day or datetime.now(ZoneInfo("Europe/Istanbul")).date()
    client = _client()
    _assert_target_account(client)
    post = _ensure_post(selected_day)
    if post["published_media_id"]:
        return {"status": "already_published", "kind": "generated_reel", "date": selected_day.isoformat()}
    marker = f"LectureSift · reel · {selected_day.isoformat()}"
    recent = client.get_recent_media(limit=50).get("data", [])
    published_item = next((item for item in recent if marker in (item.get("caption") or "")), None)
    if published_item:
        if published_item.get("id"):
            with _ready().begin() as connection:
                connection.execute(update(_REELS).where(_REELS.c.day == selected_day).values(published_media_id=published_item["id"]))
        return {"status": "already_published", "kind": "generated_reel", "date": selected_day.isoformat()}
    base_url = (PUBLIC_BASE_URL or "https://api.lecturesift.com").rstrip("/")
    media_url = f"{base_url}/instagram/evergreen/reel/{selected_day.isoformat()}.mp4"
    _verify_public_video(media_url)
    container = client.create_media_container(
        media_url=media_url, caption=post["caption"], media_type="REELS",
        cover_url=f"{base_url}/instagram/evergreen/reel/{selected_day.isoformat()}.jpg",
    )
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    with _ready().begin() as connection:
        connection.execute(update(_REELS).where(_REELS.c.day == selected_day).values(published_media_id=published.get("id")))
    return {"status": "published", "kind": "generated_reel", "date": selected_day.isoformat(), "media_id": published.get("id")}
