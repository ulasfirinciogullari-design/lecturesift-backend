# LectureSift V4.1

LectureSift turns ordered lecture recordings—or separate ordered audio and slide recordings—into a study workspace: transcript, optional translation, structured notes, summary, verified presentation slides, quiz, flashcards, and selectable PDF/Word/TXT files. It can also merge video audio into one MP3 or prepare a downloadable video from a supported URL.

## Current architecture

- `frontend/`: static Netlify interface
- `lecturesift/app.py`: FastAPI endpoints
- `lecturesift/pipeline.py`: parallel audio and visual processing
- `lecturesift/slides.py`: timestamp-only, low-memory slide detection
- `lecturesift/ai.py`: transcription, translation, and study-pack generation
- `lecturesift/exports.py`: PDF, Word, TXT, MP3/video, and ZIP exports

Production services:

- Frontend: <https://clever-horse-22b1a8.netlify.app/>
- Backend: <https://lecturesift-backend.onrender.com/>

The production deployment stays on `main`.

## Local development

Requirements: Python 3.11+, FFmpeg, system DejaVu fonts, and an OpenAI API key.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="..."
uvicorn main:app --reload
```

Open `frontend/index.html` through a static web server. The frontend currently points to the production backend; change the `API` constant in `frontend/app.js` for local API testing.

## Tests

```bash
pytest -q
node --check frontend/app.js
```

The automated suite covers human-readable API errors, SSRF/private-URL rejection, PDF-only default packaging, selectable Word/TXT outputs, ordered multi-source routing, separate audio/visual routing, MP3 merging, slide-vs-scene classification, WebM timestamp accuracy, and genuine-slide preservation.

## Main API routes

- `POST /jobs`: upload ordered `files`, or ordered `audio_files` plus `visual_files`
- `POST /jobs/url`: submit a supported video/page URL for study-pack creation, MP3 conversion, or video download
- `GET /jobs/{job_id}`: live progress
- `GET /jobs/{job_id}/result`: structured result
- `GET /jobs/{job_id}/artifact/{filename}`: individual PDF/Word/TXT/MP3/video output
- `GET /jobs/{job_id}/download`: complete ZIP package
- `GET /health`: deployment health and engine version

Transient job files are stored under `/tmp/lecturesift` and expire automatically.
