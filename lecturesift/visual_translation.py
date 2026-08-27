"""Translate text inside detected slide/screen images and render target-language copies."""

from __future__ import annotations

import base64
import json
import textwrap
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont, ImageStat
from openai import OpenAI

from .config import LANGUAGE_NAMES, OPENAI_API_KEY, VISION_TRANSLATION_MODEL

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:
    arabic_reshaper = None
    get_display = None


_CLIENT = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _safe_json(value: str) -> dict:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned)


def _image_data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.casefold() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_regions(path: Path, target_language: str) -> list[dict]:
    if not _CLIENT:
        return []
    language_name = LANGUAGE_NAMES.get(target_language, target_language)
    prompt = f"""
Inspect this lecture slide or screen image. Translate every meaningful visible
text block into {language_name}. Do not translate logos, URLs, formulas,
standalone variable names, citations, or text already written in the target
language. Preserve numbers and technical terminology.

Return JSON only:
{{
  "same_language": false,
  "regions": [
    {{"box":[x1,y1,x2,y2],"translation":"translated text"}}
  ]
}}
Coordinates use a 0-1000 scale. Keep each box tight around one coherent text
block and return at most 45 regions. If there is no useful text or it is already
in {language_name}, return an empty regions array.
"""
    response = _CLIENT.chat.completions.create(
        model=VISION_TRANSLATION_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _image_data_url(path), "detail": "high"}},
            ],
        }],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    body = _safe_json(response.choices[0].message.content or "{}")
    regions = body.get("regions") or []
    return [item for item in regions if isinstance(item, dict)][:45]


def _font(size: int):
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=max(8, int(size)))
            except OSError:
                continue
    return ImageFont.load_default()


def _display_text(text: str, language: str) -> str:
    value = str(text or "").strip()
    if language == "ar" and arabic_reshaper and get_display:
        try:
            return get_display(arabic_reshaper.reshape(value))
        except Exception:
            return value
    return value


def _box_pixels(box: object, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        values = [max(0.0, min(1000.0, float(item))) for item in box]
    except (TypeError, ValueError):
        return None
    x1, y1, x2, y2 = (
        int(values[0] * width / 1000),
        int(values[1] * height / 1000),
        int(values[2] * width / 1000),
        int(values[3] * height / 1000),
    )
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    pad_x = max(3, int((x2 - x1) * 0.04))
    pad_y = max(2, int((y2 - y1) * 0.08))
    return max(0, x1 - pad_x), max(0, y1 - pad_y), min(width, x2 + pad_x), min(height, y2 + pad_y)


def _background_and_text(image: Image.Image, box: tuple[int, int, int, int]):
    crop = image.crop(box).convert("RGB")
    stat = ImageStat.Stat(crop.resize((1, 1)))
    rgb = tuple(int(value) for value in stat.mean[:3])
    luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return rgb, ((18, 24, 38) if luminance > 145 else (248, 250, 252))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_text(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int]):
    width = max(20, box[2] - box[0] - 10)
    height = max(16, box[3] - box[1] - 8)
    maximum = max(10, min(52, int(height * 0.62)))
    for size in range(maximum, 7, -1):
        font = _font(size)
        lines = _wrap(draw, text, font, width)
        line_height = max(10, draw.textbbox((0, 0), "Ag", font=font)[3] + 2)
        if len(lines) * line_height <= height:
            return font, lines, line_height
    font = _font(8)
    lines = textwrap.wrap(text, width=max(8, int(width / 5))) or [text]
    return font, lines[: max(1, int(height / 10))], 10


def _render_regions(source: Path, destination: Path, regions: list[dict], language: str) -> int:
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    rendered = 0
    for region in regions:
        box = _box_pixels(region.get("box"), width, height)
        text = _display_text(str(region.get("translation") or ""), language)
        if not box or not text:
            continue
        background, foreground = _background_and_text(image, box)
        draw.rounded_rectangle(box, radius=max(2, int((box[3] - box[1]) * 0.08)), fill=background)
        font, lines, line_height = _fit_text(draw, text, box)
        total_height = line_height * len(lines)
        y = box[1] + max(2, (box[3] - box[1] - total_height) // 2)
        for line in lines:
            line_width = draw.textbbox((0, 0), line, font=font)[2]
            x = box[0] + max(4, (box[2] - box[0] - line_width) // 2)
            draw.text((x, y), line, font=font, fill=foreground)
            y += line_height
        rendered += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "JPEG", quality=92, optimize=True)
    return rendered


def translate_slide_images(slides: list[dict], slides_dir: Path, target_language: str, progress: Callable[[float, str], None] | None = None) -> tuple[list[dict], dict]:
    if target_language not in LANGUAGE_NAMES or not slides:
        return slides, {"requested": False, "translated_slides": 0, "translated_regions": 0}
    translated_slides = 0
    translated_regions = 0
    failures = 0
    result: list[dict] = []
    total = len(slides)
    for index, slide in enumerate(slides, 1):
        item = dict(slide)
        source = slides_dir / str(item.get("file") or "")
        if not source.exists():
            result.append(item)
            continue
        if progress:
            progress(100 * (index - 1) / max(1, total), "visual_translation")
        try:
            regions: list[dict] = []
            for attempt in range(2):
                try:
                    regions = _extract_regions(source, target_language)
                    break
                except Exception:
                    if attempt:
                        raise
            if regions:
                translated_name = f"{source.stem}_{target_language}_translated.jpg"
                count = _render_regions(source, slides_dir / translated_name, regions, target_language)
                if count:
                    item["translated_file"] = translated_name
                    item["visual_translation_status"] = "translated"
                    translated_slides += 1
                    translated_regions += count
                else:
                    item["visual_translation_status"] = "no_text"
            else:
                item["visual_translation_status"] = "same_language_or_no_text"
        except Exception:
            failures += 1
            item["visual_translation_status"] = "failed_original_preserved"
        result.append(item)
    if progress:
        progress(100, "visual_translation_done")
    return result, {"requested": True, "target_language": target_language, "translated_slides": translated_slides, "translated_regions": translated_regions, "failures": failures, "originals_preserved": True}
