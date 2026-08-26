
import os, uuid, shutil, subprocess, json, traceback, math
from pathlib import Path

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

app = FastAPI(title="LectureSift Backend V2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"ok": True, "service": "LectureSift Backend V2"}

@app.get("/health")
def health():
    return {"ok": True, "openai_key": bool(OPENAI_API_KEY), "slide_engine": "v2"}

def run(cmd):
    print("RUN:", " ".join(map(str, cmd)), flush=True)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        print("COMMAND ERROR:\n", p.stderr, flush=True)
        raise RuntimeError(p.stderr[-12000:])
    return p

def has_audio_stream(video_path: Path) -> bool:
    p = subprocess.run([
        "ffprobe","-v","error","-select_streams","a",
        "-show_entries","stream=index","-of","csv=p=0",str(video_path)
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return bool(p.stdout.strip())

# ---------- V2 SLIDE ENGINE ----------

def resize_gray(frame, w=320):
    h = max(1, int(frame.shape[0] * (w / frame.shape[1])))
    small = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

def dhash(frame):
    gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (17,16), interpolation=cv2.INTER_AREA)
    return (gray[:,1:] > gray[:,:-1]).flatten()

def hamming(a,b):
    return float(np.count_nonzero(a != b)) / len(a)

def visual_distance(a, b):
    ga, gb = resize_gray(a), resize_gray(b)
    if ga.shape != gb.shape:
        gb = cv2.resize(gb, (ga.shape[1], ga.shape[0]))
    return float(np.mean(cv2.absdiff(ga, gb))) / 255.0

def edge_density(frame):
    gray = resize_gray(frame)
    edges = cv2.Canny(gray, 70, 160)
    return float(np.count_nonzero(edges)) / edges.size

def flat_region_ratio(frame):
    gray = resize_gray(frame)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    return float(np.mean(np.abs(lap) < 8))

def saturation_mean(frame):
    small = cv2.resize(frame, (240, max(1,int(frame.shape[0]*240/frame.shape[1]))))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:,:,1])) / 255.0

def likely_slide(frame, neighbor):
    """
    Conservative heuristic:
    - slides/screens are comparatively stable over ~0.8 sec
    - usually contain enough edges/text/diagram structure
    - talking-head footage tends to have more local motion
    """
    motion = visual_distance(frame, neighbor)
    edges = edge_density(frame)
    flat = flat_region_ratio(frame)
    sat = saturation_mean(frame)

    stable = motion < 0.035
    structured = edges > 0.035
    presentation_like = (flat > 0.40 and edges > 0.025) or (edges > 0.055)
    not_extreme_video = sat < 0.72

    score = 0
    score += 3 if stable else 0
    score += 2 if structured else 0
    score += 2 if presentation_like else 0
    score += 1 if not_extreme_video else 0
    return score >= 6, {
        "motion": round(motion,4),
        "edge_density": round(edges,4),
        "flat_ratio": round(flat,4),
        "saturation": round(sat,4),
        "score": score
    }

def fullness_score(frame):
    # Prefer the more information-dense member of progressive slide builds.
    gray = resize_gray(frame)
    edges = cv2.Canny(gray, 60, 150)
    edge = np.count_nonzero(edges) / edges.size
    # entropy
    hist = cv2.calcHist([gray],[0],None,[64],[0,256]).ravel()
    p = hist / max(hist.sum(), 1)
    p = p[p>0]
    ent = float(-(p*np.log2(p)).sum()) / 6.0
    return edge * 2.0 + ent

