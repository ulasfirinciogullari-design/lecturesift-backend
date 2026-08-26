import os, uuid, shutil, subprocess, json, traceback, threading, time, re
from pathlib import Path
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen
import html
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI
import yt_dlp

WORK = Path("/tmp/lecturesift")
WORK.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI(title="LectureSift Backend V3.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS = {}
LOCK = threading.Lock()

def jobset(job_id, **kwargs):
    with LOCK:
        cur = JOBS.setdefault(job_id, {})
        cur.update(kwargs)
        cur["updated"] = time.time()

@app.get("/")
def root():
    return {"ok": True, "service": "LectureSift Backend V3.1"}

@app.get("/health")
def health():
    return {
        "ok": True,
        "openai_key": bool(OPENAI_API_KEY),
        "slide_engine": "v3.1",
        "study_pack": True,
        "async_jobs": True,
    }


def validate_remote_url(url: str):
    try:
        u = urlparse(url.strip())
    except Exception:
        raise HTTPException(400, "Invalid URL")
    if u.scheme not in {"http","https"} or not u.netloc:
        raise HTTPException(400, "Only http/https video URLs are supported")
    host=(u.hostname or "").lower()
    if host in {"localhost","127.0.0.1","0.0.0.0","::1"} or host.endswith(".local"):
        raise HTTPException(400, "Local/private URLs are not allowed")
    return url.strip()

def _download_direct_media(media_url: str, job: Path) -> Path:
    parsed = urlparse(media_url)
    ext = Path(parsed.path).suffix.lower()
    if ext not in {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".mpeg", ".mpg"}:
        ext = ".mp4"
    out = job / ("remote" + ext)
    req = Request(media_url, headers={"User-Agent": "Mozilla/5.0 LectureSift/3.1"})
    total = 0
    limit = 1024 * 1024 * 1024
    with urlopen(req, timeout=45) as r, open(out, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise RuntimeError("Remote video is larger than the 1 GB limit.")
            f.write(chunk)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("Remote media download produced an empty file.")
    return out

def _find_media_in_page(page_url: str):
    req = Request(page_url, headers={"User-Agent": "Mozilla/5.0 LectureSift/3.1"})
    with urlopen(req, timeout=30) as r:
        ctype = (r.headers.get("content-type") or "").lower()
        final_url = r.geturl()
        if any(x in ctype for x in ("video/mp4","video/webm","application/octet-stream")):
            return final_url
        raw = r.read(6 * 1024 * 1024)
    text = html.unescape(raw.decode("utf-8", errors="ignore"))
    patterns = [
        r'''(?:href|src|content)\s*=\s*["']([^"']+\.(?:mp4|m4v|mov|webm|mkv|mpeg|mpg)(?:\?[^"']*)?)["']''',
        r'''["']([^"']+\.m3u8(?:\?[^"']*)?)["']''',
        r'''https?://[^\s"'<>\\]+?\.(?:mp4|m4v|mov|webm|mkv|mpeg|mpg)(?:\?[^\s"'<>\\]*)?''',
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            candidate = m.group(1) if m.lastindex else m.group(0)
            candidate = candidate.replace("\\/", "/").strip()
            found.append(urljoin(final_url, candidate))
    found = list(dict.fromkeys(found))
    found.sort(key=lambda u: (".mp4" not in u.lower(), "preview" in u.lower(), len(u)))
    return found[0] if found else None

def download_remote_video(url: str, job: Path):
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in (".mp4",".m4v",".mov",".webm",".mkv",".mpeg",".mpg")):
        return _download_direct_media(url, job)

    try:
        media = _find_media_in_page(url)
        if media:
            if ".m3u8" in media.lower():
                out = job / "remote.mp4"
                run(["ffmpeg","-y","-i",media,"-c","copy",str(out)])
                if out.exists() and out.stat().st_size:
                    return out
            else:
                return _download_direct_media(media, job)
    except Exception as page_error:
        print("PAGE MEDIA DISCOVERY WARNING:", repr(page_error), flush=True)

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
    try:
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
            raise RuntimeError("Remote video could not be downloaded.")
        existing.sort(key=lambda x: (x.suffix.lower() != ".mp4", -x.stat().st_size))
        return existing[0]
    except Exception as e:
        msg = str(e)
        if "429" in msg or "not a bot" in msg.lower() or "sign in" in msg.lower():
            raise RuntimeError(
                "This video provider blocked server-side downloading. "
                "Please use a direct MP4/video link or upload the video file."
            )
        raise RuntimeError(
            "No downloadable video could be found at this page. "
            "Try a direct MP4/video link or upload the file."
        )

@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, "Job not found")
    safe = {k:v for k,v in data.items() if k not in {"result_path","job_dir"}}
    return safe

@app.get("/jobs/{job_id}/result")
def get_result(job_id: str):
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, "Job not found")
    if data.get("status") != "done":
        raise HTTPException(409, "Job not finished")
    p = Path(data["job_dir"]) / "result.json"
    if not p.exists():
        raise HTTPException(404, "Result not found")
    return json.loads(p.read_text(encoding="utf-8"))

