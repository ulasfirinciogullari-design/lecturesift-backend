
import os, uuid, shutil, subprocess, json, traceback, threading, time
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

app = FastAPI(title="LectureSift Backend V2.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS = {}

@app.get("/")
def root():
    return {"ok": True, "service": "LectureSift Backend V2.1"}

@app.get("/health")
def health():
    return {"ok": True, "openai_key": bool(OPENAI_API_KEY), "slide_engine": "v2.1"}

@app.get("/progress/{job_id}")
def progress(job_id: str):
    return JOBS.get(job_id, {"status":"unknown","percent":0,"stage":"unknown"})

def set_progress(job_id, percent, stage):
    JOBS[job_id] = {"status":"working","percent":int(percent),"stage":stage, "updated": time.time()}

def run(cmd):
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

def dhash(frame):
    gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (17,16), interpolation=cv2.INTER_AREA)
    return (gray[:,1:] > gray[:,:-1]).flatten()

def hamming(a,b):
    return float(np.count_nonzero(a != b)) / len(a)

def fast_scene_candidates(video_path: Path, job_id: str):
    """
    Pass 1: very cheap scan.
    Samples low-res frames and keeps only meaningful scene changes or long-stable runs.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError("Video açılamadı.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total / fps if fps else 0

    # Adaptive sampling:
    # <=10m: every 1.5s, <=60m: 2.5s, longer: 4s
    if duration <= 600:
        step = 1.5
    elif duration <= 3600:
        step = 2.5
    else:
        step = 4.0

    target_w = 160
    prev = None
    stable_start = None
    last_kept_t = -999
    candidates = []
    t = 0.0

    total_steps = max(1, int(duration/step)+1)
    i = 0
    while t <= duration:
        cap.set(cv2.CAP_PROP_POS_MSEC, t*1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            t += step; i += 1; continue

        h = max(1, int(frame.shape[0]*target_w/frame.shape[1]))
        small = cv2.resize(frame, (target_w,h), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if prev is None:
            candidates.append((t, frame.copy()))
            last_kept_t = t
            stable_start = t
        else:
            diff = float(np.mean(cv2.absdiff(gray, prev))) / 255.0

            # strong scene change
            if diff > 0.085:
                candidates.append((t, frame.copy()))
                last_kept_t = t
                stable_start = t
            else:
                # stable region: keep a representative every 12 sec max
                if diff < 0.025:
                    if stable_start is None:
                        stable_start = t
                    if t - last_kept_t >= 12:
                        candidates.append((t, frame.copy()))
                        last_kept_t = t
                else:
                    stable_start = t

        prev = gray
        i += 1
        if i % 10 == 0:
            set_progress(job_id, 10 + int(25*i/total_steps), "Hızlı sahne taraması")
        t += step

    cap.release()
    return candidates, duration

def presentation_score(frame):
    """
    Cheap-ish detailed classifier for candidates only.
    """
    h = max(1, int(frame.shape[0]*320/frame.shape[1]))
    small = cv2.resize(frame,(320,h),interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)

    edges = cv2.Canny(gray,70,160)
    edge_density = np.count_nonzero(edges)/edges.size

    lap = cv2.Laplacian(gray,cv2.CV_32F)
    flat_ratio = np.mean(np.abs(lap)<8)

    hsv = cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
    sat = np.mean(hsv[:,:,1])/255.0

    # center-face-ish motion/video footage tends to be colorful + less flat;
    # slides often have larger flat regions and crisp edges/text.
    score = 0
    if edge_density > 0.03: score += 2
    if edge_density > 0.055: score += 1
    if flat_ratio > 0.42: score += 2
    if sat < 0.62: score += 1

    return score, {
        "edge_density": round(float(edge_density),4),
        "flat_ratio": round(float(flat_ratio),4),
        "saturation": round(float(sat),4)
    }

def fullness_score(frame):
    h = max(1, int(frame.shape[0]*320/frame.shape[1]))
    small = cv2.resize(frame,(320,h),interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray,60,150)
    edge = np.count_nonzero(edges)/edges.size
    hist = cv2.calcHist([gray],[0],None,[32],[0,256]).ravel()
    p = hist/max(hist.sum(),1)
    p = p[p>0]
    ent = float(-(p*np.log2(p)).sum())/5.0
    return edge*2 + ent

def extract_slides_v21(video_path: Path, slides_dir: Path, job_id: str):
    slides_dir.mkdir(parents=True, exist_ok=True)
    candidates, duration = fast_scene_candidates(video_path, job_id)
    set_progress(job_id, 38, "Slayt adayları seçiliyor")

    filtered = []
    for idx,(t,frame) in enumerate(candidates):
        score, metrics = presentation_score(frame)
        # conservative but not too strict
        if score >= 4:
            filtered.append({
                "time":t,
                "frame":frame,
                "hash":dhash(frame),
                "fullness":fullness_score(frame),
                "score":score,
                "metrics":metrics
            })
        if idx % 8 == 0:
            set_progress(job_id, 38 + int(14*(idx+1)/max(1,len(candidates))), "Slayt adayları seçiliyor")

    set_progress(job_id, 53, "Tekrarlar temizleniyor")

    # Group only candidate frames, not every sample.
    groups=[]
    for item in filtered:
        if not groups:
            groups.append([item]); continue
        prev=groups[-1][-1]
        hd=hamming(item["hash"],prev["hash"])
        gap=item["time"]-prev["time"]
        if gap <= 15 and hd < 0.20:
            groups[-1].append(item)
        else:
            groups.append([item])

    reps=[]
    for g in groups:
        # Prefer denser, later frame for progressive builds
        best=max(g,key=lambda x:(x["fullness"],x["time"]))
        reps.append(best)

    unique=[]
    for item in reps:
        dup=None
        for i,old in enumerate(unique):
            if hamming(item["hash"],old["hash"]) < 0.055:
                dup=i;break
        if dup is None:
            unique.append(item)
        elif item["fullness"] > unique[dup]["fullness"]*1.03:
            unique[dup]=item

    unique.sort(key=lambda x:x["time"])

    manifest=[]
    for i,item in enumerate(unique,1):
        sec=item["time"]
        fn=f"slide_{i:03d}_{int(sec//60):02d}m{int(sec%60):02d}s.jpg"
        cv2.imwrite(str(slides_dir/fn),item["frame"],[int(cv2.IMWRITE_JPEG_QUALITY),90])
        manifest.append({
            "file":fn,
            "second":round(sec,1),
            "slide_score":item["score"],
            **item["metrics"]
        })

    diagnostics={
        "duration_seconds":round(duration,1),
        "fast_candidates":len(candidates),
        "presentation_candidates":len(filtered),
        "groups":len(groups),
        "final_unique_slides":len(manifest)
    }
    return manifest, diagnostics

def extract_audio(video_path: Path, job: Path):
    audio=job/"audio.mp3"
    run(["ffmpeg","-y","-i",str(video_path),"-vn","-ac","1","-ar","16000","-b:a","32k",str(audio)])
    if not audio.exists() or audio.stat().st_size==0:
        raise RuntimeError("Ses çıkarılamadı.")
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
    job_id=str(uuid.uuid4())
    JOBS[job_id]={"status":"working","percent":1,"stage":"Video yükleniyor"}

    ext=Path(file.filename or "video.mp4").suffix.lower()
    if ext not in {".mp4",".mov",".mkv",".webm",".mpeg",".mpg",".m4v"}:
        raise HTTPException(400,"Desteklenmeyen video formatı.")

    job=WORK/job_id
    job.mkdir(parents=True,exist_ok=True)
    video=job/("input"+ext)
    slides_dir=job/"slides"

    try:
        with open(video,"wb") as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                out.write(chunk)
        set_progress(job_id, 8, "Video yüklendi")

        slides,diagnostics=extract_slides_v21(video,slides_dir,job_id)
        set_progress(job_id, 68, "Ses işleniyor")

        if has_audio_stream(video):
            audio=extract_audio(video,job)
            set_progress(job_id, 76, "Transkript oluşturuluyor")
            transcript=transcribe_audio(audio,None if language=="auto" else language)
            audio_status="Ses algılandı ve transkript oluşturuldu."
        else:
            transcript="Bu videoda ses kanalı bulunamadı. Bu nedenle transkript oluşturulmadı."
            audio_status="Ses kanalı bulunamadı; yalnızca görsel/slayt analizi yapıldı."

        set_progress(job_id, 91, "Sonuç dosyaları hazırlanıyor")
        (job/"transcript.txt").write_text(transcript,encoding="utf-8")
        (job/"slides.json").write_text(json.dumps(slides,ensure_ascii=False,indent=2),encoding="utf-8")
        (job/"diagnostics.json").write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2),encoding="utf-8")
        (job/"README.txt").write_text(
            "LectureSift V2.1\n"
            f"Final slayt sayısı: {len(slides)}\n"
            f"{audio_status}\n"
            "V2.1: iki aşamalı hızlı tarama + aday karelerde detaylı analiz.\n",
            encoding="utf-8"
        )

        result=job/"result"; result.mkdir()
        for fn in ["transcript.txt","slides.json","diagnostics.json","README.txt"]:
            shutil.copy(job/fn,result/fn)
        shutil.copytree(slides_dir,result/"slides")

        zip_base=job/"LectureSift_Result_V2_1"
        shutil.make_archive(str(zip_base),"zip",root_dir=result)

        JOBS[job_id]={"status":"done","percent":100,"stage":"Tamamlandı","updated":time.time()}
        response=FileResponse(str(zip_base)+".zip",media_type="application/zip",filename="LectureSift_Result_V2_1.zip")
        response.headers["X-LectureSift-Job"] = job_id
        return response

    except Exception as e:
        JOBS[job_id]={"status":"error","percent":0,"stage":str(e)[:300],"updated":time.time()}
        print("PROCESS ERROR:",repr(e),flush=True)
        traceback.print_exc()
        raise HTTPException(500,f"Processing failed: {str(e)}")
