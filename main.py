
import os, uuid, shutil, subprocess, json, traceback
from pathlib import Path
from typing import List

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI

WORK = Path("/tmp/lecturesift")
WORK.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI(title="LectureSift Backend V1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"ok": True, "service": "LectureSift Backend V1.1"}

@app.get("/health")
def health():
    return {"ok": True, "openai_key": bool(OPENAI_API_KEY)}

def run(cmd):
    print("RUN:", " ".join(map(str, cmd)), flush=True)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        print("FFMPEG/COMMAND ERROR:\n", p.stderr, flush=True)
        raise RuntimeError(p.stderr[-12000:])
    return p

def frame_hash(img):
    small = cv2.resize(img, (17, 16), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return (gray[:,1:] > gray[:,:-1]).flatten()

def hamming(a, b):
    return float(np.count_nonzero(a != b)) / len(a)

def extract_slides(video_path: Path, slides_dir: Path):
    slides_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError("Video OpenCV ile açılamadı.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total / fps if fps else 0

    sample_sec = 3.0
    t = 0.0
    candidates, prev_hash = [], None

    while t <= duration:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok and frame is not None:
            h = frame_hash(frame)
            if prev_hash is None or hamming(h, prev_hash) > 0.08:
                candidates.append((t, frame.copy(), h))
                prev_hash = h
        t += sample_sec
    cap.release()

    kept = []
    for item in candidates:
        if not kept:
            kept.append(item)
        elif hamming(item[2], kept[-1][2]) < 0.12:
            kept[-1] = item
        else:
            kept.append(item)

    unique = []
    for item in kept:
        if not any(hamming(item[2], old[2]) < 0.055 for old in unique[-12:]):
            unique.append(item)

    manifest = []
    for i, (sec, frame, _) in enumerate(unique, 1):
        fn = f"slide_{i:03d}_{int(sec//60):02d}m{int(sec%60):02d}s.jpg"
        cv2.imwrite(str(slides_dir / fn), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        manifest.append({"file": fn, "second": round(sec,1)})
    return manifest

def extract_audio(video_path: Path, job: Path):
    audio = job / "audio.mp3"
    # For the first real test: one compact mono MP3.
    # Much simpler and more robust than the previous segment command.
    run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "32k",
        str(audio)
    ])
    if not audio.exists() or audio.stat().st_size == 0:
        raise RuntimeError("Videodan ses dosyası üretilemedi.")
    return audio

def transcribe_audio(audio: Path, language=None):
    if not client:
        raise RuntimeError("OPENAI_API_KEY ayarlı değil.")
    with open(audio, "rb") as f:
        kwargs = {"model": "gpt-4o-mini-transcribe", "file": f}
        if language and language != "auto":
            kwargs["language"] = language
        tr = client.audio.transcriptions.create(**kwargs)
    return getattr(tr, "text", str(tr)).strip()

@app.post("/process")
async def process_video(file: UploadFile = File(...), language: str = "auto"):
    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in {".mp4",".mov",".mkv",".webm",".mpeg",".mpg",".m4v"}:
        raise HTTPException(400, "Desteklenmeyen video formatı.")

    job = WORK / str(uuid.uuid4())
    job.mkdir(parents=True, exist_ok=True)
    video = job / ("input" + ext)
    slides_dir = job / "slides"

    try:
        with open(video, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)

        print(f"Uploaded: {video} size={video.stat().st_size}", flush=True)

        slides = extract_slides(video, slides_dir)
        audio = extract_audio(video, job)
        transcript = transcribe_audio(audio, None if language == "auto" else language)

        (job/"transcript.txt").write_text(transcript, encoding="utf-8")
        (job/"slides.json").write_text(json.dumps(slides, ensure_ascii=False, indent=2), encoding="utf-8")
        (job/"README.txt").write_text(
            f"LectureSift V1.1\nSlayt sayısı: {len(slides)}\n"
            "İlk gerçek işleme prototipi.",
            encoding="utf-8"
        )

        # Do NOT include the original uploaded video/audio in result zip.
        result_root = job / "result"
        result_root.mkdir()
        shutil.copy(job/"transcript.txt", result_root/"transcript.txt")
        shutil.copy(job/"slides.json", result_root/"slides.json")
        shutil.copy(job/"README.txt", result_root/"README.txt")
        shutil.copytree(slides_dir, result_root/"slides")

        zip_base = job / "LectureSift_Result"
        shutil.make_archive(str(zip_base), "zip", root_dir=result_root)
        return FileResponse(str(zip_base)+".zip", media_type="application/zip", filename="LectureSift_Result.zip")

    except Exception as e:
        print("PROCESS ERROR:", repr(e), flush=True)
        traceback.print_exc()
        raise HTTPException(500, f"Processing failed: {str(e)}")
