import html
import ipaddress
import re
import socket
import subprocess
import shutil
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yt_dlp

from .config import MAX_VIDEO_BYTES, VIDEO_EXTENSIONS
from .errors import LectureSiftError


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        validate_remote_url(new_url)
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


_URL_OPENER = build_opener(_SafeRedirectHandler())


def _download_direct_media(media_url: str, job_dir: Path) -> Path:
    media_url = validate_remote_url(media_url)
    parsed = urlparse(media_url)
    extension = Path(parsed.path).suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        extension = ".mp4"
    destination = job_dir / f"remote{extension}"
    request = Request(media_url, headers={"User-Agent": "Mozilla/5.0 LectureSift/4.0"})
    total = 0
    with _URL_OPENER.open(request, timeout=45) as response, open(destination, "wb") as stream:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_VIDEO_BYTES:
                destination.unlink(missing_ok=True)
                raise LectureSiftError("LS-UPLOAD-02", "Video izin verilen dosya boyutunu aşıyor.")
            stream.write(chunk)
    if not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError("Remote media download produced an empty file.")
    return destination


def _find_media_in_page(page_url: str) -> str | None:
    page_url = validate_remote_url(page_url)
    request = Request(page_url, headers={"User-Agent": "Mozilla/5.0 LectureSift/4.0"})
    with _URL_OPENER.open(request, timeout=30) as response:
        content_type = (response.headers.get("content-type") or "").lower()
        final_url = response.geturl()
        if any(value in content_type for value in ("video/mp4", "video/webm", "application/octet-stream")):
            return final_url
        raw = response.read(6 * 1024 * 1024)

    page_text = html.unescape(raw.decode("utf-8", errors="ignore"))
    patterns = [
        r'''(?:href|src|content)\s*=\s*["']([^"']+\.(?:mp4|m4v|mov|webm|mkv|mpeg|mpg)(?:\?[^"']*)?)["']''',
        r'''["']([^"']+\.m3u8(?:\?[^"']*)?)["']''',
        r'''https?://[^\s"'<>\\]+?\.(?:mp4|m4v|mov|webm|mkv|mpeg|mpg)(?:\?[^\s"'<>\\]*)?''',
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, page_text, flags=re.I):
            candidate = match.group(1) if match.lastindex else match.group(0)
            found.append(urljoin(final_url, candidate.replace("\\/", "/").strip()))
    found = list(dict.fromkeys(found))
    found.sort(key=lambda item: (".mp4" not in item.lower(), "preview" in item.lower(), len(item)))
    return found[0] if found else None


def download_remote_video(url: str, job_dir: Path) -> Path:
    parsed = urlparse(url)
    if Path(parsed.path).suffix.lower() in VIDEO_EXTENSIONS:
        return _download_direct_media(url, job_dir)

    try:
        media_url = _find_media_in_page(url)
        if media_url:
            media_url = validate_remote_url(media_url)
            if ".m3u8" in media_url.lower():
                destination = job_dir / "remote.mp4"
                run_command(["ffmpeg", "-y", "-rw_timeout", "45000000", "-i", media_url, "-c", "copy", str(destination)])
                if destination.exists() and destination.stat().st_size > MAX_VIDEO_BYTES:
                    destination.unlink(missing_ok=True)
                    raise LectureSiftError("LS-UPLOAD-02", "Video izin verilen dosya boyutunu aşıyor.")
                if destination.exists() and destination.stat().st_size:
                    return destination
            return _download_direct_media(media_url, job_dir)
    except LectureSiftError:
        raise
    except Exception as exc:
        print("PAGE MEDIA DISCOVERY WARNING:", repr(exc), flush=True)

    output_template = str(job_dir / "remote.%(ext)s")
    options = {
        "outtmpl": output_template,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 30,
        "max_filesize": MAX_VIDEO_BYTES,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            requested = info.get("requested_downloads") or []
            candidates = [Path(item["filepath"]) for item in requested if item.get("filepath")]
            prepared = Path(downloader.prepare_filename(info))
            candidates.extend((prepared, prepared.with_suffix(".mp4")))
        existing = [candidate for candidate in candidates if candidate.exists()]
        if not existing:
            existing = list(job_dir.glob("remote.*"))
        if not existing:
            raise RuntimeError("Remote video could not be downloaded.")
        existing.sort(key=lambda item: (item.suffix.lower() != ".mp4", -item.stat().st_size))
        return existing[0]
    except Exception as exc:
        message = str(exc)
        if "429" in message or "not a bot" in message.lower() or "sign in" in message.lower():
            raise RuntimeError("This video provider blocked server-side downloading.") from exc
        raise RuntimeError("No downloadable video could be found at this page.") from exc


def has_audio_stream(video_path: Path) -> bool:
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
    )
    return bool(process.stdout.strip())


def extract_audio_chunks(video_path: Path, job_dir: Path, prefix: str = "audio") -> list[Path]:
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", prefix)
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
            "1200",
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
