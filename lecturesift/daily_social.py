"""Privacy-safe scheduled Instagram publishing for LectureSift.

The daily creative system is English-first for the global feed, while every
creative and caption also carries a concise Turkish localization. No customer
content, private data, follower scraping or engagement automation is used.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from hashlib import sha256

from PIL import Image, ImageDraw, ImageFont

from .config import (
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_ACCOUNT_ID,
    INSTAGRAM_APP_SECRET,
    INSTAGRAM_DAILY_AUTOMATION_ENABLED,
    INSTAGRAM_DAILY_MEDIA_TYPE,
    INSTAGRAM_GRAPH_API_VERSION,
    PUBLIC_BASE_URL,
)
from .instagram import InstagramAPIError, InstagramClient, InstagramConfigurationError
from .launch_social import next_pending_post


@dataclass(frozen=True)
class DailyTip:
    title: str
    body: str
    title_tr: str
    body_tr: str
    caption: str


# A rotating mix of product education, study workflow and active-learning ideas.
# The feed remains useful even to people who have not signed up yet.
_TIPS = (
    (
        "Stop rewatching lectures",
        "Turn the useful parts into a review pack instead.",
        "Dersi baştan sona tekrar izleme",
        "İşe yarayan kısımları tekrar paketine dönüştür.",
    ),
    (
        "One lecture. Five study tools.",
        "Transcript, notes, summary, quiz and flashcards.",
        "Tek ders. Beş çalışma aracı.",
        "Transkript, not, özet, quiz ve flashcard.",
    ),
    (
        "Use active recall",
        "Close the notes. Ask yourself a question first.",
        "Aktif hatırlama kullan",
        "Notu kapat. Önce soruyu kendine sor.",
    ),
    (
        "Turn slides into questions",
        "Every key slide can become a quick self-test.",
        "Slaytları soruya dönüştür",
        "Her önemli slaytı kısa bir teste çevir.",
    ),
    (
        "Search, don't rewatch",
        "A transcript helps you jump back to the part you need.",
        "Tekrar izleme, ara",
        "Transkript ihtiyacın olan bölümü hızlıca buldurur.",
    ),
    (
        "Study in three passes",
        "Understand → recall → test. Repeat what actually matters.",
        "Üç turda çalış",
        "Anla → hatırla → test et. Önemli olanı tekrar et.",
    ),
    (
        "Make revision reusable",
        "Build notes once, then review them as quizzes and flashcards.",
        "Tekrarı yeniden kullanılabilir yap",
        "Notu bir kez oluştur; quiz ve flashcard olarak tekrar kullan.",
    ),
    (
        "Combine your sources",
        "Video, audio and slides work better when they stay in one flow.",
        "Kaynaklarını birleştir",
        "Video, ses ve slayt tek akışta daha kullanışlıdır.",
    ),
    (
        "Don't just highlight",
        "Turn a key idea into a question you must answer.",
        "Sadece altını çizme",
        "Önemli fikri cevaplaman gereken bir soruya dönüştür.",
    ),
    (
        "A 60-minute lecture is not a 60-minute review",
        "Keep the concepts you need and cut the busywork.",
        "60 dakikalık ders, 60 dakikalık tekrar değildir",
        "Gerekli kavramları tut; gereksiz uğraşı azalt.",
    ),
    (
        "Build a study pack",
        "One source can become several ways to learn.",
        "Çalışma paketi oluştur",
        "Tek kaynak birden fazla öğrenme biçimine dönüşebilir.",
    ),
    (
        "Test before you feel ready",
        "A short quiz shows what you actually remember.",
        "Hazır hissetmeden test ol",
        "Kısa bir quiz gerçekten neyi hatırladığını gösterir.",
    ),
    (
        "Review the hard parts first",
        "Spend time where recall breaks, not where it feels easy.",
        "Önce zor kısımları tekrar et",
        "Kolay gelen yere değil, hatırlamanın koptuğu yere zaman ayır.",
    ),
    (
        "Make lecture notes work harder",
        "Turn passive notes into questions, quizzes and flashcards.",
        "Ders notlarını daha işlevli kullan",
        "Pasif notları soru, quiz ve flashcard'a dönüştür.",
    ),
)


def _index(day: date) -> int:
    return int(sha256(day.isoformat().encode("utf-8")).hexdigest(), 16) % len(_TIPS)


def daily_tip(day: date) -> DailyTip:
    title, body, title_tr, body_tr = _TIPS[_index(day)]
    marker = f"#LectureSiftGununNotu{day:%Y%m%d}"
    caption = (
        f"🎓 {title}\n\n{body}\n\n"
        "LectureSift turns lecture-heavy learning into transcripts, structured notes, quizzes and flashcards.\n\n"
        f"🇹🇷 {title_tr}\n{body_tr}\n\n"
        "LectureSift; dersleri transkript, düzenli not, quiz ve flashcard gibi çalışmaya hazır çıktılara dönüştürür.\n\n"
        f"{marker} #LectureSift #StudySmarter #AIForStudents #EdTech #StudyTips #Öğrenci"
    )
    return DailyTip(title=title, body=body, title_tr=title_tr, body_tr=body_tr, caption=caption)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold else ("DejaVuSans.ttf", "Arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) >= max_lines - 1:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return "\n".join(lines)


def _fit_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int,
    *,
    max_lines: int,
    bold: bool,
) -> tuple[ImageFont.ImageFont, str]:
    for size in range(start_size, min_size - 1, -2):
        font = _font(size, bold)
        wrapped = _wrap(draw, text, font, max_width, max_lines)
        box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=max(8, size // 6))
        if box[2] <= max_width and box[3] <= max_height:
            return font, wrapped
    font = _font(min_size, bold)
    return font, _wrap(draw, text, font, max_width, max_lines)


def render_daily_image(day: date) -> bytes:
    """Render a 3:4 fallback feed card using the same grid-safe visual system."""
    tip = daily_tip(day)
    image = Image.new("RGB", (1080, 1440), "#050b1f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((650, -100, 1160, 410), fill="#18376c")
    draw.rounded_rectangle((72, 72, 1008, 1368), radius=58, fill="#08172f", outline="#2c6dff", width=3)
    draw.rounded_rectangle((118, 130, 430, 204), radius=30, fill="#10294d")
    draw.text((153, 149), "LECTURESIFT", fill="#4ce0d4", font=_font(30, True))
    draw.text((118, 282), "STUDY SMARTER", fill="#8dbdff", font=_font(30, True))

    title_font, title = _fit_wrapped(draw, tip.title, 830, 230, 78, 52, max_lines=3, bold=True)
    draw.multiline_text((118, 345), title, fill="white", font=title_font, spacing=14)
    title_box = draw.multiline_textbbox((118, 345), title, font=title_font, spacing=14)

    body_y = max(620, title_box[3] + 55)
    body_font, body = _fit_wrapped(draw, tip.body, 820, 190, 43, 34, max_lines=3, bold=False)
    draw.multiline_text((118, body_y), body, fill="#d9e8ff", font=body_font, spacing=16)

    draw.line((118, 880, 962, 880), fill="#254b78", width=3)
    draw.text((118, 930), "TR", fill="#4ce0d4", font=_font(29, True))
    tr_font, tr_title = _fit_wrapped(draw, tip.title_tr, 760, 145, 45, 34, max_lines=3, bold=True)
    draw.multiline_text((190, 922), tr_title, fill="#eef5ff", font=tr_font, spacing=10)
    draw.text((118, 1115), "lecturesift.com", fill="#4ce0a3", font=_font(31, True))
    draw.rounded_rectangle((118, 1190, 744, 1280), radius=30, fill="#386fff")
    draw.text((155, 1216), "Lecture → Study Pack", fill="white", font=_font(34, True))

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=93, optimize=True)
    return output.getvalue()


def render_daily_reel_cover(day: date) -> bytes:
    """Render a 9:16 bilingual hook card with a conservative profile-safe center."""
    tip = daily_tip(day)
    image = Image.new("RGB", (1080, 1920), "#050b1f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((620, -170, 1220, 430), fill="#18376c")
    draw.ellipse((-260, 1280, 360, 1900), fill="#28175a")
    draw.rounded_rectangle((70, 90, 1010, 1830), radius=60, fill="#08172f", outline="#386fff", width=4)

    draw.rounded_rectangle((118, 150, 435, 226), radius=30, fill="#10294d")
    draw.text((153, 170), "LECTURESIFT", fill="#4ce0d4", font=_font(31, True))
    draw.text((118, 352), "STUDY SMARTER", fill="#8dbdff", font=_font(34, True))

    title_font, title = _fit_wrapped(draw, tip.title, 825, 310, 84, 54, max_lines=3, bold=True)
    draw.multiline_text((118, 440), title, fill="white", font=title_font, spacing=15)
    title_box = draw.multiline_textbbox((118, 440), title, font=title_font, spacing=15)

    body_y = max(790, title_box[3] + 65)
    body_font, body = _fit_wrapped(draw, tip.body, 820, 225, 49, 36, max_lines=4, bold=False)
    draw.multiline_text((118, body_y), body, fill="#d9e8ff", font=body_font, spacing=20)

    draw.line((118, 1110, 962, 1110), fill="#285184", width=3)
    draw.text((118, 1180), "TÜRKÇE", fill="#4ce0d4", font=_font(30, True))
    tr_title_font, tr_title = _fit_wrapped(draw, tip.title_tr, 820, 190, 49, 36, max_lines=3, bold=True)
    draw.multiline_text((118, 1245), tr_title, fill="#f4f8ff", font=tr_title_font, spacing=12)
    tr_box = draw.multiline_textbbox((118, 1245), tr_title, font=tr_title_font, spacing=12)
    tr_body_y = max(1435, tr_box[3] + 34)
    tr_body_font, tr_body = _fit_wrapped(draw, tip.body_tr, 820, 150, 35, 30, max_lines=3, bold=False)
    draw.multiline_text((118, tr_body_y), tr_body, fill="#bed6f6", font=tr_body_font, spacing=12)

    draw.rounded_rectangle((118, 1660, 810, 1748), radius=31, fill="#386fff")
    draw.text((153, 1684), "Turn lectures into study packs", fill="white", font=_font(31, True))
    draw.text((118, 1780), "lecturesift.com", fill="#4ce0a3", font=_font(29, True))

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=93, optimize=True)
    return output.getvalue()


@lru_cache(maxsize=4)
def render_daily_reel(day: date) -> bytes:
    """Create a short MP4 Reel with subtle motion and a strong first-frame hook."""
    work = tempfile.mkdtemp(prefix="lecturesift-reel-")
    try:
        cover_path = f"{work}/cover.jpg"
        output_path = f"{work}/reel.mp4"
        with open(cover_path, "wb") as cover:
            cover.write(render_daily_reel_cover(day))
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-i", cover_path, "-t", "10",
            "-vf", "zoompan=z='min(zoom+0.00022,1.045)':d=300:s=1080x1920:fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-movflags", "+faststart", "-an", output_path,
        ]
        completed = subprocess.run(command, capture_output=True, timeout=75, check=False)
        if completed.returncode != 0:
            raise RuntimeError("Instagram Reel could not be rendered")
        with open(output_path, "rb") as reel:
            data = reel.read()
        if len(data) < 10_000:
            raise RuntimeError("Instagram Reel output is invalid")
        return data
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Instagram Reel renderer is unavailable") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def is_already_published(client: InstagramClient, day: date) -> bool:
    marker = f"#LectureSiftGununNotu{day:%Y%m%d}"
    return any(marker in (item.get("caption") or "") for item in client.get_recent_media().get("data", []))


def _wait_until_ready(client: InstagramClient, container_id: str) -> None:
    for _ in range(48):
        status = client.get_container_status(container_id).get("status_code")
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise InstagramAPIError("Instagram media processing failed")
        time.sleep(5)
    raise InstagramAPIError("Instagram media processing timed out")


def _client() -> InstagramClient:
    return InstagramClient(
        access_token=INSTAGRAM_ACCESS_TOKEN,
        account_id=INSTAGRAM_ACCOUNT_ID,
        app_secret=INSTAGRAM_APP_SECRET,
        api_version=INSTAGRAM_GRAPH_API_VERSION,
    )


def _assert_target_account(client: InstagramClient) -> None:
    account = client.get_account()
    if (account.get("username") or "").lower() != "lecturesift":
        raise InstagramConfigurationError("Configured Instagram account is not @lecturesift")


def publish_next_launch_post(*, only_if_none_completed: bool = False, force: bool = False) -> dict:
    """Publish the first missing launch-grid card, with marker-based idempotency."""
    if not force and not INSTAGRAM_DAILY_AUTOMATION_ENABLED:
        return {"status": "disabled"}

    base_url = PUBLIC_BASE_URL or "https://lecturesift-backend.onrender.com"
    client = _client()
    _assert_target_account(client)
    recent = client.get_recent_media(limit=50).get("data", [])
    captions = "\n".join((item.get("caption") or "") for item in recent)
    completed = [idx for idx in range(1, 10) if f"#LectureSiftLaunch{idx:02d}" in captions]
    if only_if_none_completed and completed:
        return {"status": "launch_already_started", "completed": completed}

    post = next_pending_post(recent)
    if post is None:
        return {"status": "launch_complete", "completed": completed}

    media_url = f"{base_url}/instagram/launch/image/{post.index}.jpg"
    container = client.create_media_container(media_url=media_url, caption=post.caption)
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    return {"status": "published", "kind": "launch", "index": post.index, "media_id": published.get("id")}


def publish_daily_post(day: date | None = None) -> dict:
    """Publish launch cards first; once complete, publish one bilingual daily Reel."""
    if not INSTAGRAM_DAILY_AUTOMATION_ENABLED:
        return {"status": "disabled"}

    launch = publish_next_launch_post()
    if launch.get("status") != "launch_complete":
        return launch

    selected_day = day or date.today()
    client = _client()
    _assert_target_account(client)
    if is_already_published(client, selected_day):
        return {"status": "already_published", "kind": "daily", "date": selected_day.isoformat()}
    tip = daily_tip(selected_day)
    base_url = PUBLIC_BASE_URL or "https://lecturesift-backend.onrender.com"
    media_type = INSTAGRAM_DAILY_MEDIA_TYPE.upper()
    if media_type == "REELS":
        media_url = f"{base_url}/instagram/daily/reel/{selected_day.isoformat()}.mp4"
        cover_url = f"{base_url}/instagram/daily/reel/{selected_day.isoformat()}.jpg"
        container = client.create_media_container(
            media_url=media_url,
            caption=tip.caption,
            media_type="REELS",
            cover_url=cover_url,
        )
    elif media_type == "IMAGE":
        container = client.create_media_container(
            media_url=f"{base_url}/instagram/daily/image/{selected_day.isoformat()}.jpg",
            caption=tip.caption,
        )
    else:
        raise InstagramConfigurationError("INSTAGRAM_DAILY_MEDIA_TYPE must be IMAGE or REELS")
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    return {
        "status": "published",
        "kind": "daily",
        "media_type": media_type,
        "date": selected_day.isoformat(),
        "media_id": published.get("id"),
    }


def main() -> int:
    try:
        result = publish_daily_post()
    except (InstagramAPIError, InstagramConfigurationError, RuntimeError, KeyError) as exc:
        print(f"Instagram scheduled post failed: {exc}", file=sys.stderr)
        return 1
    print(result.get("status", "unknown"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
