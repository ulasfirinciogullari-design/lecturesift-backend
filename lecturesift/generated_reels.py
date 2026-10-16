"""Retention-first, product-led Reels for the official LectureSift account."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError
from PIL import Image, ImageDraw

from .config import INSTAGRAM_DAILY_AUTOMATION_ENABLED, OPENAI_API_KEY, PUBLIC_BASE_URL
from .daily_social import (
    DailyTip,
    _AUDIO_DIR,
    _TIPS,
    _assert_target_account,
    _client,
    _fit_wrapped,
    _font,
    _verify_public_video,
    _wait_until_ready,
    render_tip_reel,
    render_tip_reel_cover,
)
from .evergreen_social import _recent_titles
from .instagram import InstagramConfigurationError
from .social_storage import read_bytes, read_json, recent_json, write_bytes, write_json

_PREFIX = "social/instagram/reels/"
_FORMAT_VERSION = 2

# Each brief uses a small, verifiable lesson example. The model writes the hook
# and narration around these facts instead of inventing educational claims.
_BRIEFS = (
    {
        "series": "lecture-to-quiz",
        "student_problem": "rereading a long biology lecture instead of testing recall",
        "source": "52 sayfalık biyoloji dersi",
        "question": "Mitozda kromozomlar hücrenin ortasında hangi evrede dizilir?",
        "answer": "Metafaz",
        "keyword": "ders notlarından test oluşturma",
    },
    {
        "series": "lecture-to-flashcards",
        "student_problem": "highlighting psychology notes without checking memory",
        "source": "Klasik koşullanma notları",
        "question": "Nötr uyarıcı öğrenmeden sonra neye dönüşür?",
        "answer": "Koşullu uyarıcı",
        "keyword": "ders notlarından flashcard hazırlama",
    },
    {
        "series": "three-second-quiz",
        "student_problem": "mistaking recognition for recall in economics",
        "source": "Ekonomi ders kaydı",
        "question": "Talep artarken arz sabitse fiyat genelde ne olur?",
        "answer": "Artar",
        "keyword": "aktif hatırlama mini test",
    },
    {
        "series": "transcript-search",
        "student_problem": "rewatching an entire lecture to find one missed definition",
        "source": "70 dakikalık tarih dersi",
        "question": "Birincil kaynak nedir?",
        "answer": "Döneme doğrudan ait kaynak",
        "keyword": "ders kaydında konu arama",
    },
    {
        "series": "lecture-to-quiz",
        "student_problem": "reading chemistry notes repeatedly before an exam",
        "source": "Asitler ve bazlar dersi",
        "question": "pH 7'nin altı neyi gösterir?",
        "answer": "Asidik çözelti",
        "keyword": "sınava hazırlanmak için quiz",
    },
    {
        "series": "one-gap-review",
        "student_problem": "reviewing everything instead of finding one statistics gap",
        "source": "İstatistik ders notları",
        "question": "Uç değerler medyanı neden az etkiler?",
        "answer": "Sıradaki konuma dayanır",
        "keyword": "eksik konu bulma yöntemi",
    },
    {
        "series": "three-second-quiz",
        "student_problem": "passively rereading anatomy notes",
        "source": "Kalp anatomisi dersi",
        "question": "Oksijenli kanı vücuda hangi odacık pompalar?",
        "answer": "Sol karıncık",
        "keyword": "anatomi aktif hatırlama sorusu",
    },
    {
        "series": "lecture-to-flashcards",
        "student_problem": "memorizing an algorithm definition without its condition",
        "source": "Algoritmalar dersi",
        "question": "Binary search için hangi koşul gerekir?",
        "answer": "Sıralı veri",
        "keyword": "bilgisayar bilimi flashcard",
    },
)

_GROWTH_SCHEMA = {
    "type": "object",
    "properties": {
        name: {"type": "string"}
        for name in ("hook_tr", "hook_en", "payoff_tr", "voice_tr", "caption_tr")
    },
    "required": ["hook_tr", "hook_en", "payoff_tr", "voice_tr", "caption_tr"],
    "additionalProperties": False,
}


def _key(day: date) -> str:
    return f"{_PREFIX}{day.isoformat()}.json"


def _audio_key(day: date) -> str:
    return f"{_PREFIX}{day.isoformat()}.mp3"


def _row(day: date) -> dict | None:
    return read_json(_key(day))


def _legacy_tip(row: dict) -> DailyTip:
    return DailyTip(
        title=row["title"], body=row["body"], title_tr=row["title_tr"],
        body_tr=row["body_tr"], caption=row["caption"], steps=tuple(row["steps"]),
    )


def _clean_text(data: dict, field: str, minimum: int, maximum: int) -> str:
    value = " ".join(str(data.get(field) or "").split())
    if not minimum <= len(value) <= maximum or "#" in value or "@" in value:
        raise ValueError(f"Generated Reel {field} failed its length or text check")
    return value


def _validate_concept(data: dict, brief: dict, recent_hooks: list[str]) -> dict:
    cleaned = {
        "hook_tr": _clean_text(data, "hook_tr", 12, 48),
        "hook_en": _clean_text(data, "hook_en", 12, 58),
        "payoff_tr": _clean_text(data, "payoff_tr", 24, 82),
        "voice_tr": _clean_text(data, "voice_tr", 55, 230),
        "caption_tr": _clean_text(data, "caption_tr", 55, 260),
    }
    word_count = len(cleaned["voice_tr"].split())
    if not 14 <= word_count <= 30:
        raise ValueError("Generated Reel narration must contain 14 to 30 words")
    hook = cleaned["hook_tr"].casefold()
    if any(hook == old.casefold() for old in recent_hooks):
        raise ValueError("Generated Reel hook repeats a recent Reel")
    if not any(term.casefold() in cleaned["voice_tr"].casefold() for term in ("LectureSift", "ders", "not")):
        raise ValueError("Generated Reel narration must connect the lesson to the product workflow")
    if any(term in " ".join(cleaned.values()).casefold() for term in (
        "garanti", "mucize", "viral", "%100", "saniyede hazır", "instant",
    )):
        raise ValueError("Generated Reel makes an unsupported or spammy claim")
    return {**cleaned, **brief, "format_version": _FORMAT_VERSION}


def _fallback_concept(brief: dict) -> dict:
    source = brief["source"]
    hooks = {
        "pain": f"{source}: tekrar okumayı bırak",
        "challenge": brief["question"],
        "transformation": f"{source} → mini test",
    }
    return {
        **brief,
        "format_version": _FORMAT_VERSION,
        "hook_tr": hooks[brief["hook_style"]],
        "hook_en": "Turn lecture notes into a recall test",
        "payoff_tr": "Dersi çalışma paketine çevir; özet, kart ve testle kendini yokla.",
        "voice_tr": (
            f"{source} baştan okunmaz. LectureSift ile dersi çalışma paketine çevir, "
            "testi çöz ve sadece unuttuğun yere dön."
        ),
        "caption_tr": (
            f"{brief['keyword']}: bütün dersi yeniden okumadan önce bir soruyla gerçekten "
            "hatırlayıp hatırlamadığını kontrol et."
        ),
    }


def _generate_concept(day: date, recent_hooks: list[str]) -> dict:
    brief = {
        **_BRIEFS[day.toordinal() % len(_BRIEFS)],
        "hook_style": ("pain", "challenge", "transformation")[day.toordinal() % 3],
    }
    if not OPENAI_API_KEY:
        return _fallback_concept(brief)
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=45, max_retries=1)
    messages = [
        {"role": "system", "content": (
            "Write one short Turkish Instagram Reel for LectureSift. LectureSift turns uploaded lectures, "
            "recordings and documents into a transcript, structured notes, summary, quiz and flashcards. "
            "The viewer must understand the problem in the first second. Follow the requested hook_style: "
            "pain names the frustrating behavior, challenge asks the fixed demo question, and transformation "
            "shows one source becoming one useful output. Do not open with the product name. The narration must be natural Turkish, "
            "14-30 words, and explain one honest product workflow. Do not promise processing speed, exam "
            "results or unsupported learning claims. Avoid generic study advice, listicles, corporate language, "
            "emoji and clickbait. The caption should invite one specific response or save."
        )},
        {"role": "user", "content": json.dumps({
            "brief": brief,
            "fixed_demo_question": brief["question"],
            "fixed_demo_answer": brief["answer"],
            "avoid_recent_hooks": recent_hooks[:40],
            "limits": "Turkish hook <= 48 chars; English cover hook <= 58; payoff <= 82 chars",
        }, ensure_ascii=False)},
    ]
    for _ in range(4):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini", temperature=0.8, max_tokens=550,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "growth_reel", "strict": True, "schema": _GROWTH_SCHEMA},
                },
                messages=messages,
            )
        except OpenAIError:
            return _fallback_concept(brief)
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            continue
        try:
            return _validate_concept(json.loads(choice.message.content or "{}"), brief, recent_hooks)
        except (ValueError, TypeError) as exc:
            messages.extend([
                {"role": "assistant", "content": choice.message.content or "{}"},
                {"role": "user", "content": f"Revise the draft: {exc}. Return only valid JSON."},
            ])
    return _fallback_concept(brief)


def _frame_base(accent: str, progress: float) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (720, 1280), "#030816")
    draw = ImageDraw.Draw(image)
    draw.ellipse((390, -210, 850, 250), fill=accent)
    draw.ellipse((-260, 930, 230, 1420), fill="#17114a")
    draw.rounded_rectangle((34, 40, 686, 1240), radius=42, fill="#07162c", outline="#2e6cff", width=3)
    draw.rounded_rectangle((72, 82, 296, 142), radius=24, fill="#102b4e")
    draw.text((99, 100), "LECTURESIFT", fill="#53e3d5", font=_font(24, True))
    draw.rounded_rectangle((72, 1188, 648, 1202), radius=7, fill="#17345d")
    draw.rounded_rectangle((72, 1188, 72 + int(576 * progress), 1202), radius=7, fill="#53e3d5")
    return image, draw


def _hook_frame(row: dict) -> Image.Image:
    image, draw = _frame_base("#4a174f", 0.12)
    labels = {"pain": "BUNU YAPMA", "challenge": "3 SANİYEN VAR", "transformation": "ÖNCE → SONRA"}
    label = labels.get(row.get("hook_style"), "HEMEN DENE")
    draw.rounded_rectangle((72, 220, 346, 278), radius=22, fill="#e34068")
    draw.text((100, 238), label, fill="white", font=_font(23, True))
    font, text = _fit_wrapped(draw, row["hook_tr"], 570, 430, 72, 46, max_lines=4, bold=True)
    box = draw.multiline_textbbox((0, 0), text, font=font, spacing=16)
    draw.multiline_text((72, 435 - (box[3] - box[1]) / 2), text, fill="white", font=font, spacing=16)
    draw.text((72, 1015), "1 ders  →  1 çalışma paketi", fill="#9fc4f5", font=_font(28, True))
    return image


def _source_frame(row: dict) -> Image.Image:
    image, draw = _frame_base("#173b72", 0.38)
    draw.text((72, 220), "DAĞINIK KAYNAK", fill="#8dbdff", font=_font(27, True))
    draw.rounded_rectangle((72, 320, 648, 675), radius=34, fill="#0f294e", outline="#315f98", width=2)
    draw.rounded_rectangle((106, 362, 186, 442), radius=18, fill="#e34068")
    draw.text((128, 382), "▶", fill="white", font=_font(29, True))
    font, source = _fit_wrapped(draw, row["source"], 400, 105, 43, 31, max_lines=3, bold=True)
    draw.multiline_text((214, 360), source, fill="white", font=font, spacing=9)
    for index, width in enumerate((430, 500, 382, 470)):
        draw.rounded_rectangle((106, 493 + index * 38, 106 + width, 509 + index * 38), radius=8, fill="#284e78")
    draw.text((72, 775), "Tekrar okumak ≠ hatırlamak", fill="#f6c85f", font=_font(39, True))
    draw.text((72, 855), "Önce kendini test et.", fill="white", font=_font(35, True))
    return image


def _product_frame(row: dict) -> Image.Image:
    image, draw = _frame_base("#123e4a", 0.70)
    draw.text((72, 205), "ÇALIŞMA PAKETİ", fill="#53e3d5", font=_font(28, True))
    draw.rounded_rectangle((72, 285, 648, 860), radius=34, fill="#f7f9fd")
    draw.text((110, 328), "Ders işlendi", fill="#17345d", font=_font(26, True))
    draw.ellipse((566, 320, 610, 364), fill="#37bd85")
    draw.text((577, 328), "✓", fill="white", font=_font(25, True))
    chips = (("ÖZET", "#315fee"), ("TEST", "#8457e8"), ("KARTLAR", "#10a885"))
    for index, (label, color) in enumerate(chips):
        top = 425 + index * 118
        draw.rounded_rectangle((110, top, 610, top + 82), radius=22, fill="#eaf0fa")
        draw.rounded_rectangle((130, top + 18, 250, top + 64), radius=18, fill=color)
        draw.text((151, top + 29), label, fill="white", font=_font(21, True))
        draw.text((284, top + 27), "Hazır", fill="#163457", font=_font(26, True))
    font, payoff = _fit_wrapped(draw, row["payoff_tr"], 575, 180, 37, 29, max_lines=4, bold=True)
    draw.multiline_text((72, 945), payoff, fill="white", font=font, spacing=10)
    return image


def _quiz_frame(row: dict) -> Image.Image:
    image, draw = _frame_base("#2d195f", 1.0)
    draw.text((72, 205), "3 SANİYELİK TEST", fill="#c8b4ff", font=_font(29, True))
    draw.rounded_rectangle((72, 292, 648, 805), radius=34, fill="#10294d", outline="#7658dd", width=3)
    font, question = _fit_wrapped(draw, row["question"], 500, 230, 44, 31, max_lines=5, bold=True)
    draw.multiline_text((110, 350), question, fill="white", font=font, spacing=12)
    draw.line((110, 610, 610, 610), fill="#315479", width=2)
    draw.text((110, 656), "CEVAP", fill="#8dbdff", font=_font(23, True))
    answer_font, answer = _fit_wrapped(draw, row["answer"], 490, 92, 43, 31, max_lines=2, bold=True)
    draw.multiline_text((110, 704), answer, fill="#53e3d5", font=answer_font, spacing=8)
    draw.rounded_rectangle((72, 905, 648, 1010), radius=28, fill="#315fee")
    draw.text((105, 938), "Yarınki test için takip et", fill="white", font=_font(31, True))
    draw.text((72, 1065), "@lecturesift  •  lecturesift.com", fill="#bcd5f5", font=_font(26, True))
    return image


def render_growth_reel_cover(row: dict) -> bytes:
    output = io.BytesIO()
    _hook_frame(row).save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()


def render_growth_reel(row: dict, voice_audio: bytes, voice_duration: float) -> bytes:
    """Render a fast-cut product demo with a hook inside the first second."""
    work = tempfile.mkdtemp(prefix="lecturesift-growth-reel-")
    try:
        if not voice_audio or not 3 <= voice_duration <= 15:
            raise RuntimeError("Instagram Reel narration duration is outside the retention format")
        voice = Path(work) / "voice.mp3"
        voice.write_bytes(voice_audio)
        duration = max(8.0, voice_duration + 0.35)
        scene_durations = (1.15, duration * 0.24, duration * 0.31)
        scene_durations += (duration - sum(scene_durations),)
        frames = (_hook_frame(row), _source_frame(row), _product_frame(row), _quiz_frame(row))
        for index, frame in enumerate(frames):
            frame.save(f"{work}/frame-{index}.jpg", format="JPEG", quality=90)
        output_path = f"{work}/reel.mp4"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            *[argument for index in range(4) for argument in (
                "-loop", "1", "-framerate", "30", "-t", f"{scene_durations[index]:.3f}",
                "-i", f"{work}/frame-{index}.jpg",
            )],
            "-i", str(voice), "-stream_loop", "-1", "-i", str(_AUDIO_DIR / "music-bed.m4a"),
            "-filter_complex", (
                "[0:v]scale=720:1280,zoompan=z='min(zoom+0.0018,1.06)':d=1:s=720x1280:fps=30,"
                f"trim=duration={scene_durations[0]:.3f},setpts=PTS-STARTPTS[v0];"
                "[1:v]scale=720:1280,zoompan=z='min(zoom+0.0012,1.05)':d=1:s=720x1280:fps=30,"
                f"trim=duration={scene_durations[1]:.3f},setpts=PTS-STARTPTS[v1];"
                "[2:v]scale=720:1280,zoompan=z='min(zoom+0.0012,1.05)':d=1:s=720x1280:fps=30,"
                f"trim=duration={scene_durations[2]:.3f},setpts=PTS-STARTPTS[v2];"
                "[3:v]scale=720:1280,zoompan=z='min(zoom+0.0010,1.04)':d=1:s=720x1280:fps=30,"
                f"trim=duration={scene_durations[3]:.3f},setpts=PTS-STARTPTS[v3];"
                "[v0][v1][v2][v3]concat=n=4:v=1:a=0,format=yuv420p[v];"
                "[4:a]aresample=48000[voice];"
                f"[5:a]aresample=48000,atrim=0:{duration:.3f},volume=0.18[bed];"
                "[voice][bed]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                f"atrim=0:{duration:.3f},alimiter=limit=0.9,"
                f"afade=t=out:st={duration - 0.35:.3f}:d=0.35[a]"
            ),
            "-map", "[v]", "-map", "[a]", "-r", "30", "-threads", "1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", output_path,
        ]
        completed = subprocess.run(command, capture_output=True, timeout=45, check=False)
        if completed.returncode != 0:
            raise RuntimeError("Instagram growth Reel could not be rendered")
        data = Path(output_path).read_bytes()
        if len(data) < 10_000:
            raise RuntimeError("Instagram growth Reel output is invalid")
        return data
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Instagram growth Reel renderer is unavailable") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cover_for_day(day: date) -> bytes | None:
    row = _row(day)
    if not row:
        return None
    if row.get("format_version") == _FORMAT_VERSION:
        return render_growth_reel_cover(row)
    return render_tip_reel_cover(_legacy_tip(row))


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
    if row.get("format_version") == _FORMAT_VERSION:
        return render_growth_reel(row, audio, float(row["voice_seconds"]))
    return render_tip_reel(_legacy_tip(row), audio, float(row["voice_seconds"]))


def _recent_reel_hooks() -> list[str]:
    return [row.get("hook_tr") or row.get("title") or "" for row in recent_json(_PREFIX, limit=60)]


def _speech(data: dict) -> tuple[bytes, float]:
    if not OPENAI_API_KEY:
        raise InstagramConfigurationError("OpenAI key is required for narrated Reels")
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=60, max_retries=1)
    with tempfile.TemporaryDirectory(prefix="lecturesift-voice-") as folder:
        audio_path = Path(folder) / "voice.mp3"
        try:
            with client.audio.speech.with_streaming_response.create(
                model="gpt-4o-mini-tts", voice="coral", input=data["voice_tr"],
                instructions=(
                    "Natural Turkish creator voice. Start immediately, speak briskly and clearly, "
                    "sound curious rather than promotional, and do not add pauses at the beginning."
                ),
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
        if not 3 <= seconds <= 15:
            raise RuntimeError("Generated Reel narration is too long for the retention format")
        return audio, seconds


def _ensure_post(day: date):
    existing = _row(day)
    if existing:
        return existing
    recent_hooks = _recent_reel_hooks() + _recent_titles() + [tip[0] for tip in _TIPS]
    data = _generate_concept(day, recent_hooks)
    voice, seconds = _speech(data)
    marker = f"LectureSift · reel · {day.isoformat()}"
    hashtags = "#DersÇalışma #Üniversite #SınavHazırlığı #AktifHatırlama #Öğrenci #LectureSift"
    caption = (
        f"{data['hook_tr']}\n\n{data['caption_tr']}\n\n"
        "Yarınki mini test için @lecturesift'i takip et.\n\n"
        f"{hashtags}\n{marker}\nVoice: AI-generated."
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
