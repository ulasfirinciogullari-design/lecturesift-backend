"""Studio-quality Reels and carousel images for the official LectureSift Instagram account.

A Reel is a 1080x1920, 30 fps product demo: hook, the study problem, LectureSift turning a
lecture into a study pack, a timed quiz question, and a call to action. Narration comes from
ElevenLabs with word timings, and the captions highlight each word as it is spoken. Frames are
drawn with Pillow and piped straight into FFmpeg, so no browser is needed on the server.

This module only renders. It never publishes: the existing Instagram publisher stays in charge
of what goes out and when.

  python -m lecturesift.reel_studio preview OUT_DIR    render the sample Reel, cover and carousel
"""

from __future__ import annotations

import base64
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FPS = 30
_ASSETS = Path(__file__).resolve().parent / "assets" / "instagram"
_FONTS = _ASSETS / "fonts"

# Brand colours from frontend/favicon.svg and the site palette.
BLUE = (39, 166, 237)
VIOLET = (128, 97, 239)
NAVY = (9, 18, 40)
INK = (16, 33, 66)
YELLOW = (246, 200, 95)
GREEN = (55, 189, 133)
WHITE = (255, 255, 255)
MUTED = (166, 184, 214)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
QUIZ_PAUSE = 1.6  # seconds of thinking time between the question and the answer
SEGMENT_GAP = 0.18


class StudioError(RuntimeError):
    pass


# ---------------------------------------------------------------- content

SAMPLE = {
    "id": "sample-biology-mitosis",
    "source": "Biyoloji 252 · Mitoz",
    "file": "biyoloji-ders-kaydi.mp4",
    "hook": ["Biyoloji notlarını", "3. kez mi okuyorsun?"],
    "hook_highlight": "3. kez",
    "problem": "Tekrar okumak ≠ hatırlamak",
    "question": "Mitozda kromozomlar hücrenin ortasında hangi evrede dizilir?",
    "options": ["Profaz", "Metafaz", "Anafaz", "Telofaz"],
    "answer": 1,
    "cta": "Dersini yükle, çalışma paketin hazır.",
    # Spoken lines, one per scene. "say" is what the voice reads when it differs from the caption.
    "voice": [
        {"scene": "hook", "text": "Biyoloji notlarını üçüncü kez mi okuyorsun? Dur."},
        {"scene": "problem", "text": "Tekrar okumak, hatırladığını sanmanı sağlar. Ama sınavda sana soru sorulur."},
        {"scene": "product", "text": "Ders kaydını ya da PDF'ini LectureSift'e yükle. Notlar, özet, test ve kartlar tek pakette hazır."},
        {"scene": "quiz", "text": "Hadi dene. Mitozda kromozomlar hücrenin ortasında hangi evrede dizilir?"},
        {"scene": "answer", "text": "Cevap: metafaz."},
        {"scene": "cta", "text": "Bütün dersi değil, unuttuğun yeri tekrar et. LectureSift'i ücretsiz dene."},
    ],
    "caption": (
        "Aynı notu üçüncü kez okumak yerine kendine bir soru sor. 🧠\n"
        "LectureSift ders kaydını, PDF'ini ya da slaytlarını özet, quiz ve flashcard'lara çevirir.\n"
        "Sen kaç saniyede cevapladın? Yoruma yaz 👇\n\n"
        "Voice: AI-generated.\n"
        "#LectureSift #ÜniversiteHayatı #SınavHazırlık #AktifHatırlama #Biyoloji"
    ),
}


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Narration:
    audio: bytes | None  # mp3 of the whole narration, None for a silent layout preview
    words: tuple[Word, ...]
    scenes: dict[str, tuple[float, float]]
    duration: float


# ---------------------------------------------------------------- voice

def _estimate_words(text: str, start: float) -> tuple[list[Word], float]:
    """Word timings at a natural Turkish narration pace, for previews without a voice key."""
    words, t = [], start
    for token in text.split():
        length = 0.1 + 0.05 * len(token.strip(".,:;?!"))
        words.append(Word(token, t, t + length))
        t += length + (0.32 if token[-1] in ".?!" else 0.16 if token[-1] in ",:;" else 0.03)
    return words, t


def _words_from_alignment(text: str, alignment: dict, offset: float) -> list[Word]:
    """Start/end of each space-separated word from ElevenLabs character timings."""
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    words, current, w_start, w_end = [], "", None, 0.0
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if current:
                words.append(Word(current, offset + w_start, offset + w_end))
            current, w_start = "", None
            continue
        if w_start is None:
            w_start = s
        current += ch
        w_end = e
    if current:
        words.append(Word(current, offset + w_start, offset + w_end))
    if [w.text for w in words] != text.split():
        raise StudioError("ElevenLabs alignment does not match the narration text")
    return words