@app.get("/jobs/{job_id}/slide/{filename}")
def get_slide(job_id: str, filename: str):
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, "Job not found")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    slide = Path(data["job_dir"]) / "slides" / filename
    if not slide.exists():
        raise HTTPException(404, "Slide not found")
    return FileResponse(str(slide), media_type="image/jpeg")

@app.get("/jobs/{job_id}/download")
def download(job_id: str):
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, "Job not found")
    if data.get("status") != "done":
        raise HTTPException(409, "Job not finished")
    return FileResponse(
        data["result_path"],
        media_type="application/zip",
        filename="LectureSift_Study_Pack.zip"
    )

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

_face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

def dhash(frame):
    gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (17,16), interpolation=cv2.INTER_AREA)
    return (gray[:,1:] > gray[:,:-1]).flatten()

def hamming(a,b):
    return float(np.count_nonzero(a != b)) / len(a)

def face_area_ratio(frame):
    h = max(1, int(frame.shape[0]*360/frame.shape[1]))
    small = cv2.resize(frame,(360,h),interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
    faces = _face.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(36,36))
    area = small.shape[0]*small.shape[1]
    if not len(faces):
        return 0.0
    return max((w*h for x,y,w,h in faces), default=0) / max(area,1)

def skin_ratio(frame):
    h = max(1, int(frame.shape[0]*240/frame.shape[1]))
    small = cv2.resize(frame,(240,h),interpolation=cv2.INTER_AREA)
    ycrcb = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb)
    lo = np.array([0,133,77], dtype=np.uint8)
    hi = np.array([255,173,127], dtype=np.uint8)
    mask = cv2.inRange(ycrcb, lo, hi)
    return float(np.count_nonzero(mask))/mask.size

