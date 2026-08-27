# LectureSift V4

LectureSift turns a lecture video into a study workspace: original and translated transcripts, structured notes, summary, verified presentation slides, quiz, flashcards, and downloadable PDF/TXT files.

## Current architecture

- `frontend/`: static Netlify interface
- `lecturesift/app.py`: FastAPI endpoints
- `lecturesift/pipeline.py`: parallel audio and visual processing
- `lecturesift/slides.py`: timestamp-only, low-memory slide detection
- `lecturesift/ai.py`: transcription, translation, and study-pack generation
- `lecturesift/exports.py`: PDF, TXT, JSON, and ZIP exports

Production services:

- Frontend: <https://clever-horse-22b1a8.netlify.app/>
- Backend: <https://lecturesift-backend.onrender.com/>

The production deployment stays on `main`. V4 work should first be tested from a separate branch.

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

The automated suite covers human-readable API errors, SSRF/private-URL rejection, PDF/TXT/ZIP packaging, slide-vs-scene classification, classroom/person-band and textured-office rejection, WebM timestamp accuracy, a no-audio natural-scene video that must return zero slides, and a short genuine slide that must be preserved.

## Main API routes

- `POST /jobs`: upload a video
- `POST /jobs/url`: submit a supported video/page URL
- `GET /jobs/{job_id}`: live progress
- `GET /jobs/{job_id}/result`: structured result
- `GET /jobs/{job_id}/artifact/{filename}`: individual PDF/TXT output
- `GET /jobs/{job_id}/download`: complete ZIP package
- `GET /health`: deployment health and engine version

Transient job files are stored under `/tmp/lecturesift` and expire automatically.
