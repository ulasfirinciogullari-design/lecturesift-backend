import threading, time, traceback
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import Form, HTTPException

import main

app = main.app


def validate_remote_url(url: str):
    try:
        u = urlparse(url.strip())
    except Exception:
        raise HTTPException(400, "Invalid URL")
    if u.scheme not in {"http", "https"} or not u.netloc:
        raise HTTPException(400, "Only http/https URLs are supported")
    host = (u.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        raise HTTPException(400, "Local/private URLs are not allowed")
    return url.strip()


def download_remote_video(url: str, job: Path):
    outtmpl = str(job / "remote.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 30,
        "max_filesize": 1024 * 1024 * 1024,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        requested = info.get("requested_downloads") or []
        candidates = []
        for d in requested:
            fp = d.get("filepath")
            if fp:
                candidates.append(Path(fp))
        prepared = Path(ydl.prepare_filename(info))
        candidates += [prepared, prepared.with_suffix(".mp4")]
    existing = [x for x in candidates if x.exists()]
    if not existing:
        existing = list(job.glob("remote.*"))
    if not existing:
        raise RuntimeError("Remote video could not be downloaded")
    existing.sort(key=lambda x: (x.suffix.lower() != ".mp4", -x.stat().st_size))
    return existing[0]


@app.get("/url-health")
def url_health():
    return {"ok": True, "url_processing": True, "service": "LectureSift V3.1 URL"}


@app.post("/jobs/url")
def create_url_job(
    video_url: str = Form(...),
    source_language: str = Form("auto"),
    output_language: str = Form("tr"),
    summary_style: str = Form("standard"),
    quiz_count: int = Form(10),
    flashcard_count: int = Form(20),
):
    url = validate_remote_url(video_url)
    job_id = str(main.uuid.uuid4())
    job = main.WORK / job_id
    job.mkdir(parents=True, exist_ok=True)
    opts = {
        "source_language": source_language,
        "output_language": output_language,
        "summary_style": summary_style,
        "quiz_count": max(3, min(int(quiz_count), 30)),
        "flashcard_count": max(5, min(int(flashcard_count), 60)),
    }
    main.JOBS[job_id] = {
        "job_id": job_id,
        "status": "working",
        "percent": 3,
        "stage": "downloading_url",
        "created": time.time(),
        "job_dir": str(job),
        "options": opts,
        "source_url": url,
    }

    def worker():
        try:
            main.jobset(job_id, percent=5, stage="downloading_url")
            video = download_remote_video(url, job)
            main.jobset(job_id, percent=10, stage="queued")
            main.process_job(job_id, video, opts)
        except Exception as e:
            print("URL PROCESS ERROR:", repr(e), flush=True)
            traceback.print_exc()
            main.jobset(job_id, status="error", percent=0, stage="error", error=str(e)[:1000])

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id, "status": "working"}
