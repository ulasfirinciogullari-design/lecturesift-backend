"""Privacy-safe scheduled Instagram publishing for LectureSift."""

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
    caption: str


_TIPS = (
    ("25 dakika odaklan", "Tek bir konu seç.\n25 dk çalış, 5 dk ara ver."),
    ("Aktif hatırlama", "Notu kapat.\nKendine soruyu sen sor."),
    ("Feynman tekniği", "Konuyu sade bir dille\nbir arkadaşına anlatır gibi yaz."),
    ("Aralıklı tekrar", "Bugün, 3 gün sonra\nve 1 hafta sonra tekrar et."),
    ("Mini hedef koy", "Bugün sadece\ntek bir küçük bölümü bitir."),
    ("Dikkat dağıtanları kapat", "Bildirimleri sustur.\nMasanda yalnızca gerekli olan kalsın."),
    ("Kendini test et", "Okumayı bırakıp\n3 soru çözmeyi dene."),
)


def _index(day: date) -> int:
    return int(sha256(day.isoformat().encode("utf-8")).hexdigest(), 16) % len(_TIPS)


def daily_tip(day: date) -> DailyTip:
    title, body = _TIPS[_index(day)]
    marker = f"#LectureSiftGununNotu{day:%Y%m%d}"
    caption = (
        f"🎓 Günün çalışma notu: {title}\n\n{body.replace(chr(10), ' ')}\n\n"
        "Ders videolarını özet, quiz ve flashcard'a dönüştür.\n"
        f"{marker} #studygram #öğrenci #çalışmatavsiyesi"
    )
    return DailyTip(title=title, body=body, caption=caption)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold else ("DejaVuSans.ttf", "Arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_daily_image(day: date) -> bytes:
    tip = daily_tip(day)
    image = Image.new("RGB", (1080, 1080), "#071429")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((68, 68, 1012, 1012), radius=48, fill="#0d2244", outline="#39a6ff", width=3)
    draw.rounded_rectangle((108, 122, 365, 188), radius=28, fill="#18385f")
    draw.text((140, 140), "LECTURESIFT", fill="#4ce0a3", font=_font(28, True))
    draw.text((108, 258), "Günün çalışma notu", fill="#a9c9ef", font=_font(39))
    draw.multiline_text((108, 338), tip.title, fill="white", font=_font(74, True), spacing=12)
    draw.multiline_text((108, 540), tip.body, fill="#d9e9ff", font=_font(47), spacing=22)
    draw.rounded_rectangle((108, 846, 728, 918), radius=26, fill="#39a6ff")
    draw.text((140, 866), "Daha akıllı çalış.", fill="#061022", font=_font(31, True))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()


def render_daily_reel_cover(day: date) -> bytes:
    """Render a vertical, readable 9:16 cover without using customer content."""
    tip = daily_tip(day)
    image = Image.new("RGB", (1080, 1920), "#071429")
    draw = ImageDraw.Draw(image)
    draw.ellipse((620, -180, 1220, 420), fill="#17376d")
    draw.rounded_rectangle((70, 90, 1010, 1830), radius=58, fill="#0d2244", outline="#39a6ff", width=4)
    draw.rounded_rectangle((118, 150, 430, 226), radius=30, fill="#18385f")
    draw.text((154, 171), "LECTURESIFT", fill="#4ce0a3", font=_font(31, True))
    draw.text((118, 390), "Günün çalışma notu", fill="#a9c9ef", font=_font(44))
    draw.multiline_text((118, 505), tip.title, fill="white", font=_font(78, True), spacing=14)
    draw.line((118, 750, 962, 750), fill="#264d78", width=3)
    draw.multiline_text((118, 860), tip.body, fill="#d9e9ff", font=_font(52), spacing=28)
    draw.rounded_rectangle((118, 1525, 750, 1612), radius=30, fill="#39a6ff")
    draw.text((154, 1548), "Daha akıllı çalış.", fill="#061022", font=_font(35, True))
    draw.text((118, 1705), "lecturesift.com", fill="#4ce0a3", font=_font(31, True))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()


@lru_cache(maxsize=4)
def render_daily_reel(day: date) -> bytes:
    """Create a short MP4 Reel with a subtle, deterministic motion effect."""
    work = tempfile.mkdtemp(prefix="lecturesift-reel-")
    try:
        cover_path = f"{work}/cover.jpg"
        output_path = f"{work}/reel.mp4"
        with open(cover_path, "wb") as cover:
            cover.write(render_daily_reel_cover(day))
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-i", cover_path, "-t", "9",
            "-vf", "zoompan=z='min(zoom+0.00025,1.05)':d=270:s=1080x1920:fps=30,format=yuv420p",
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
    """Publish the next launch card first; once launch is complete, publish the daily study tip."""
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
