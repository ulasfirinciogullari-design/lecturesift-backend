"""Fresh narrated Reels once the pre-produced editorial season is exhausted."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError

from .config import INSTAGRAM_DAILY_AUTOMATION_ENABLED, OPENAI_API_KEY, PUBLIC_BASE_URL
from .daily_social import (
    DailyTip, _TIPS, _assert_target_account, _client, _verify_public_video, _wait_until_ready,
    render_tip_reel, render_tip_reel_cover,
)
from .evergreen_social import _PILLARS, _generate, _recent_titles
from .instagram import InstagramConfigurationError
from .social_storage import read_bytes, read_json, recent_json, write_bytes, write_json

_PREFIX = "social/instagram/reels/"


def _key(day: date) -> str:
    return f"{_PREFIX}{day.isoformat()}.json"


def _audio_key(day: date) -> str:
    return f"{_PREFIX}{day.isoformat()}.mp3"


def _row(day: date) -> dict | None:
    return read_json(_key(day))


def _tip_from_row(row) -> DailyTip:
    return DailyTip(
        title=row["title"], body=row["body"], title_tr=row["title_tr"],
        body_tr=row["body_tr"], caption=row["caption"],
        steps=tuple(row["steps"]),
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
    audio = read_bytes(_audio_key(day))
    if audio is None:
        raise RuntimeError("Generated Reel narration is missing")
    return render_tip_reel(_tip_from_row(row), audio, float(row["voice_seconds"]))


def _recent_reel_titles() -> list[str]:
    return [row["title"] for row in recent_json(_PREFIX, limit=60)]


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
    write_bytes(_audio_key(day), voice, "audio/mpeg")
    write_json(_key(day), {
        **data, "caption": caption, "voice_seconds": f"{seconds:.3f}",
        "published_media_id": None, "created_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    })
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
            write_json(_key(selected_day), {**post, "published_media_id": published_item["id"]})
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
    write_json(_key(selected_day), {**post, "published_media_id": published.get("id")})
    return {"status": "published", "kind": "generated_reel", "date": selected_day.isoformat(), "media_id": published.get("id")}


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "prepare":
        print("Usage: python -m lecturesift.generated_reels prepare YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        selected_day = date.fromisoformat(sys.argv[2])
        _ensure_post(selected_day)
    except (InstagramConfigurationError, RuntimeError, ValueError, KeyError) as exc:
        print(f"Instagram Reel preparation failed: {exc}", file=sys.stderr)
        return 1
    print("prepared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
