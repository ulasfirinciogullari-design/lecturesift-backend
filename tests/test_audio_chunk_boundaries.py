"""Real FFmpeg boundary regression, run in remote CI with no provider call."""
import shutil
import subprocess

import pytest

from lecturesift.duration import file_duration_seconds
from lecturesift.media import extract_audio_chunks


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
@pytest.mark.parametrize("seconds,expected_chunks", [(120, 2), (120.5, 2), (121.5, 3)])
def test_short_final_fragments_are_joined_without_losing_audio(tmp_path, seconds, expected_chunks):
    source = tmp_path / "source.m4a"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=16000:duration={seconds}", "-c:a", "aac", "-b:a", "32k", str(source),
    ], check=True, capture_output=True, timeout=20)
    chunks = extract_audio_chunks(source, tmp_path, prefix="boundary", segment_seconds=60)
    assert len(chunks) == expected_chunks
    durations = [file_duration_seconds(path) for path in chunks]
    assert all(duration >= 1 for duration in durations)
    assert seconds - .1 <= sum(durations) <= seconds + .6
    assert not list(tmp_path.glob("*joined*"))
    assert not list(tmp_path.glob("*join.txt"))
    # The last audible samples survive; the fix must not silently trim the tail.
    decoded = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-sseof", "-0.25", "-i", str(chunks[-1]),
        "-f", "s16le", "-ac", "1", "-ar", "16000", "-",
    ], check=True, capture_output=True, timeout=10).stdout
    assert len(decoded) >= 4000
    assert any(decoded)
