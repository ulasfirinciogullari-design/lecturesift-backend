"""Privacy-safe daily Instagram publishing for LectureSift."""

from __future__ import annotations

import io
import sys
import time
from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from PIL import Image, ImageDraw, ImageFont

from .config import (
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_ACCOUNT_ID,
    INSTAGRAM_APP_SECRET,
    INSTAGRAM_DAILY_AUTOMATION_ENABLED,
    INSTAGRAM_GRAPH_API_VERSION,
    PUBLIC_BASE_URL,
)
from .instagram import InstagramAPIError, InstagramClient, InstagramConfigurationError


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


def is_already_published(client: InstagramClient, day: date) -> bool:
    marker = f"#LectureSiftGununNotu{day:%Y%m%d}"
    return any(marker in (item.get("caption") or "") for item in client.get_recent_media().get("data", []))


def _wait_until_ready(client: InstagramClient, container_id: str) -> None:
    for _ in range(12):
        status = client.get_container_status(container_id).get("status_code")
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise InstagramAPIError("Instagram media processing failed")
        time.sleep(5)
    raise InstagramAPIError("Instagram media processing timed out")


def publish_daily_post(day: date | None = None) -> dict:
    if not INSTAGRAM_DAILY_AUTOMATION_ENABLED:
        return {"status": "disabled"}
    if not PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL must be set for daily Instagram automation")
    selected_day = day or date.today()
    client = InstagramClient(
        access_token=INSTAGRAM_ACCESS_TOKEN,
        account_id=INSTAGRAM_ACCOUNT_ID,
        app_secret=INSTAGRAM_APP_SECRET,
        api_version=INSTAGRAM_GRAPH_API_VERSION,
    )
    if is_already_published(client, selected_day):
        return {"status": "already_published", "date": selected_day.isoformat()}
    tip = daily_tip(selected_day)
    media_url = f"{PUBLIC_BASE_URL}/instagram/daily/image/{selected_day.isoformat()}.jpg"
    container = client.create_media_container(media_url=media_url, caption=tip.caption)
    _wait_until_ready(client, container["id"])
    published = client.publish_media(container["id"])
    return {"status": "published", "date": selected_day.isoformat(), "media_id": published.get("id")}


def main() -> int:
    try:
        result = publish_daily_post()
    except (InstagramAPIError, InstagramConfigurationError, RuntimeError, KeyError) as exc:
        print(f"Daily Instagram post failed: {exc}", file=sys.stderr)
        return 1
    print(result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
