import ipaddress
import re
import socket
import subprocess
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

from .config import MAX_VIDEO_BYTES, MEDIA_EXTENSIONS
from .errors import LectureSiftError


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr[-12000:])
    return process


def _is_private_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def validate_remote_url(url: str) -> str:
    cleaned = (url or "").strip()
    try:
        parsed = urlparse(cleaned)
    except Exception as exc:
        raise LectureSiftError("LS-URL-01", "Geçerli bir video bağlantısı gir.", str(exc)) from exc

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LectureSiftError("LS-URL-01", "Bağlantı http:// veya https:// ile başlamalı.")

    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".local") or _is_private_address(host):
        raise LectureSiftError("LS-URL-04", "Yerel veya özel ağ bağlantıları güvenlik nedeniyle desteklenmiyor.")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port)}
    except (socket.gaierror, ValueError) as exc:
        raise LectureSiftError("LS-URL-01", "Bağlantının sunucu adresi bulunamadı.", str(exc)) from exc
    if any(_is_private_address(address) for address in addresses):
        raise LectureSiftError("LS-URL-04", "Yerel veya özel ağ bağlantıları güvenlik nedeniyle desteklenmiyor.")

    return cleaned


def validate_youtube_url(url: str) -> str:
    """Accept one YouTube video and discard playlists, tracking and redirects."""
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"https", "http"} or parsed.username is not None or parsed.password is not None:
            raise ValueError("Invalid scheme or credentials")
        if parsed.port not in {None, 443 if parsed.scheme == "https" else 80}:
            raise ValueError("Invalid port")
        video_id = ""
        if host == "youtu.be":
            video_id = parsed.path.removeprefix("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
            if parsed.path == "/watch" and "nocookie" not in host:
                values = parse_qs(parsed.query).get("v", [])
                video_id = values[0] if len(values) == 1 else ""
            elif re.fullmatch(r"/(?:shorts|live|embed)/[A-Za-z0-9_-]{11}", parsed.path):
                video_id = parsed.path.rsplit("/", 1)[1]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) is None:
            raise ValueError("Not a single YouTube video")
    except (ValueError, TypeError) as exc:
        raise LectureSiftError("LS-URL-05", "Yalnızca geçerli bir YouTube video bağlantısı gir.", status_code=422) from exc
    return validate_remote_url(f"https://www.youtube.com/watch?v={video_id}")


def _remote_download_format(job_type: str, include_slides: bool) -> str:
    """Select only the streams needed by the requested job.

    Download jobs retain the provider's best available video. Transcript and
    audio-export jobs avoid downloading a video stream entirely. Slide-aware
    analysis keeps enough visual detail for OCR while avoiding unnecessarily
    large 1440p/4K transfers.
    """
    normalized_job_type = str(job_type or "study_pack").strip().lower()
    if normalized_job_type == "download_video":
        return "bv*+ba/b"
    if normalized_job_type in {"audio_export", "transcript", "transcription"} or not include_slides:
        return "bestaudio/best"
    return "bestvideo[height<=720]+bestaudio/best[height<=720]/best"


def download_remote_video(
    url: str,
    job_dir: Path,
    *,
    job_type: str = "study_pack",
    include_slides: bool = True,
) -> Path:
    url = validate_youtube_url(url)
    return _download_with_ytdlp(url, job_dir, job_type, include_slides)


