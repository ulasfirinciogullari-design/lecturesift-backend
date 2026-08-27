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

## Email verification

Production registration can require a one-time email code delivered through Resend. The verification state is stored in the billing database; codes are HMAC-hashed, expire after 10 minutes, are single-use, and are protected by resend and attempt limits. Existing accounts created before activation remain valid.

Required production variables:

- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL` (recommended: `LectureSift <no-reply@mail.lecturesift.com>`)
- `EMAIL_VERIFICATION_SECRET`
- `EMAIL_VERIFICATION_REQUIRED=true` after the sending domain is verified

Optional controls are `EMAIL_VERIFICATION_TTL_SECONDS`, `EMAIL_VERIFICATION_RESEND_SECONDS`, `EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR`, and `EMAIL_VERIFICATION_MAX_ATTEMPTS`. Health is available at `GET /billing/email/health`.

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
- `GET /instagram/health`: verify the configured Instagram account connection
- `POST /instagram/media`: create an image, Reel, or Story media container
- `GET /instagram/media/{container_id}`: inspect container processing status
- `POST /instagram/media/publish`: publish a ready media container

Instagram credentials are read only from `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_ACCOUNT_ID`, and
`INSTAGRAM_APP_SECRET`. Publishing routes additionally require `INSTAGRAM_ADMIN_TOKEN` as a Bearer
token, so they remain disabled by default. Never place any of these values in source control.

Transient job files are stored under `/tmp/lecturesift` and expire automatically.

