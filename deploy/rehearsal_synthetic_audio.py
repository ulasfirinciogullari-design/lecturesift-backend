"""Create one synthetic speech source using only a dedicated rehearsal key."""

from __future__ import annotations

import os
from pathlib import Path

from openai import OpenAI
from sqlalchemy.engine import make_url

from lecturesift import config


OUTPUT = Path("/var/lib/lecturesift/rehearsal-synthetic-lecture.mp3")


def main() -> None:
    database = make_url(config.DATABASE_URL).database or ""
    if (
        os.getenv("LECTURESIFT_REHEARSAL") != "1"
        or not database.startswith("lecturesift_rehearsal_")
        or os.getenv("LECTURESIFT_WORKER") != "1"
    ):
        raise RuntimeError("refusing synthetic source generation outside rehearsal worker")
    if not config.OPENAI_API_KEY:
        raise RuntimeError("dedicated rehearsal OpenAI key is absent")
    response = OpenAI(api_key=config.OPENAI_API_KEY, timeout=90.0).audio.speech.create(
        model="gpt-4o-mini-tts",
        voice="alloy",
        input=(
            "Fotosentez, bitkilerin ışık enerjisini kimyasal enerjiye "
            "dönüştürdüğü süreçtir. Kloroplastlarda ATP üretilir ve Calvin "
            "döngüsü karbondioksiti kullanır."
        ),
        response_format="mp3",
    )
    response.stream_to_file(OUTPUT)
    if not OUTPUT.is_file() or OUTPUT.stat().st_size < 1024:
        raise RuntimeError("synthetic rehearsal audio was not created")
    OUTPUT.chmod(0o600)


if __name__ == "__main__":
    main()