def extract_slides_v2(video_path: Path, slides_dir: Path):
    slides_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError("Video OpenCV ile açılamadı.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total / fps if fps else 0

    # Dense enough for transitions, sparse enough for long lectures.
    step = 2.0 if duration <= 1800 else 3.0
    neighbor_gap = 0.8

    candidates = []
    t = 0.0
    while t <= duration:
        cap.set(cv2.CAP_PROP_POS_MSEC, t*1000)
        ok1, frame = cap.read()
        cap.set(cv2.CAP_PROP_POS_MSEC, min(duration, t+neighbor_gap)*1000)
        ok2, neigh = cap.read()
        if ok1 and ok2 and frame is not None and neigh is not None:
            yes, metrics = likely_slide(frame, neigh)
            if yes:
                candidates.append({
                    "time": t,
                    "frame": frame.copy(),
                    "hash": dhash(frame),
                    "fullness": fullness_score(frame),
                    "metrics": metrics
                })
        t += step
    cap.release()

    # Group consecutive near-identical/progressively-built slide candidates.
    groups = []
    for item in candidates:
        if not groups:
            groups.append([item])
            continue
        prev = groups[-1][-1]
        hd = hamming(item["hash"], prev["hash"])
        vd = visual_distance(item["frame"], prev["frame"])
        time_gap = item["time"] - prev["time"]

        # Same slide / progressive reveal: modest visual difference and close in time.
        if time_gap <= step*1.6 and (hd < 0.20 or vd < 0.10):
            groups[-1].append(item)
        else:
            groups.append([item])

    # In each group keep the richest/latest complete version.
    representatives = []
    for g in groups:
        # prefer information density; use later frame as tie-breaker
        best = max(g, key=lambda x: (x["fullness"], x["time"]))
        representatives.append(best)

    # Global duplicate cleanup: recurring title slides etc.
    unique = []
    for item in representatives:
        duplicate_idx = None
        for i, old in enumerate(unique):
            hd = hamming(item["hash"], old["hash"])
            vd = visual_distance(item["frame"], old["frame"])
            if hd < 0.055 or vd < 0.035:
                duplicate_idx = i
                break
        if duplicate_idx is None:
            unique.append(item)
        else:
            # If duplicate reappears with more content, replace older copy.
            if item["fullness"] > unique[duplicate_idx]["fullness"] * 1.04:
                unique[duplicate_idx] = item

    unique.sort(key=lambda x: x["time"])

    manifest = []
    for i,item in enumerate(unique,1):
        sec = item["time"]
        fn = f"slide_{i:03d}_{int(sec//60):02d}m{int(sec%60):02d}s.jpg"
        cv2.imwrite(str(slides_dir/fn), item["frame"], [int(cv2.IMWRITE_JPEG_QUALITY),92])
        manifest.append({
            "file": fn,
            "second": round(sec,1),
            "slide_score": item["metrics"]["score"],
            "motion": item["metrics"]["motion"],
            "edge_density": item["metrics"]["edge_density"]
        })

    diagnostics = {
        "duration_seconds": round(duration,1),
        "sample_step_seconds": step,
        "slide_candidates": len(candidates),
        "candidate_groups": len(groups),
        "final_unique_slides": len(manifest)
    }
    return manifest, diagnostics

# ---------- AUDIO / TRANSCRIPT ----------

def extract_audio(video_path: Path, job: Path):
    audio = job/"audio.mp3"
    run(["ffmpeg","-y","-i",str(video_path),"-vn","-ac","1","-ar","16000","-b:a","32k",str(audio)])
    if not audio.exists() or audio.stat().st_size == 0:
        raise RuntimeError("Videodan ses dosyası üretilemedi.")
    return audio

def transcribe_audio(audio: Path, language=None):
    if not client:
        raise RuntimeError("OPENAI_API_KEY ayarlı değil.")
    with open(audio,"rb") as f:
        kwargs={"model":"gpt-4o-mini-transcribe","file":f}
        if language and language!="auto":
            kwargs["language"]=language
        tr=client.audio.transcriptions.create(**kwargs)
    return getattr(tr,"text",str(tr)).strip()

@app.post("/process")
async def process_video(file: UploadFile=File(...), language: str="auto"):
    ext=Path(file.filename or "video.mp4").suffix.lower()
    if ext not in {".mp4",".mov",".mkv",".webm",".mpeg",".mpg",".m4v"}:
        raise HTTPException(400,"Desteklenmeyen video formatı.")

    job=WORK/str(uuid.uuid4())
    job.mkdir(parents=True,exist_ok=True)
    video=job/("input"+ext)
    slides_dir=job/"slides"

    try:
        with open(video,"wb") as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                out.write(chunk)

        print(f"Uploaded: {video} size={video.stat().st_size}",flush=True)

        slides, diagnostics = extract_slides_v2(video, slides_dir)

        if has_audio_stream(video):
            audio=extract_audio(video,job)
            transcript=transcribe_audio(audio,None if language=="auto" else language)
            audio_status="Ses algılandı ve transkript oluşturuldu."
        else:
            transcript="Bu videoda ses kanalı bulunamadı. Bu nedenle transkript oluşturulmadı."
            audio_status="Ses kanalı bulunamadı; yalnızca görsel/slayt analizi yapıldı."

        (job/"transcript.txt").write_text(transcript,encoding="utf-8")
        (job/"slides.json").write_text(json.dumps(slides,ensure_ascii=False,indent=2),encoding="utf-8")
        (job/"diagnostics.json").write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2),encoding="utf-8")
        (job/"README.txt").write_text(
            "LectureSift V2\n"
            f"Final slayt sayısı: {len(slides)}\n"
            f"{audio_status}\n"
            "V2: sunum-benzeri durağan sahne filtresi + aşamalı slayt gruplama + global tekrar temizliği.\n",
            encoding="utf-8"
        )

        result=job/"result"; result.mkdir()
        for fn in ["transcript.txt","slides.json","diagnostics.json","README.txt"]:
            shutil.copy(job/fn,result/fn)
        shutil.copytree(slides_dir,result/"slides")

        zip_base=job/"LectureSift_Result_V2"
        shutil.make_archive(str(zip_base),"zip",root_dir=result)
        return FileResponse(str(zip_base)+".zip",media_type="application/zip",filename="LectureSift_Result_V2.zip")

    except Exception as e:
        print("PROCESS ERROR:",repr(e),flush=True)
        traceback.print_exc()
        raise HTTPException(500,f"Processing failed: {str(e)}")
