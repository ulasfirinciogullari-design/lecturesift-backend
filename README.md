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

Long-running production media jobs require the separate queue, worker, and private object-storage setup described in [DURABLE_PROCESSING.md](DURABLE_PROCESSING.md). Do not enable paid processing until its health and restart-recovery checks pass in preview.

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
- `GET /instagram/daily/reel/{date}.mp4`: serve the cached 9:16 video used by the daily cloud scheduler

## Billing and hosted card payments

Account, bank-transfer, credit-pack, and admin approval data is stored in the configured PostgreSQL database. iyzico is the preferred card provider when the runtime-only `IYZICO_API_KEY` and `IYZICO_SECRET_KEY` values are present. Production uses `IYZICO_BASE_URL=https://api.iyzipay.com`; sandbox testing may explicitly use `https://sandbox-api.iyzipay.com`. PayTR remains an optional fallback. Direct IBAN orders use `BILLING_BANK_IBAN`, `BILLING_BANK_ACCOUNT_HOLDER`, and `BILLING_BANK_NAME`; they remain pending until an administrator verifies the bank movement and approves or rejects the unique order reference.

Set the independent `ADMIN_ADMIN` secret in Render to open `/admin.html`. Normal user sessions, legal contact addresses, billing credentials, and Instagram credentials never grant admin access. The secret must never be committed and is retained only for the current browser-tab session.

Transcript timelines always include clearly labeled audio-chunk start times. Set `LECTURESIFT_PRECISE_TRANSCRIPT_TIMESTAMPS=true` on both the web service and worker only after approving the higher transcription cost; this switches transcription to `gpt-4o-transcribe-diarize` and stores provider-reported segment start/end times and speaker labels. The safe default is `false`.

Configure the PayTR notification URL as:

`https://lecturesift-backend.onrender.com/billing/paytr/callback`

The callback validates PayTR's HMAC signature and the original order amount, and processes repeated notifications idempotently. It must remain public and return plain `OK` only after the verified order state has been recorded. Never commit PayTR credentials or include them in logs.

iyzico Checkout Form uses the application-generated callback URL under `/billing/iyzico/callback`. Every API request uses IYZWSv2 HMAC-SHA256 authentication. Initialize and retrieve response signatures, provider token, order reference, currency, basket amount, and paid amount must all match before a plan or credit pack is activated. Never commit or log iyzico credentials.

Protected Bank Transfer/EFT is separate from the direct-IBAN/manual-review flow. It uses the same Checkout Form only after iyzico explicitly confirms that payment method on the merchant account. Configure the Merchant Notifications URL as `https://lecturesift-backend.onrender.com/billing/iyzico/webhook` and ask iyzico to enable both Bank Transfer/EFT and `X-IYZ-SIGNATURE-V3`. Set `IYZICO_BANK_TRANSFER_ENABLED=true` only after that separate activation is confirmed. The webhook verifies the ordered HPP signature fields, performs a second server-to-server Checkout Form retrieve, and activates access only when `paymentStatus` is `SUCCESS`; `INIT_BANK_TRANSFER` remains pending. Signature activation alone must not be treated as protected bank-transfer activation.

Instagram credentials are read only from `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_ACCOUNT_ID`, and
`INSTAGRAM_APP_SECRET`. Publishing routes additionally require `INSTAGRAM_ADMIN_TOKEN` as a Bearer
token, so they remain disabled by default. Never place any of these values in source control.

Transient job files are stored under `/tmp/lecturesift` and expire automatically.

