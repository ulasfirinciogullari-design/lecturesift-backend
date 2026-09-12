"""Opt-in synthetic long-media probe for an isolated remote allocation.

Calls the real transcription, study and export pipeline. Never run this inside
a production worker or with a production database, queue or storage account.
The caller supplies a temporary directory, a synthetic SQLite database and an
OpenAI key. Runtime and retries are bounded; provider calls incur usage costs.
Repeated synthetic speech tests duration and chunk handling;
it does not establish quality for arbitrary real lectures or large HD uploads.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import re
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
import zipfile


def main():
    root = Path(os.environ["LECTURESIFT_WORK_DIR"]).resolve()
    assert os.environ.get("LECTURESIFT_ISOLATED_LONG_MEDIA_PROBE") == "synthetic-confirmed"
    assert str(root).startswith("/tmp/ls-long-media-")
    assert os.environ.get("DATABASE_URL") == "sqlite:///" + str(root / "billing.db")
    assert not any(os.environ.get(k) for k in (
        "REDIS_URL", "CELERY_BROKER_URL", "S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY", "IYZICO_API_KEY", "PAYTR_MERCHANT_KEY", "RESEND_API_KEY",
        "LECTURESIFT_ADSENSE_API_CLIENT_SECRET", "LECTURESIFT_ADSENSE_API_REFRESH_TOKEN",
    ))
    signal.alarm(2700)
    started = time.monotonic()
    evidence = {"scope": "isolated real pipeline; synthetic repeated speech, low-resolution MP4",
                "revision": os.environ.get("LECTURESIFT_PROBE_REVISION", "unknown"),
                "media_module_sha256": os.environ.get("LECTURESIFT_PROBE_MEDIA_SHA256", "unchanged"),
                "source_module_sha256": json.loads(os.environ.get("LECTURESIFT_PROBE_MODULE_SHA256", "{}")),
                "real_provider": True, "production_data_used": False,
                "browser_upload_tested": False, "runs": []}
    progress_path = root / "evidence.json"
    lock = threading.Lock()

    def progress(stage, **fields):
        with lock:
            evidence.update(stage=stage, elapsed_seconds=round(time.monotonic() - started, 2), **fields)
            tmp = progress_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(evidence))
            tmp.replace(progress_path)

    def command(args, timeout=480):
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError("synthetic_media_command_failed")
        return result.stdout

    try:
        progress("initializing")
        from openai import OpenAI
        from sqlalchemy import update
        from lecturesift import ai, billing_service as billing, config
        from lecturesift.duration import media_duration_seconds
        from lecturesift.jobs import JOBS
        from lecturesift import pipeline
        from lecturesift.pipeline import process_job
        from lecturesift.tasks import _enforce_uploaded_job_quota
        from PIL import Image, ImageDraw, ImageFont

        # Keep the provider's normal ten-minute request timeout. Retrying is
        # disabled only in this probe so one diagnosis cannot duplicate spend.
        ai._CLIENT = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=600, max_retries=0)
        evidence["transcription_parallelism"] = config.TRANSCRIPTION_PARALLELISM
        evidence["transcription_chunk_seconds"] = pipeline.FAST_TRANSCRIPTION_CHUNK_SECONDS
        original_transcribe = pipeline.transcribe

        def safe_error(exc):
            value = str(exc).replace(os.environ["OPENAI_API_KEY"], "[redacted]")
            return re.sub(r"https?://\S+", "[url]", value)[:1200]

        def observed_transcribe(path, language, duration):
            try:
                return original_transcribe(path, language, duration)
            except Exception as exc:
                progress("provider_failure", provider_failure={"type": type(exc).__name__,
                         "duration_seconds": duration, "file": path.name, "detail": safe_error(exc)})
                raise

        pipeline.transcribe = observed_transcribe
        progress("generating_synthetic_speech")
        speech = (
            "This biology lesson explains photosynthesis and ecosystems. Plants use light to make glucose. "
            "Chlorophyll absorbs sunlight. Water and carbon dioxide are inputs. Oxygen is released. "
            "Glucose stores chemical energy. Producers support the food chain. Consumers obtain energy "
            "by eating plants or other animals. Decomposers return nutrients to the soil. Energy moves "
            "through an ecosystem and some energy is lost as heat at each step. A balanced ecosystem "
            "depends on biodiversity. Review how photosynthesis connects plants with other organisms."
        )
        ending = (
            "Final chapter. Blue whales migrate across the ocean. Whale migration is the final topic "
            "in this lesson. Protecting marine habitats supports biodiversity. This concludes the lesson."
        )
        for name, text in [("body", speech), ("ending", ending)]:
            text_path = root / (name + ".txt")
            text_path.write_text(text)
            command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                     "flite=textfile=" + str(text_path) + ":voice=slt", "-ar", "16000", "-ac", "1",
                     "-y", str(root / (name + ".wav"))], 30)
        slide = Image.new("RGB", (640, 360), "#f4f8f0")
        draw = ImageDraw.Draw(slide)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        draw.multiline_text((35, 45), "BIOLOGY: ENERGY AND LIFE\n\nSunlight + water + carbon dioxide\nPlants produce glucose and oxygen\n\nProducers / consumers / decomposers\nBiodiversity supports ecosystems", font=font, fill="#16472c", spacing=12)
        slide.save(root / "slide.png")

        for hours, plan in [(3, "plus"), (5, "pro")]:
            row = {"hours": hours, "plan": plan, "synthetic": True}
            evidence["runs"].append(row)
            progress("generating_video", current_hours=hours)
            source = root / (str(hours) + "-hour-lesson.mp4")
            seconds = hours * 3600
            command([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "1", "-filter_complex_threads", "1",
                "-stream_loop", "-1", "-i", str(root / "body.wav"), "-i", str(root / "ending.wav"),
                "-loop", "1", "-framerate", "1/30", "-i", str(root / "slide.png"),
                "-filter_complex", f"[0:a]atrim=duration={seconds - 30},asetpts=PTS-STARTPTS[a];[1:a]apad=whole_dur=30,atrim=duration=30,asetpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=0:a=1[out]",
                "-map", "2:v", "-map", "[out]", "-t", str(seconds), "-c:v", "libx264",
                "-preset", "ultrafast", "-tune", "stillimage", "-threads", "1", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "32k", "-ar", "16000", "-ac", "1", "-movflags", "+faststart", "-y", str(source),
            ])
            duration = media_duration_seconds([source])
            row.update(duration_seconds=round(duration, 3), source_bytes=source.stat().st_size)
            assert abs(duration - seconds) < 2
            user_id = billing.register_user("long-" + uuid.uuid4().hex + "@example.invalid", "Synthetic-password123!", "Synthetic", "Probe")["user"]["id"]
            with billing.ENGINE.begin() as connection:
                connection.execute(update(billing.USER_PROFILES).where(billing.USER_PROFILES.c.user_id == user_id).values(email_verified_at=billing.utcnow()))
            order = billing.create_payment_order(user_id, "synthetic-provider", plan, "monthly", "TRY")
            billing.complete_payment_order(order["reference"], succeeded=True, provider_amount_minor=order["amount_minor"])
            before = billing.account_status(user_id)
            job_id = "long-" + str(hours) + "-" + uuid.uuid4().hex
            job_dir = root / job_id
            job_dir.mkdir()
            options = {"billing_user_id": user_id, "job_type": "study_pack", "source_language": "en",
                       "output_language": "en", "summary_style": "detailed", "include_summary": True,
                       "include_transcript": True, "include_slides": True, "quiz_count": 5,
                       "flashcard_count": 5, "output_formats": ["pdf", "docx", "txt"],
                       "translate_transcript": False, "transcript_timestamps": False, "speaker_detection": False,
                       "transcript_timestamps_mode": "chunk_estimate",
                       "_measured_audio_duration_seconds": duration, "_measured_duration_seconds": duration}
            JOBS.create(job_id, job_dir, options, user_id=user_id, source_type="upload")
            _enforce_uploaded_job_quota(user_id, job_id, duration, source_file_count=1,
                                        source_size_bytes=source.stat().st_size, document_mode=False,
                                        document_pages=0, ocr_pages=0)
            row["quota_accepted"] = True
            stop = threading.Event()

            def monitor():
                while not stop.wait(15):
                    state = JOBS.get(job_id) or {}
                    progress("processing", current_job_stage=state.get("stage"), current_percent=state.get("percent"))

            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            run_started = time.monotonic()
            try:
                process_job(job_id, [source], options)
            finally:
                stop.set()
                thread.join(timeout=2)
            state = JOBS.get(job_id) or {}
            row.update(status=state.get("status"), error_code=state.get("error_code"), elapsed_seconds=round(time.monotonic() - run_started, 2))
            if state.get("status") != "done":
                row["error_detail"] = safe_error(state.get("technical_error") or "")
                raise RuntimeError("long_pipeline_failed")
            result_files = list(job_dir.rglob("result.json"))
            if not result_files:
                result_files = list(job_dir.rglob("*_data.json"))
            assert result_files, "result_json_missing"
            result = json.loads(result_files[0].read_text())
            transcript = result.get("transcript_original", "")
            segments = result.get("transcript_segments", [])
            with zipfile.ZipFile(state["result_path"]) as archive:
                assert archive.testzip() is None
                names = archive.namelist()
            row.update(transcript_characters=len(transcript), transcript_chunks=len(segments),
                       last_segment_end=max((float(x.get("end", 0)) for x in segments), default=0),
                       beginning_topic_present="photosynth" in transcript.lower(),
                       final_topic_present="whale" in transcript.lower(),
                       transcript_ending=transcript[-600:],
                       summary_words=len(result.get("summary", "").split()),
                       quiz_count=len(result.get("quiz", [])), flashcard_count=len(result.get("flashcards", [])),
                       slides=len(result.get("slides", [])), zip_bytes=Path(state["result_path"]).stat().st_size,
                       exported_extensions=sorted({Path(n).suffix for n in names}))
            assert row["beginning_topic_present"], "beginning_topic_missing"
            assert row["final_topic_present"], "final_topic_missing"
            assert row["last_segment_end"] >= seconds - 2 and row["transcript_chunks"] >= hours * 4
            assert row["summary_words"] >= 100, "summary_missing"
            assert row["quiz_count"] == 5, "quiz_count_incomplete"
            assert row["flashcard_count"] == 5, "flashcard_count_incomplete"
            assert {".pdf", ".docx", ".txt"}.issubset(row["exported_extensions"])
            after = billing.account_status(user_id)
            row["minutes_debited"] = before["remaining_minutes"] - after["remaining_minutes"]
            assert hours * 60 <= row["minutes_debited"] <= hours * 60 + 1
            progress("case_completed")
        evidence["ok"] = True
    except Exception as exc:
        evidence.update(ok=False, error={"type": type(exc).__name__, "code": getattr(exc, "code", None),
                                        "assertion": str(exc)[:120] if isinstance(exc, AssertionError) else None})
    finally:
        database = root / "billing.db"
        if database.exists():
            with sqlite3.connect(database) as connection:
                if connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lecturesift_cost_events'").fetchone():
                    evidence["cost_usd"] = (connection.execute("SELECT sum(cost_microusd) FROM lecturesift_cost_events").fetchone()[0] or 0) / 1_000_000
        evidence["max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)
        progress("finished")
        print(json.dumps(evidence))


if __name__ == "__main__":
    main()