def presentation_score(frame):
    h = max(1, int(frame.shape[0]*320/frame.shape[1]))
    small = cv2.resize(frame,(320,h),interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray,70,160)
    edge = np.count_nonzero(edges)/edges.size
    lap = cv2.Laplacian(gray,cv2.CV_32F)
    flat = np.mean(np.abs(lap)<8)
    hsv = cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
    sat = np.mean(hsv[:,:,1])/255.0
    face = face_area_ratio(frame)
    skin = skin_ratio(frame)

    score = 0
    if edge > 0.035: score += 2
    if edge > 0.060: score += 1
    if flat > 0.47: score += 2
    if sat < 0.55: score += 1
    if face > 0.025: score -= 4
    if face > 0.075: score -= 3
    if skin > 0.22: score -= 2
    if skin > 0.35: score -= 2

    return score, {
        "edge_density": round(float(edge),4),
        "flat_ratio": round(float(flat),4),
        "saturation": round(float(sat),4),
        "face_ratio": round(float(face),4),
        "skin_ratio": round(float(skin),4),
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

def fast_scene_candidates(video_path: Path, job_id: str):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError("Video could not be opened.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total / fps if fps else 0
    step = 1.5 if duration <= 600 else (2.5 if duration <= 3600 else 4.0)
    target_w = 160

    prev = None
    last_kept_t = -999
    candidates = []
    t = 0.0
    total_steps = max(1, int(duration/step)+1)
    i = 0

    while t <= duration:
        cap.set(cv2.CAP_PROP_POS_MSEC, t*1000)
        ok, frame = cap.read()
        if ok and frame is not None:
            hh = max(1, int(frame.shape[0]*target_w/frame.shape[1]))
            small = cv2.resize(frame,(target_w,hh),interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
            if prev is None:
                candidates.append((t,frame.copy()))
                last_kept_t=t
            else:
                diff = float(np.mean(cv2.absdiff(gray,prev)))/255.0
                if diff > 0.085:
                    candidates.append((t,frame.copy()))
                    last_kept_t=t
                elif diff < 0.022 and t-last_kept_t >= 10:
                    candidates.append((t,frame.copy()))
                    last_kept_t=t
            prev=gray

        i += 1
        if i % 8 == 0:
            jobset(job_id, percent=18+int(18*i/total_steps), stage="scene_scan")
        t += step

    cap.release()
    return candidates, duration

def extract_slides(video_path: Path, slides_dir: Path, job_id: str):
    slides_dir.mkdir(parents=True, exist_ok=True)
    candidates,duration = fast_scene_candidates(video_path,job_id)
    jobset(job_id, percent=37, stage="slide_detection")

    filtered=[]
    for idx,(t,frame) in enumerate(candidates):
        score,metrics = presentation_score(frame)
        if score >= 5 and metrics["face_ratio"] < 0.035 and metrics["skin_ratio"] < 0.30:
            filtered.append({
                "time":t,
                "frame":frame,
                "hash":dhash(frame),
                "fullness":fullness_score(frame),
                "score":score,
                "metrics":metrics
            })
        if idx % 5 == 0:
            jobset(job_id, percent=37+int(10*(idx+1)/max(1,len(candidates))), stage="slide_detection")

    groups=[]
    for item in filtered:
        if not groups:
            groups.append([item])
            continue
        prev=groups[-1][-1]
        if item["time"]-prev["time"] <= 14 and hamming(item["hash"],prev["hash"]) < 0.20:
            groups[-1].append(item)
        else:
            groups.append([item])

    reps=[max(g,key=lambda x:(x["fullness"],x["time"])) for g in groups]
    unique=[]
    for item in reps:
        dup=None
        for i,old in enumerate(unique):
            if hamming(item["hash"],old["hash"]) < 0.055:
                dup=i
                break
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

    return manifest,{
        "duration_seconds":round(duration,1),
        "fast_candidates":len(candidates),
        "presentation_candidates":len(filtered),
        "final_unique_slides":len(manifest)
    }

def extract_audio(video_path: Path, job: Path):
    audio=job/"audio.mp3"
    run(["ffmpeg","-y","-i",str(video_path),"-vn","-ac","1","-ar","16000","-b:a","32k",str(audio)])
    if not audio.exists() or audio.stat().st_size==0:
        raise RuntimeError("Audio extraction failed.")
    return audio

def transcribe(audio: Path, language: str):
    if not client:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    with open(audio,"rb") as f:
        kwargs={"model":"gpt-4o-mini-transcribe","file":f}
        if language and language!="auto":
            kwargs["language"]=language
        tr=client.audio.transcriptions.create(**kwargs)
    return getattr(tr,"text",str(tr)).strip()

LANG_NAMES = {
    "tr":"Turkish","en":"English","de":"German","fr":"French","es":"Spanish","it":"Italian",
    "pt":"Portuguese","ru":"Russian","ar":"Arabic","zh":"Chinese","ja":"Japanese",
    "ko":"Korean","hi":"Hindi"
}

def safe_json(text: str):
    text=text.strip()
    text=re.sub(r"^```(?:json)?\s*","",text,flags=re.I)
    text=re.sub(r"\s*```$","",text)
    return json.loads(text)

def make_study_pack(transcript: str, language: str, summary_style: str, quiz_count: int, flashcard_count: int):
    if not client:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    out_lang=LANG_NAMES.get(language,"English")
    style_map={
        "short":"very concise, key points only",
        "standard":"balanced and structured",
        "detailed":"detailed, explanatory and study-friendly",
        "exam":"exam-focused with definitions, distinctions, likely test points and common traps"
    }
    style=style_map.get(summary_style,style_map["standard"])

    prompt=f"""
You are LectureSift, an academic study-pack generator.
Use ONLY the transcript below. Do not invent facts that are absent.
Output language: {out_lang}.
Summary style: {style}.

Return VALID JSON ONLY with this schema:
{{
  "title": "string",
  "summary": "string",
  "key_points": ["string"],
  "notes": [
    {{"heading":"string","content":"string","bullets":["string"]}}
  ],
  "quiz": [
    {{"question":"string","options":["A","B","C","D"],"answer_index":0,"explanation":"string"}}
  ],
  "flashcards": [
    {{"front":"string","back":"string"}}
  ]
}}

Requirements:
- quiz must contain exactly {quiz_count} questions when source content supports it; otherwise as many non-redundant questions as justified.
- flashcards should contain up to {flashcard_count} useful cards.
- notes should preserve important terminology from the transcript.
- distinguish uncertain/unclear transcript statements rather than correcting them silently.
- no markdown fences.

TRANSCRIPT:
{transcript[:90000]}
"""
    resp=client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        response_format={"type":"json_object"},
        temperature=0.2
    )
    txt=resp.choices[0].message.content
    return safe_json(txt)

def process_job(job_id: str, video: Path, opts: dict):
    job=Path(JOBS[job_id]["job_dir"])
    slides_dir=job/"slides"
    started=time.time()
    try:
        jobset(job_id,status="working",percent=12,stage="preparing",started=started)

        slides,diagnostics=extract_slides(video,slides_dir,job_id)
        jobset(job_id,percent=50,stage="audio")

        if has_audio_stream(video):
            audio=extract_audio(video,job)
            jobset(job_id,percent=58,stage="transcription")
            transcript=transcribe(audio,opts["source_language"])
        else:
            transcript=""
        (job/"transcript.txt").write_text(transcript or "No audio track detected.",encoding="utf-8")

        jobset(job_id,percent=70,stage="study_notes")
        if transcript.strip():
            pack=make_study_pack(
                transcript,
                opts["output_language"],
                opts["summary_style"],
                opts["quiz_count"],
                opts["flashcard_count"]
            )
        else:
            pack={
                "title":"LectureSift",
                "summary":"No audio track was detected, so text-based study materials could not be generated.",
                "key_points":[],"notes":[],"quiz":[],"flashcards":[]
            }

        jobset(job_id,percent=88,stage="packaging")
        result={
            "job_id":job_id,
            "options":opts,
            "slides":slides,
            "diagnostics":diagnostics,
            "transcript":transcript,
            **pack
        }
        (job/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        (job/"summary.txt").write_text(pack.get("summary",""),encoding="utf-8")
        (job/"notes.txt").write_text(
            "\n\n".join(
                f"{x.get('heading','')}\n{x.get('content','')}\n" +
                "\n".join("• "+b for b in x.get("bullets",[]))
                for x in pack.get("notes",[])
            ),encoding="utf-8"
        )
        (job/"quiz.json").write_text(json.dumps(pack.get("quiz",[]),ensure_ascii=False,indent=2),encoding="utf-8")
        (job/"flashcards.json").write_text(json.dumps(pack.get("flashcards",[]),ensure_ascii=False,indent=2),encoding="utf-8")
        (job/"slides.json").write_text(json.dumps(slides,ensure_ascii=False,indent=2),encoding="utf-8")

        out=job/"pack"
        out.mkdir()
        for fn in ["transcript.txt","result.json","summary.txt","notes.txt","quiz.json","flashcards.json","slides.json"]:
            shutil.copy(job/fn,out/fn)
        if slides_dir.exists():
            shutil.copytree(slides_dir,out/"slides")

        zipbase=job/"LectureSift_Study_Pack"
        shutil.make_archive(str(zipbase),"zip",root_dir=out)
        elapsed=round(time.time()-started,1)
        jobset(
            job_id,status="done",percent=100,stage="done",
            elapsed_seconds=elapsed,result_path=str(zipbase)+".zip"
        )
    except Exception as e:
        print("PROCESS ERROR:",repr(e),flush=True)
        traceback.print_exc()
        jobset(job_id,status="error",percent=0,stage="error",error=str(e)[:1000])

@app.post("/jobs")
async def create_job(
    file: UploadFile=File(...),
    source_language: str=Form("auto"),
    output_language: str=Form("tr"),
    summary_style: str=Form("standard"),
    quiz_count: int=Form(10),
    flashcard_count: int=Form(20),
):
    ext=Path(file.filename or "video.mp4").suffix.lower()
    if ext not in {".mp4",".mov",".mkv",".webm",".mpeg",".mpg",".m4v"}:
        raise HTTPException(400,"Unsupported video format.")

    job_id=str(uuid.uuid4())
    job=WORK/job_id
    job.mkdir(parents=True,exist_ok=True)
    video=job/("input"+ext)

    with open(video,"wb") as out:
        while True:
            chunk=await file.read(1024*1024)
            if not chunk:
                break
            out.write(chunk)

    opts={
        "source_language":source_language,
        "output_language":output_language,
        "summary_style":summary_style,
        "quiz_count":max(3,min(int(quiz_count),30)),
        "flashcard_count":max(5,min(int(flashcard_count),60)),
    }
    JOBS[job_id]={
        "job_id":job_id,"status":"queued","percent":10,"stage":"queued",
        "created":time.time(),"job_dir":str(job),"options":opts
    }
    threading.Thread(target=process_job,args=(job_id,video,opts),daemon=True).start()
    return {"job_id":job_id,"status":"queued"}

@app.post("/jobs/url")
def create_url_job(
    video_url: str=Form(...),
    source_language: str=Form("auto"),
    output_language: str=Form("tr"),
    summary_style: str=Form("standard"),
    quiz_count: int=Form(10),
    flashcard_count: int=Form(20),
):
    url=validate_remote_url(video_url)
    job_id=str(uuid.uuid4())
    job=WORK/job_id
    job.mkdir(parents=True,exist_ok=True)
    opts={
        "source_language":source_language,
        "output_language":output_language,
        "summary_style":summary_style,
        "quiz_count":max(3,min(int(quiz_count),30)),
        "flashcard_count":max(5,min(int(flashcard_count),60)),
    }
    JOBS[job_id]={
        "job_id":job_id,"status":"working","percent":3,"stage":"downloading_url",
        "created":time.time(),"job_dir":str(job),"options":opts,"source_url":url
    }
    def worker():
        try:
            jobset(job_id,percent=5,stage="downloading_url")
            video=download_remote_video(url,job)
            jobset(job_id,percent=10,stage="queued")
            process_job(job_id,video,opts)
        except Exception as e:
            print("URL PROCESS ERROR:",repr(e),flush=True)
            traceback.print_exc()
            jobset(job_id,status="error",percent=0,stage="error",error=str(e)[:1000])
    threading.Thread(target=worker,daemon=True).start()
    return {"job_id":job_id,"status":"working"}
