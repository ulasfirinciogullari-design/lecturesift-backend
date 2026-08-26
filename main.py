
import os, uuid, shutil, subprocess, json, math
from pathlib import Path
from typing import List

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI

APP_DIR = Path(__file__).resolve().parent
WORK = Path("/tmp/lecturesift")
WORK.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI(title="LectureSift Backend V1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # MVP only; later restrict to your domain
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"ok": True, "service": "LectureSift Backend V1"}

@app.get("/health")
def health():
    return {"ok": True, "openai_key": bool(OPENAI_API_KEY)}

def run(cmd: List[str]):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-4000:])
    return p

def frame_hash(img):
    # simple dHash-like perceptual fingerprint
    small = cv2.resize(img, (17, 16), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    diff = gray[:,1:] > gray[:,:-1]
    return diff.flatten()

def hamming(a, b):
    return float(np.count_nonzero(a != b)) / len(a)

def extract_slides(video_path: Path, slides_dir: Path):
    slides_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total / fps if fps else 0

    # sample every 3 seconds for MVP
    sample_sec = 3.0
    t = 0.0
    candidates = []
    prev_hash = None

    while t <= duration:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            t += sample_sec
            continue
        h = frame_hash(frame)
        # save only when materially different from previous sample
        if prev_hash is None or hamming(h, prev_hash) > 0.08:
            candidates.append((t, frame.copy(), h))
            prev_hash = h
        t += sample_sec
    cap.release()

    # collapse near-duplicate consecutive candidates; prefer the later frame
    kept = []
    for item in candidates:
        if not kept:
            kept.append(item)
            continue
        if hamming(item[2], kept[-1][2]) < 0.12:
            kept[-1] = item
        else:
            kept.append(item)

    # secondary global duplicate removal
    unique = []
    for item in kept:
        duplicate = False
        for old in unique[-12:]:
            if hamming(item[2], old[2]) < 0.055:
                duplicate = True
                break
        if not duplicate:
            unique.append(item)

    manifest = []
    for i, (sec, frame, _) in enumerate(unique, start=1):
        fn = f"slide_{i:03d}_{int(sec//60):02d}m{int(sec%60):02d}s.jpg"
        path = slides_dir / fn
        cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        manifest.append({"file": fn, "second": round(sec,1)})
    return manifest

def extract_audio_chunks(video_path: Path, audio_dir: Path):
    audio_dir.mkdir(parents=True, exist_ok=True)
    pattern = audio_dir / "chunk_%03d.mp3"
    # mono, 32 kbps, 20-minute chunks: small enough for reliable API transfer
    run([
        "ffmpeg","-y","-i",str(video_path),
        "-vn","-ac","1","-ar","16000","-b:a","32k",
        "-f","segment","-segment_time","1200","-reset_timestamps","1",
        str(pattern)
    ])
    return sorted(audio_dir.glob("chunk_*.mp3"))

def transcribe_chunks(chunks: List[Path], language: str | None = None):
    if not client:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    texts = []
    for i, chunk in enumerate(chunks, start=1):
        with open(chunk, "rb") as f:
            kwargs = {"model": "gpt-4o-mini-transcribe", "file": f}
            if language and language != "auto":
                kwargs["language"] = language
            tr = client.audio.transcriptions.create(**kwargs)
        text = getattr(tr, "text", str(tr))
        texts.append(f"\n\n===== PART {i} =====\n{text.strip()}")
    return "".join(texts).strip()

@app.post("/process")
async def process_video(file: UploadFile = File(...), language: str = "auto"):
    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in {".mp4",".mov",".mkv",".webm",".mpeg",".mpg",".m4v"}:
        raise HTTPException(400, "Unsupported video format")

    job = WORK / str(uuid.uuid4())
    job.mkdir(parents=True, exist_ok=True)
    video = job / ("input" + ext)
    slides = job / "slides"
    audio = job / "audio"
    output = job / "LectureSift_Result.zip"

    try:
        with open(video, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                out.write(chunk)

        manifest = extract_slides(video, slides)
        chunks = extract_audio_chunks(video, audio)
        transcript = transcribe_chunks(chunks, None if language=="auto" else language)

        (job/"transcript.txt").write_text(transcript, encoding="utf-8")
        (job/"slides.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (job/"README.txt").write_text(
            f"LectureSift V1 result\nSlides found: {len(manifest)}\n\n"
            "This is the first processing prototype. Slide detection will improve in later versions.",
            encoding="utf-8"
        )

        shutil.make_archive(str(output.with_suffix("")), "zip", root_dir=job, base_dir=".")
        # archive included itself possibility avoided because it was created after scan in make_archive
        return FileResponse(str(output), media_type="application/zip", filename="LectureSift_Result.zip")
    except Exception as e:
        raise HTTPException(500, f"Processing failed: {str(e)}")