def _elevenlabs(text: str, previous: str, following: str) -> tuple[bytes, dict]:
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise StudioError("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID are required for narration")
    body = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "language_code": "tr",
        "previous_text": previous,
        "next_text": following,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True},
    }
    request = Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/with-timestamps?output_format=mp3_44100_128",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise StudioError(f"ElevenLabs request failed with HTTP {exc.code}") from None
    except (URLError, TimeoutError, ValueError):
        raise StudioError("ElevenLabs is temporarily unavailable") from None
    alignment = data.get("alignment") or data.get("normalized_alignment")
    if not data.get("audio_base64") or not alignment:
        raise StudioError("ElevenLabs returned no audio or timings")
    return base64.b64decode(data["audio_base64"]), alignment


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def narrate(row: dict, *, voice: bool = True) -> Narration:
    """One ElevenLabs request per line (cheap retries, exact scene boundaries), joined on one timeline."""
    lines = row["voice"]
    words: list[Word] = []
    scenes: dict[str, tuple[float, float]] = {}
    t = 0.25
    with tempfile.TemporaryDirectory(prefix="lecturesift-studio-voice-") as folder:
        clips: list[tuple[Path, float]] = []
        for index, line in enumerate(lines):
            text = line["text"]
            if voice:
                previous = lines[index - 1]["text"] if index else ""
                following = lines[index + 1]["text"] if index + 1 < len(lines) else ""
                audio, alignment = _elevenlabs(line.get("say", text), previous, following)
                clip = Path(folder) / f"{index:02d}.mp3"
                clip.write_bytes(audio)
                line_words = _words_from_alignment(line.get("say", text), alignment, t)
                if "say" in line:  # captions show the written form, timed by the spoken one
                    shown = text.split()
                    if len(shown) != len(line_words):
                        raise StudioError("A 'say' override must keep the same number of words")
                    line_words = [Word(s, w.start, w.end) for s, w in zip(shown, line_words)]
                clips.append((clip, t))
                end = t + _probe_seconds(clip)
            else:
                line_words, end = _estimate_words(text, t)
            words += line_words
            scenes[line["scene"]] = (t, end)
            t = end + (QUIZ_PAUSE if line["scene"] == "quiz" else SEGMENT_GAP)
        duration = t + 1.2
        audio = _mix_voice(clips, duration, Path(folder)) if voice else None
    return Narration(audio, tuple(words), scenes, duration)


def _mix_voice(clips: list[tuple[Path, float]], duration: float, folder: Path) -> bytes:
    out = folder / "voice.wav"
    inputs, filters = [], []
    for index, (clip, start) in enumerate(clips):
        inputs += ["-i", str(clip)]
        delay = int(start * 1000)
        filters.append(f"[{index}:a]aresample=48000,adelay={delay}|{delay}[v{index}]")
    labels = "".join(f"[v{i}]" for i in range(len(clips)))
    filters.append(f"{labels}amix=inputs={len(clips)}:normalize=0,apad,atrim=0:{duration:.3f}[out]")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
         "-filter_complex", ";".join(filters), "-map", "[out]", "-ac", "2", str(out)],
        check=True,
    )
    return out.read_bytes()


# ---------------------------------------------------------------- drawing helpers

@lru_cache(maxsize=64)
def font(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    files = {
        "medium": "Inter-Medium.otf", "semibold": "Inter-SemiBold.otf", "bold": "Inter-Bold.otf",
        "display": "InterDisplay-ExtraBold.otf", "black": "InterDisplay-Black.otf",
    }
    return ImageFont.truetype(str(_FONTS / files[weight]), size)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def ease_out(x: float) -> float:
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_back(x: float) -> float:
    x = clamp(x)
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def ramp(t: float, start: float, length: float = 0.45) -> float:
    return clamp((t - start) / length)


def mix(a: tuple, b: tuple, k: float) -> tuple:
    return tuple(int(round(x + (y - x) * k)) for x, y in zip(a, b))


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=f) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@lru_cache(maxsize=1)
def background() -> Image.Image:
    """A slightly oversized navy field with soft brand-colour glows; frames crop a drifting window."""
    big = Image.new("RGB", (W + 240, H + 240), NAVY)
    glow = Image.new("RGB", big.size, (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-260, -200, 760, 820), fill=(22, 92, 150))
    gd.ellipse((560, 1100, 1560, 2160), fill=(70, 50, 150))
    gd.ellipse((380, 520, 1100, 1240), fill=(18, 40, 92))
    glow = glow.filter(ImageFilter.GaussianBlur(190))
    return Image.blend(big, glow, 0.85)