def _download_with_ytdlp(url: str, job_dir: Path, job_type: str, include_slides: bool) -> Path:
    output_template = str(job_dir / "remote.%(ext)s")
    options = {
        "outtmpl": output_template,
        "format": _remote_download_format(job_type, include_slides),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 30,
        "max_filesize": MAX_VIDEO_BYTES,
        "js_runtimes": {"node": {}},
        # Solver code is a pinned build dependency, never fetched at job time.
        "remote_components": [],
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            requested = info.get("requested_downloads") or []
            candidates = [Path(item["filepath"]) for item in requested if item.get("filepath")]
            prepared = Path(downloader.prepare_filename(info))
            candidates.extend((prepared, prepared.with_suffix(".mp4")))
        existing = [candidate for candidate in candidates if candidate.is_file() and candidate.suffix.lower() in MEDIA_EXTENSIONS and candidate.stat().st_size > 0]
        if not existing:
            existing = [candidate for candidate in job_dir.glob("remote.*") if candidate.is_file() and candidate.suffix.lower() in MEDIA_EXTENSIONS and candidate.stat().st_size > 0]
        if not existing:
            raise RuntimeError("Remote video could not be downloaded.")
        existing.sort(key=lambda item: (item.suffix.lower() != ".mp4", -item.stat().st_size))
        if existing[0].stat().st_size > MAX_VIDEO_BYTES:
            existing[0].unlink(missing_ok=True)
            raise LectureSiftError("LS-UPLOAD-02", "Video izin verilen dosya boyutunu aşıyor.")
        return existing[0]
    except LectureSiftError:
        raise
    except Exception as exc:
        message = str(exc)
        if "429" in message or "not a bot" in message.lower() or "sign in" in message.lower():
            raise RuntimeError("This video provider blocked server-side downloading.") from exc
        raise RuntimeError("No downloadable video could be found at this page.") from exc


def has_audio_stream(video_path: Path) -> bool:
    try:
        process = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if process.returncode != 0:
            diagnostic = (process.stderr or "").strip()[-2000:]
            raise RuntimeError(f"ffprobe could not inspect the media stream: {diagnostic}")
        return bool(process.stdout.strip())
    except FileNotFoundError:
        # Minimal FFmpeg distributions may omit ffprobe. Mapping the first
        # audio stream to a null output gives the same yes/no answer.
        fallback = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video_path), "-map", "0:a:0", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if fallback.returncode == 0:
            return True
        diagnostic = (fallback.stderr or "").strip()
        lowered = diagnostic.casefold()
        if "matches no streams" in lowered or "does not contain any stream" in lowered:
            return False
        raise RuntimeError(f"ffmpeg could not inspect the media stream: {diagnostic[-2000:]}")


def extract_audio_chunks(
    video_path: Path,
    job_dir: Path,
    prefix: str = "audio",
    segment_seconds: int = 1200,
) -> list[Path]:
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", prefix)
    bounded_segment_seconds = max(60, int(segment_seconds))
    audio_pattern = job_dir / f"{safe_prefix}_%03d.mp3"
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "32k",
            "-f",
            "segment",
            "-segment_time",
            str(bounded_segment_seconds),
            "-reset_timestamps",
            "1",
            str(audio_pattern),
        ]
    )
    chunks = [path for path in sorted(job_dir.glob(f"{safe_prefix}_*.mp3")) if path.stat().st_size > 0]
    if not chunks:
        raise RuntimeError("Audio extraction failed.")
    return chunks


def convert_videos_to_mp3(video_paths: list[Path], job_dir: Path) -> Path:
    parts_dir = job_dir / "audio_export_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for index, video_path in enumerate(video_paths, 1):
        if not has_audio_stream(video_path):
            continue
        part = parts_dir / f"part_{index:03d}.mp3"
        run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vn",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-b:a",
                "128k",
                str(part),
            ]
        )
        if part.exists() and part.stat().st_size:
            parts.append(part)
    if not parts:
        shutil.rmtree(parts_dir, ignore_errors=True)
        raise LectureSiftError("LS-AUDIO-01", "Yüklenen videolarda dönüştürülebilecek bir ses kanalı bulunamadı.")

    destination = job_dir / "LectureSift_Ders_Sesi.mp3"
    if len(parts) == 1:
        shutil.move(str(parts[0]), destination)
    else:
        concat_file = parts_dir / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{str(path.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for path in parts),
            encoding="utf-8",
        )
        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(destination),
            ]
        )
    shutil.rmtree(parts_dir, ignore_errors=True)
    return destination
