import re
import subprocess
import shutil
from pathlib import Path


from .errors import LectureSiftError
from .duration import file_duration_seconds


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


def validate_remote_url(url: str) -> str:
    """Retired entry point: no DNS lookup or external media request is allowed."""
    raise LectureSiftError("LS-URL-06", "Bağlantıyla kaynak ekleme kaldırıldı. Dosyanı yükle.", status_code=410)


def download_remote_video(url: str, job_dir: Path, **_options) -> Path:
    # A stale caller must never reactivate removed remote-source functionality.
    validate_remote_url(url)
    raise AssertionError("Retired remote sources cannot return a file")


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
    # MP3 encoder padding can create a 72 ms fragment at an exact segment
    # boundary. Providers reject that as an invalid audio file. Preserve the
    # tail by joining it to its predecessor instead of dropping real speech.
    if len(chunks) > 1 and file_duration_seconds(chunks[-1]) < 1.0:
        previous, tail = chunks[-2:]
        manifest = job_dir / f"{safe_prefix}_join.txt"
        joined = job_dir / f"{safe_prefix}_joined.mp3"
        try:
            # Both basenames come only from the sanitized prefix and numeric
            # segment index. The concat reader never accepts user-authored URLs.
            manifest.write_text(f"file '{previous.name}'\nfile '{tail.name}'\n", encoding="utf-8")
            run_command(["ffmpeg", "-y", "-f", "concat", "-safe", "1", "-i", str(manifest),
                         "-c:a", "copy", str(joined)])
            if not joined.is_file() or joined.stat().st_size == 0:
                raise RuntimeError("Audio tail could not be preserved.")
            joined.replace(previous)
            tail.unlink()
            chunks.pop()
        finally:
            manifest.unlink(missing_ok=True)
            joined.unlink(missing_ok=True)
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