@lru_cache(maxsize=16)
def shadow(width: int, height: int, radius: int, blur: int = 38, alpha: int = 120) -> Image.Image:
    pad = blur * 3
    layer = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((pad, pad + 18, pad + width, pad + height + 18), radius, fill=(0, 0, 0, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _paste(frame: Image.Image, layer: Image.Image, pos: tuple[int, int]) -> None:
    frame.paste(layer, (int(pos[0]), int(pos[1])), layer)


def card(frame: Image.Image, box: tuple[int, int, int, int], radius: int = 40, fill=WHITE, alpha: float = 1.0) -> ImageDraw.ImageDraw:
    x0, y0, x1, y1 = box
    sh = shadow(x1 - x0, y1 - y0, radius)
    pad = (sh.width - (x1 - x0)) // 2
    if alpha < 1:
        sh = sh.copy()
        sh.putalpha(sh.getchannel("A").point(lambda a: int(a * alpha)))
    _paste(frame, sh, (x0 - pad, y0 - pad))
    draw = ImageDraw.Draw(frame, "RGBA")
    draw.rounded_rectangle(box, radius, fill=(*fill, int(255 * alpha)))
    return draw


def logo(frame: Image.Image, x: int, y: int, size: int) -> None:
    """The favicon mark: rounded gradient square with three white bars."""
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    gp = grad.load()
    for i in range(size):
        for j in range(size):
            gp[i, j] = (*mix(BLUE, VIOLET, (i + j) / (2 * size)), 255)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), int(size * 0.28), fill=255)
    tile.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(tile)
    s = size / 64
    for bx, by, bh in ((15, 31, 18), (28, 14, 35), (41, 23, 26)):
        d.rounded_rectangle((bx * s, by * s, (bx + 8) * s, (by + bh) * s), 3 * s, fill=WHITE)
    _paste(frame, tile, (x, y))


@lru_cache(maxsize=4)
def _logo_tile(size: int) -> Image.Image:
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    logo(tile, 0, 0, size)
    return tile


def brand_chip(frame: Image.Image, alpha: float = 1.0) -> None:
    tile = _logo_tile(64)
    if alpha < 1:
        tile = tile.copy()
        tile.putalpha(tile.getchannel("A").point(lambda a: int(a * alpha)))
    _paste(frame, tile, (72, 170))
    ImageDraw.Draw(frame, "RGBA").text((152, 178), "LectureSift", font=font(40, "bold"), fill=(*WHITE, int(255 * alpha)))


