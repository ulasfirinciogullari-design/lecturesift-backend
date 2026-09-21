"""Generate LectureSift's original, unobtrusive Reel backing track.

This track is composed from simple synthesized tones and percussion. It uses
no samples, third-party recording, or platform music library.
"""

from __future__ import annotations

import math
import random
import subprocess
import tempfile
import wave
from pathlib import Path


RATE = 48_000
BEAT = 0.6
SECONDS = 16.8
CHORDS = (
    (146.83, 174.61, 220.00),  # D minor
    (116.54, 146.83, 174.61),  # B flat major
    (174.61, 220.00, 261.63),  # F major
    (130.81, 164.81, 196.00),  # C major
)


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "lecturesift/assets/instagram/music-bed.m4a"
    output.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260921)
    with tempfile.TemporaryDirectory(prefix="lecturesift-music-") as work:
        wav = Path(work) / "bed.wav"
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(RATE)
            data = bytearray()
            for sample in range(int(SECONDS * RATE)):
                t = sample / RATE
                beat = int(t / BEAT)
                beat_phase = t % BEAT
                chord = CHORDS[(beat // 4) % len(CHORDS)]
                fade = min(1.0, t / 0.45, (SECONDS - t) / 0.65)
                pad = sum(math.sin(2 * math.pi * f * t) for f in chord) * 0.009
                half_beat = int(t / (BEAT / 2))
                pluck_time = t % (BEAT / 2)
                pluck_note = chord[(half_beat + half_beat // 8) % 3] * 2
                pluck = math.exp(-pluck_time * 12) * (
                    math.sin(2 * math.pi * pluck_note * pluck_time)
                    + 0.23 * math.sin(4 * math.pi * pluck_note * pluck_time)
                ) * 0.08
                kick = 0.0
                if beat % 4 in (0, 2) and beat_phase < 0.23:
                    kick = math.sin(2 * math.pi * (62 - 26 * beat_phase) * beat_phase) * math.exp(-beat_phase * 19) * 0.16
                noise = rng.uniform(-1, 1)
                hat = noise * math.exp(-pluck_time * 95) * 0.018
                snare = noise * math.exp(-beat_phase * 35) * 0.035 if beat % 4 in (1, 3) else 0.0
                value = max(-1.0, min(1.0, (pad + pluck + kick + hat + snare) * fade))
                data.extend(int(value * 32767).to_bytes(2, "little", signed=True))
                if len(data) >= RATE * 2:
                    audio.writeframes(data)
                    data.clear()
            audio.writeframes(data)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
             "-c:a", "aac", "-ar", "48000", "-b:a", "128k", str(output)],
            check=True,
        )
    print(output)


if __name__ == "__main__":
    main()