def check(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, k: float, color=GREEN) -> None:
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    if k <= 0:
        return
    p1, p2, p3 = (cx - r * 0.45, cy + r * 0.02), (cx - r * 0.1, cy + r * 0.38), (cx + r * 0.5, cy - r * 0.32)
    first = clamp(k / 0.4)
    draw.line([p1, (p1[0] + (p2[0] - p1[0]) * first, p1[1] + (p2[1] - p1[1]) * first)], fill=WHITE, width=max(3, r // 4))
    if k > 0.4:
        second = clamp((k - 0.4) / 0.6)
        draw.line([p2, (p2[0] + (p3[0] - p2[0]) * second, p2[1] + (p3[1] - p2[1]) * second)], fill=WHITE, width=max(3, r // 4))


# ---------------------------------------------------------------- scenes

def scene_hook(frame: Image.Image, row: dict, t: float, start: float, end: float) -> None:
    draw = ImageDraw.Draw(frame, "RGBA")
    f = font(104, "black")
    y = 640
    hi = row.get("hook_highlight", "")
    for li, line in enumerate(row["hook"]):
        k = ease_back(ramp(t, start + li * 0.35, 0.5))
        if k <= 0:
            continue
        lift = int((1 - k) * 90)
        a = int(255 * clamp(k))
        lines = wrap(draw, line, f, W - 160)
        for text in lines:
            x = 80
            if hi and hi in text:
                before, after = text.split(hi, 1)
                draw.text((x, y + lift), before, font=f, fill=(*WHITE, a))
                x += int(draw.textlength(before, font=f)) + (16 if before else 0)
                wbox = draw.textbbox((x, y + lift), hi, font=f)
                draw.rounded_rectangle((wbox[0] - 14, wbox[1] - 10, wbox[2] + 14, wbox[3] + 16), 22, fill=(*YELLOW, a))
                draw.text((x, y + lift), hi, font=f, fill=(*NAVY, a))
                x += int(draw.textlength(hi, font=f)) + 16
                draw.text((x, y + lift), after, font=f, fill=(*WHITE, a))
            else:
                draw.text((x, y + lift), text, font=f, fill=(*WHITE, a))
            y += 128
    k = ease_out(ramp(t, start + 1.0, 0.5))
    if k > 0:
        draw.text((82, y + 40), "Bunu dene ↓", font=font(46, "semibold"), fill=(*MUTED, int(255 * k)))


def scene_problem(frame: Image.Image, row: dict, t: float, start: float, end: float) -> None:
    draw = ImageDraw.Draw(frame, "RGBA")
    k0 = ease_out(ramp(t, start, 0.5))
    draw.text((80, 330), row["source"].upper(), font=font(34, "bold"), fill=(*BLUE, int(255 * k0)))
    # Three fanned pages, the top one gets highlighter swipes over and over.
    for index, angle in enumerate((-9, 6, 0)):
        k = ease_back(ramp(t, start + index * 0.15, 0.55))
        if k <= 0:
            continue
        page = Image.new("RGBA", (560, 720), (0, 0, 0, 0))
        pd = ImageDraw.Draw(page)
        pd.rounded_rectangle((0, 0, 559, 719), 34, fill=(248, 250, 255))
        pd.rounded_rectangle((44, 52, 330, 80), 12, fill=(196, 208, 230))
        for li in range(11):
            yy = 128 + li * 48
            width = (440, 470, 380, 455, 410, 470, 300, 440, 460, 390, 420)[li]
            if index == 2:
                swipe = ramp(t, start + 0.8 + li * 0.18, 0.3)
                if swipe > 0 and li % 3 != 2:
                    pd.rounded_rectangle((38, yy - 10, 38 + int((width + 12) * swipe), yy + 22), 8, fill=(255, 226, 120))
            pd.rounded_rectangle((44, yy, 44 + width, yy + 12), 6, fill=(150, 165, 192))
        page = page.rotate(angle, resample=Image.BICUBIC, expand=True)
        sh = shadow(560, 720, 34, 30, 110)
        cx, cy = 540 + (index - 1) * 40, 900 + int((1 - k) * 260)
        _paste(frame, sh, (cx - sh.width // 2, cy - sh.height // 2))
        _paste(frame, page, (cx - page.width // 2, cy - page.height // 2))
    k = ease_back(ramp(t, start + 1.4, 0.5))
    if k > 0:
        text = row["problem"]
        f = font(64, "display")
        tw = draw.textlength(text, font=f)
        x, y = (W - tw) / 2, 1330 - int((1 - k) * 40)
        draw.rounded_rectangle((x - 34, y - 22, x + tw + 34, y + 92), 30, fill=(*INK, int(240 * clamp(k))))
        draw.text((x, y), text, font=f, fill=(*YELLOW, int(255 * clamp(k))))


_STEPS = ("Transkript", "Akıllı notlar", "Özet", "Quiz · 10 soru", "Flashcard · 20 kart")


def scene_product(frame: Image.Image, row: dict, t: float, start: float, end: float) -> None:
    k = ease_out(ramp(t, start, 0.55))
    top = 330 + int((1 - k) * 700)
    box = (70, top, W - 70, top + 900)
    draw = card(frame, box, 48, fill=(247, 249, 254), alpha=clamp(k * 1.4))
    x0, y0 = box[0] + 56, box[1] + 52
    # Upload row
    draw.rounded_rectangle((x0, y0, box[2] - 56, y0 + 132), 28, fill=WHITE, outline=(222, 229, 242), width=2)
    draw.rounded_rectangle((x0 + 26, y0 + 26, x0 + 106, y0 + 106), 20, fill=(229, 64, 104))
    draw.polygon([(x0 + 54, y0 + 46), (x0 + 54, y0 + 86), (x0 + 86, y0 + 66)], fill=WHITE)
    draw.text((x0 + 134, y0 + 28), row["file"], font=font(34, "bold"), fill=INK)
    up = ease_out(ramp(t, start + 0.3, 1.1))
    draw.rounded_rectangle((x0 + 134, y0 + 86, box[2] - 96, y0 + 102), 8, fill=(226, 232, 244))
    if up > 0:
        draw.rounded_rectangle((x0 + 134, y0 + 86, x0 + 134 + int((box[2] - 96 - x0 - 134) * up), y0 + 102), 8, fill=BLUE)
    # Processing steps tick one by one across the scene.
    span = max(1.6, (end - start) - 1.6)
    yy = y0 + 196
    draw.text((x0, yy), "Çalışma paketi hazırlanıyor", font=font(30, "semibold"), fill=(94, 110, 140))
    yy += 70
    for index, label in enumerate(_STEPS):
        at = start + 1.2 + index * span / len(_STEPS)
        appear = ease_out(ramp(t, at - 0.35, 0.35))
        done = ramp(t, at, 0.35)
        row_y = yy + index * 116
        if appear <= 0:
            continue
        draw.rounded_rectangle((x0, row_y, box[2] - 56, row_y + 96), 24, fill=mix((247, 249, 254), WHITE, appear), outline=(228, 234, 245), width=2)
        if done > 0:
            check(draw, x0 + 52, row_y + 48, 24, done)
        else:
            spin = (t * 6) % (2 * math.pi)
            draw.arc((x0 + 28, row_y + 24, x0 + 76, row_y + 72), math.degrees(spin), math.degrees(spin) + 260, fill=BLUE, width=6)
        draw.text((x0 + 100, row_y + 26), label, font=font(36, "bold"), fill=INK)
        state = "Hazır" if done >= 1 else "…"
        tw = draw.textlength(state, font=font(30, "semibold"))
        draw.text((box[2] - 86 - tw, row_y + 31), state, font=font(30, "semibold"), fill=GREEN if done >= 1 else MUTED)


def scene_quiz(frame: Image.Image, row: dict, t: float, start: float, end: float, reveal: float) -> None:
    k = ease_back(ramp(t, start, 0.5))
    top = 300 + int((1 - k) * 120)
    box = (70, top, W - 70, top + 930)
    draw = card(frame, box, 48, fill=WHITE, alpha=clamp(k * 1.5))
    x0 = box[0] + 56
    draw.rounded_rectangle((x0, top + 52, x0 + 230, top + 106), 27, fill=(237, 233, 255))
    draw.text((x0 + 26, top + 60), "SORU 1/10", font=font(30, "bold"), fill=VIOLET)
    # Countdown ring from the end of the question to the reveal.
    cx, cy, r = box[2] - 110, top + 80, 46
    left = clamp((reveal - t) / QUIZ_PAUSE) if t >= reveal - QUIZ_PAUSE else 1.0
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(232, 236, 245), width=10)
    if t < reveal:
        draw.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * left, fill=VIOLET, width=10)
        n = str(max(1, math.ceil(left * 3))) if t >= reveal - QUIZ_PAUSE else "?"
        tw = draw.textlength(n, font=font(40, "black"))
        draw.text((cx - tw / 2, cy - 26), n, font=font(40, "black"), fill=INK)
    else:
        check(draw, cx, cy, r, ramp(t, reveal, 0.35))
    f = font(52, "display")
    yy = top + 150
    for line in wrap(draw, row["question"], f, box[2] - x0 - 56):
        draw.text((x0, yy), line, font=f, fill=INK)
        yy += 68
    yy += 40
    shown = ramp(t, reveal, 0.4)
    for index, option in enumerate(row["options"]):
        appear = ease_out(ramp(t, start + 0.5 + index * 0.18, 0.35))
        if appear <= 0:
            continue
        correct = index == row["answer"]
        oy = yy + index * 132 + int((1 - appear) * 30)
        fill, outline, ink = (246, 248, 253), (226, 232, 244), INK
        if shown > 0 and correct:
            fill, outline, ink = mix(fill, (226, 248, 237), shown), mix(outline, GREEN, shown), mix(INK, (18, 120, 80), shown)
        elif shown > 0:
            ink = mix(INK, (176, 186, 204), shown)
        draw.rounded_rectangle((x0, oy, box[2] - 56, oy + 108), 26, fill=fill, outline=outline, width=4)
        letter = "ABCD"[index]
        draw.rounded_rectangle((x0 + 22, oy + 22, x0 + 86, oy + 86), 18, fill=(GREEN if shown > 0 and correct else (230, 235, 246)))
        lw = draw.textlength(letter, font=font(34, "black"))
        draw.text((x0 + 54 - lw / 2, oy + 32), letter, font=font(34, "black"), fill=WHITE if shown > 0 and correct else INK)
        draw.text((x0 + 116, oy + 30), option, font=font(42, "bold"), fill=ink)


def scene_cta(frame: Image.Image, row: dict, t: float, start: float, end: float) -> None:
    draw = ImageDraw.Draw(frame, "RGBA")
    k = ease_back(ramp(t, start, 0.6))
    size = int(210 * clamp(k, 0.01, 1.2))
    if size > 4:
        tile = _logo_tile(210).resize((size, size), Image.LANCZOS)
        _paste(frame, tile, ((W - size) // 2, 520 + (210 - size) // 2))
    k2 = ease_out(ramp(t, start + 0.3, 0.5))
    a = int(255 * k2)
    for text, f, y, color in (
        ("LectureSift", font(96, "black"), 790, WHITE),
        (row["cta"], font(46, "semibold"), 920, MUTED),
    ):
        lines = wrap(draw, text, f, W - 180)
        for line in lines:
            tw = draw.textlength(line, font=f)
            draw.text(((W - tw) / 2, y + int((1 - k2) * 30)), line, font=f, fill=(*color, a))
            y += int(f.size * 1.25)
    k3 = ease_back(ramp(t, start + 0.7, 0.5))
    if k3 > 0:
        label = "lecturesift.com"
        f = font(52, "bold")
        tw = draw.textlength(label, font=f)
        x, y = (W - tw) / 2, 1110
        grad = Image.new("RGBA", (int(tw + 96), 110))
        gp = grad.load()
        for i in range(grad.width):
            c = mix(BLUE, VIOLET, i / grad.width)
            for j in range(grad.height):
                gp[i, j] = (*c, int(255 * clamp(k3)))
        mask = Image.new("L", grad.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, grad.width - 1, grad.height - 1), 55, fill=255)
        pill = Image.new("RGBA", grad.size, (0, 0, 0, 0))
        pill.paste(grad, (0, 0), mask)
        _paste(frame, pill, (int(x - 48), int(y - 22)))
        draw.text((x, y), label, font=f, fill=(*WHITE, int(255 * clamp(k3))))
        note = "30 dakika ücretsiz · kart gerekmez"
        nf = font(36, "medium")
        nw = draw.textlength(note, font=nf)
        draw.text(((W - nw) / 2, 1250), note, font=nf, fill=(*MUTED, int(255 * clamp(k3))))


# ---------------------------------------------------------------- captions

CAPTION_SIZE = 62
CAPTION_WIDTH = W - 200


def caption_groups(words: tuple[Word, ...], max_words: int = 4) -> list[tuple[Word, ...]]:
    """Short caption phrases that fit one line: break on punctuation, width or max_words."""
    f = font(CAPTION_SIZE, "black")
    groups, current = [], []
    for word in words:
        if current and f.getlength(" ".join(w.text for w in (*current, word))) > CAPTION_WIDTH:
            groups.append(tuple(current))
            current = []
        current.append(word)
        if len(current) >= max_words or word.text[-1] in ".?!:;" or (word.text[-1] == "," and len(current) > 1):
            groups.append(tuple(current))
            current = []
    if current:
        groups.append(tuple(current))
    return groups


def draw_captions(frame: Image.Image, groups: list[tuple[Word, ...]], t: float) -> None:
    active = next((g for g in groups if g[0].start - 0.05 <= t <= g[-1].end + 0.25), None)
    if not active:
        return
    draw = ImageDraw.Draw(frame, "RGBA")
    f = font(CAPTION_SIZE, "black")
    texts = [w.text for w in active]
    space = draw.textlength(" ", font=f)
    widths = [draw.textlength(x, font=f) for x in texts]
    total = sum(widths) + space * (len(texts) - 1)
    pop = ease_back(ramp(t, active[0].start - 0.05, 0.22))
    x = (W - total) / 2
    y = 1540 + int((1 - pop) * 24)
    draw.rounded_rectangle((x - 30, y - 18, x + total + 30, y + 92), 28, fill=(6, 12, 28, int(190 * clamp(pop))))
    for word, width in zip(active, widths):
        speaking = word.start - 0.03 <= t <= word.end + 0.05
        said = t > word.end
        color = YELLOW if speaking else WHITE if said or t >= active[0].start else MUTED
        draw.text((x, y), word.text, font=f, fill=(*color, int(255 * clamp(pop))))
        x += width + space


# ---------------------------------------------------------------- frames and video

def _scene_at(scenes: dict, t: float, duration: float) -> tuple[str, float, float]:
    order = ["hook", "problem", "product", "quiz", "cta"]
    starts = {name: scenes[name][0] for name in order}
    starts["quiz"] = scenes["quiz"][0]
    bounds = []
    for index, name in enumerate(order):
        start = 0.0 if index == 0 else starts[name] - 0.15
        end = starts[order[index + 1]] - 0.15 if index + 1 < len(order) else duration
        bounds.append((name, start, end))
    for name, start, end in bounds:
        if start <= t < end:
            return name, start, end
    return bounds[-1]


def render_frame(row: dict, narration: Narration, t: float, groups=None) -> Image.Image:
    drift = 60 + 60 * math.sin(t * 0.35)
    base = background().crop((int(drift), int(120 + 50 * math.cos(t * 0.27)), int(drift) + W, int(120 + 50 * math.cos(t * 0.27)) + H))
    frame = base.copy()
    name, start, end = _scene_at(narration.scenes, t, narration.duration)
    reveal = narration.scenes["answer"][0]
    if name == "hook":
        scene_hook(frame, row, t, narration.scenes["hook"][0], end)
    elif name == "problem":
        scene_problem(frame, row, t, start + 0.15, end)
    elif name == "product":
        scene_product(frame, row, t, start + 0.15, end)
    elif name == "quiz":
        scene_quiz(frame, row, t, start + 0.15, end, reveal)
    else:
        scene_cta(frame, row, t, start + 0.15, end)
    # Short white flash on every cut keeps the pace without a jarring jump.
    since = t - start
    if 0 < since < 0.12 and name != "hook":
        ImageDraw.Draw(frame, "RGBA").rectangle((0, 0, W, H), fill=(255, 255, 255, int(70 * (1 - since / 0.12))))
    if name != "cta":
        brand_chip(frame)
    # Thin progress bar along the top safe edge.
    d = ImageDraw.Draw(frame, "RGBA")
    d.rounded_rectangle((72, 120, W - 72, 128), 4, fill=(255, 255, 255, 40))
    d.rounded_rectangle((72, 120, 72 + int((W - 144) * clamp(t / narration.duration)), 128), 4, fill=(*BLUE, 255))
    draw_captions(frame, groups if groups is not None else caption_groups(narration.words), t)
    return frame.convert("RGB")


def render_reel(row: dict, narration: Narration, out: Path) -> Path:
    """Stream frames into FFmpeg with the narration over a quiet music bed."""
    groups = caption_groups(narration.words)
    frames = math.ceil(narration.duration * FPS)
    with tempfile.TemporaryDirectory(prefix="lecturesift-studio-") as folder:
        audio_args, audio_filter = [], []
        music = _ASSETS / "music-bed.m4a"
        if narration.audio:
            voice = Path(folder) / "voice.wav"
            voice.write_bytes(narration.audio)
            audio_args = ["-i", str(voice), "-stream_loop", "-1", "-i", str(music)]
            audio_filter = [
                "-filter_complex",
                "[2:a]volume=0.12,afade=t=out:st={0:.2f}:d=1.2[bed];[1:a]volume=1.0[v];"
                "[v][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]".format(narration.duration - 1.3),
                "-map", "0:v", "-map", "[a]",
            ]
        else:
            audio_args = ["-stream_loop", "-1", "-i", str(music)]
            audio_filter = ["-map", "0:v", "-map", "1:a", "-af", f"volume=0.35,afade=t=out:st={narration.duration - 1.3:.2f}:d=1.2"]
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            *audio_args, *audio_filter,
            "-t", f"{narration.duration:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            for index in range(frames):
                process.stdin.write(render_frame(row, narration, index / FPS, groups).tobytes())
        finally:
            process.stdin.close()
        if process.wait() != 0:
            raise StudioError("FFmpeg could not encode the Reel")
    return out


def render_cover(row: dict, narration: Narration) -> Image.Image:
    """The hook frame once every line has landed, without captions."""
    t = narration.scenes["hook"][0] + 1.6
    frame = render_frame(row, Narration(None, (), narration.scenes, narration.duration), t, groups=[])
    return frame


# ---------------------------------------------------------------- carousel images (4:5)

CW, CH = 1080, 1350


def _slide_base(index: int, total: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    frame = background().crop((80, 300, 80 + CW, 300 + CH))
    _paste(frame, _logo_tile(56), (72, 72))
    d = ImageDraw.Draw(frame, "RGBA")
    d.text((144, 80), "LectureSift", font=font(36, "bold"), fill=WHITE)
    label = f"{index}/{total}"
    tw = d.textlength(label, font=font(30, "semibold"))
    d.rounded_rectangle((CW - 72 - tw - 36, 74, CW - 72, 124), 25, fill=(255, 255, 255, 30))
    d.text((CW - 72 - tw - 18, 82), label, font=font(30, "semibold"), fill=MUTED)
    return frame, d


def _swipe_hint(d: ImageDraw.ImageDraw, text: str = "Kaydır →") -> None:
    tw = d.textlength(text, font=font(34, "bold"))
    d.text((CW - 72 - tw, CH - 110), text, font=font(34, "bold"), fill=BLUE)


def render_carousel(row: dict) -> list[Image.Image]:
    total = 5
    slides = []

    frame, d = _slide_base(1, total)
    y = 380
    for line in row["hook"]:
        f = font(100, "black")
        for text in wrap(d, line, f, CW - 150):
            hi = row.get("hook_highlight", "")
            x = 72
            if hi and hi in text:
                before, after = text.split(hi, 1)
                d.text((x, y), before, font=f, fill=WHITE)
                x += int(d.textlength(before, font=f)) + (16 if before else 0)
                box = d.textbbox((x, y), hi, font=f)
                d.rounded_rectangle((box[0] - 14, box[1] - 10, box[2] + 14, box[3] + 16), 22, fill=YELLOW)
                d.text((x, y), hi, font=f, fill=NAVY)
                x += int(d.textlength(hi, font=f)) + 16
                d.text((x, y), after, font=f, fill=WHITE)
            else:
                d.text((x, y), text, font=f, fill=WHITE)
            y += 124
    d.text((74, y + 40), "Önce bu 30 saniyelik testi yap.", font=font(44, "semibold"), fill=MUTED)
    _swipe_hint(d)
    slides.append(frame)

    frame, d = _slide_base(2, total)
    d.text((72, 260), "NEDEN İŞE YARAMIYOR?", font=font(34, "bold"), fill=BLUE)
    y = 340
    for line in wrap(d, row["problem"], font(84, "black"), CW - 150):
        d.text((72, y), line, font=font(84, "black"), fill=YELLOW)
        y += 104
    y += 30
    for text in ("Notu tanımak, onu hatırlamak değildir.", "Sınav sana soru sorar, notunu göstermez.", "Kendini test etmek eksikleri hemen gösterir."):
        check(d, 100, y + 30, 22, 1.0, BLUE)
        for li, line in enumerate(wrap(d, text, font(42, "semibold"), CW - 230)):
            d.text((146, y + li * 54), line, font=font(42, "semibold"), fill=WHITE)
        y += 150
    _swipe_hint(d)
    slides.append(frame)

    frame, d = _slide_base(3, total)
    d = card(frame, (72, 220, CW - 72, CH - 170), 44)
    d.rounded_rectangle((124, 270, 354, 324), 27, fill=(237, 233, 255))
    d.text((150, 278), "SORU 1/10", font=font(30, "bold"), fill=VIOLET)
    y = 370
    for line in wrap(d, row["question"], font(54, "display"), CW - 260):
        d.text((124, y), line, font=font(54, "display"), fill=INK)
        y += 70
    y += 36
    for index, option in enumerate(row["options"]):
        d.rounded_rectangle((124, y, CW - 124, y + 104), 26, fill=(246, 248, 253), outline=(226, 232, 244), width=4)
        d.rounded_rectangle((146, y + 20, 210, y + 84), 18, fill=(230, 235, 246))
        lw = d.textlength("ABCD"[index], font=font(34, "black"))
        d.text((178 - lw / 2, y + 30), "ABCD"[index], font=font(34, "black"), fill=INK)
        d.text((240, y + 28), option, font=font(42, "bold"), fill=INK)
        y += 124
    d.text((124, CH - 250), "Cevabını aklında tut, sonra kaydır.", font=font(34, "semibold"), fill=(94, 110, 140))
    _swipe_hint(d)
    slides.append(frame)

    frame, d = _slide_base(4, total)
    d.text((72, 260), "CEVAP", font=font(34, "bold"), fill=GREEN)
    answer = row["options"][row["answer"]]
    d.text((72, 320), answer, font=font(130, "black"), fill=WHITE)
    check(d, CW - 160, 400, 64, 1.0)
    y = 540
    d.text((72, y), "Her ders için bu 4 adım:", font=font(42, "bold"), fill=MUTED)
    y += 90
    for number, text in enumerate(("Dersi ya da PDF'i yükle", "Özet ve notları gözden geçir", "10 soruluk quiz'i çöz", "Bilemediklerini kartlarla tekrar et"), start=1):
        d.rounded_rectangle((72, y, CW - 72, y + 112), 28, fill=(255, 255, 255, 22))
        d.ellipse((100, y + 24, 164, y + 88), fill=BLUE)
        nw = d.textlength(str(number), font=font(36, "black"))
        d.text((132 - nw / 2, y + 34), str(number), font=font(36, "black"), fill=WHITE)
        d.text((192, y + 32), text, font=font(42, "bold"), fill=WHITE)
        y += 136
    _swipe_hint(d)
    slides.append(frame)

    frame, d = _slide_base(5, total)
    _paste(frame, _logo_tile(210).resize((180, 180), Image.LANCZOS), ((CW - 180) // 2, 320))
    for text, f, y, color in (("LectureSift", font(92, "black"), 560, WHITE), (row["cta"], font(46, "semibold"), 690, MUTED)):
        for line in wrap(d, text, f, CW - 180):
            tw = d.textlength(line, font=f)
            d.text(((CW - tw) / 2, y), line, font=f, fill=color)
            y += int(f.size * 1.25)
    label = "lecturesift.com"
    f = font(50, "bold")
    tw = d.textlength(label, font=f)
    d.rounded_rectangle(((CW - tw) / 2 - 48, 880, (CW + tw) / 2 + 48, 986), 53, fill=BLUE)
    d.text(((CW - tw) / 2, 902), label, font=f, fill=WHITE)
    note = "30 dakika ücretsiz · kart gerekmez"
    nw = d.textlength(note, font=font(36, "medium"))
    d.text(((CW - nw) / 2, 1030), note, font=font(36, "medium"), fill=MUTED)
    slides.append(frame)

    return [s.convert("RGB") for s in slides]


# ---------------------------------------------------------------- preview

def preview(out_dir: Path, row: dict | None = None) -> dict:
    """Render the sample Reel, cover and carousel into out_dir. Uses ElevenLabs when its key is set."""
    row = row or SAMPLE
    out_dir.mkdir(parents=True, exist_ok=True)
    voiced = bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID)
    narration = narrate(row, voice=voiced)
    video = render_reel(row, narration, out_dir / "reel.mp4")
    render_cover(row, narration).save(out_dir / "reel-cover.jpg", quality=92)
    for index, slide in enumerate(render_carousel(row), start=1):
        slide.save(out_dir / f"carousel-{index}.jpg", quality=92)
    (out_dir / "caption.txt").write_text(row["caption"], encoding="utf-8")
    return {"video": str(video), "seconds": round(narration.duration, 2), "voiced": voiced}


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "preview":
        print(json.dumps(preview(Path(argv[1])), ensure_ascii=False))
        return 0
    print("Usage: python -m lecturesift.reel_studio preview OUT_DIR", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
