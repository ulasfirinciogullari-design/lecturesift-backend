# LectureSift V4.1

LectureSift turns ordered uploaded lecture videos, audio recordings, documents,
and supported images into a study workspace: transcript, optional translation,
structured notes, summary, verified presentation slides, quiz, flashcards, and
selectable PDF/Word/TXT files. Local video sources can also produce an MP3
audio export.

## Current architecture

- `frontend/`: static Netlify interface
- `lecturesift/app.py`: FastAPI endpoints
- `lecturesift/pipeline.py`: parallel audio and visual processing
- `lecturesift/slides.py`: timestamp-only, low-memory slide detection
- `lecturesift/ai.py`: transcription, translation, and study-pack generation
- `lecturesift/exports.py`: PDF, Word, TXT, MP3, and ZIP exports

Production services:

- Frontend: <https://lecturesift.com/>
- Backend: <https://api.lecturesift.com/>

The production deployment stays on `main`.

Organic search baseline, content priorities and verification steps are tracked
in [SEO_CALISMA_PLANI.md](SEO_CALISMA_PLANI.md).

On the VPS, `/etc/lecturesift/runtime.env` is a root-side source of truth, not
a container environment file. Production preflight derives independent
root-only `api.env`, `worker.env`, and `instagram.env` files. This keeps admin,
session, payment, email, legal and Instagram secrets out of the processing
worker, while the scheduled Instagram studio job receives only its publishing,
OpenAI, R2 and ElevenLabs values and no database, payment or admin credentials. Host-only restic repository, storage and
encryption credentials are never copied into any application role. See
[VPS_DEPLOYMENT.md](VPS_DEPLOYMENT.md) for
the fail-closed generation and validation procedure.

PostgreSQL also uses separate logins: the API can mutate application rows but
cannot create schema objects, while the worker sees only masked entitlement
views and narrowly writable usage/guest/runtime paths. A one-shot maintenance
container, authenticated as the database owner, is the only application image
allowed to create or update tables. Startup fails if either runtime role gains
schema creation, direct payment/auth/admin/contact access, or shares a password.

Long-running production media jobs require the separate queue, worker, and private object-storage setup described in [DURABLE_PROCESSING.md](DURABLE_PROCESSING.md). Do not enable paid processing until its health and restart-recovery checks pass in preview.

## Local development

Requirements: Python 3.11+ (3.12 recommended), FFmpeg/FFprobe, Tesseract OCR with English and Turkish language data, system DejaVu fonts on Linux (Windows uses Arial), and an OpenAI API key for real AI processing.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
export OPENAI_API_KEY="..."
uvicorn main:app --reload
```

On Windows, the repository includes a complete environment and test check after `.venv` is created:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_windows.ps1
```

Use `-SkipTests` to check installed packages, FFmpeg/FFprobe, OCR languages, 7-Zip, and optional NVIDIA encoders without running pytest. The script never prints application secrets.

Open `frontend/index.html` through a static web server. The frontend currently points to the production backend; change the `API` constant in `frontend/app.js` for local API testing.

## Tests

```bash
pytest -q
node --check frontend/app.js
```

The automated suite covers human-readable API errors, SSRF/private-URL rejection, PDF-only default packaging, selectable Word/TXT outputs, ordered multi-source routing, legacy separate audio/visual compatibility, MP3 merging, slide-vs-scene classification, WebM timestamp accuracy, and genuine-slide preservation.

## Main API routes

- `POST /jobs`: upload ordered sources in `files`. The paired `audio_files` plus
  `visual_files` transport remains temporarily available only for compatibility
  with released clients; the website no longer offers separate audio/visual
  choices. Ambiguous mixtures fail with `LS-UPLOAD-06` instead of discarding a
  supplied source list.
- `POST /jobs/url`: retired-client tombstone; excluded from OpenAPI and always returns HTTP 410 with `LS-URL-06`
- `GET /jobs/{job_id}`: live progress
- `GET /jobs/{job_id}/result`: structured result
- `GET /jobs/{job_id}/artifact/{filename}`: individual PDF/Word/TXT/MP3 output
- `GET /jobs/{job_id}/download`: complete ZIP package
- `GET /health`: deployment health, engine version, and exact 40-hex build revision (`unknown` when unverified)
- `GET /instagram/health`: verify the configured Instagram account connection
- `POST /instagram/media`: create an image, video, Reel, or Story media container
- `GET /instagram/media/{container_id}`: inspect container processing status
- `POST /instagram/media/publish`: publish a ready media container
- `GET /instagram/daily/reel/{date}.mp4`: serve the cached 9:16 video with topic-specific narration and original background music
- `GET /instagram/daily/plan?day=YYYY-MM-DD`: preview the next caption and photo/Reel format

## Billing and hosted card payments

Account, bank-transfer, credit-pack, and admin approval data is stored in the configured PostgreSQL database. iyzico is the preferred card provider when the runtime-only `IYZICO_API_KEY` and `IYZICO_SECRET_KEY` values are present. Production uses `IYZICO_BASE_URL=https://api.iyzipay.com`; sandbox testing may explicitly use `https://sandbox-api.iyzipay.com`. PayTR remains an optional fallback. Direct IBAN orders use `BILLING_BANK_IBAN`, `BILLING_BANK_ACCOUNT_HOLDER`, and `BILLING_BANK_NAME`; they remain pending until an administrator verifies the bank movement and approves or rejects the unique order reference.

Verified accounts listed in `LECTURESIFT_ADMIN_EMAILS` (comma-separated, case-insensitive) open `/admin.html` with their password plus a six-digit code emailed to that address. The code expires after 10 minutes and five wrong attempts; the admin session lasts 12 hours and ends on sign-out, a password change or session revocation. The independent `ADMIN_ADMIN` secret remains the emergency key. Normal user sessions, legal contact addresses, billing credentials, and Instagram credentials never grant admin access. Neither credential is committed, and the panel keeps its session only for the current browser tab.

Visitors without an account get one guest trial per browser of up to `LECTURESIFT_GUEST_TRIAL_MINUTES` (default 30) through `POST /billing/guest-session`. Reopening an existing trial on the same device is always allowed. New trials are limited to `LECTURESIFT_GUEST_TRIAL_IP_DAILY_LIMIT` (default 3) per client IP (an IPv6 /64 counts as one client) and `LECTURESIFT_GUEST_TRIAL_GLOBAL_DAILY_LIMIT` (default 100; `0` pauses new trials) across the service per UTC day. The client IP is the address Caddy writes into `X-Forwarded-For`. The global count is read from the stored guest-trial rows, so a restart does not reset it. Either limit returns HTTP 429 `LS-GUEST-06` with `Retry-After`, and the workspace then links to free registration.

Every job can independently request precise transcript timestamps and automatic speaker separation. The fast default uses clearly labelled audio-chunk start estimates; precise modes use `gpt-4o-transcribe-diarize` and preserve provider-reported segment boundaries. Speaker identities are scoped to one provider request, so equal labels in separate long-audio chunks are never silently assumed to be the same person. `LECTURESIFT_PRECISE_TRANSCRIPT_TIMESTAMPS=true` remains only as a legacy default for already queued jobs; new web requests carry explicit per-job choices.

Configure the PayTR notification URL as:

`https://api.lecturesift.com/billing/paytr/callback`

The callback validates PayTR's HMAC signature and the original order amount, and processes repeated notifications idempotently. It must remain public and return plain `OK` only after the verified order state has been recorded. Never commit PayTR credentials or include them in logs.

iyzico Checkout Form uses the application-generated callback URL under `/billing/iyzico/callback`. Every API request uses IYZWSv2 HMAC-SHA256 authentication. Initialize and retrieve response signatures, provider token, order reference, currency, basket amount, and paid amount must all match before a plan or credit pack is activated. Never commit or log iyzico credentials.

Protected Bank Transfer/EFT is separate from the direct-IBAN/manual-review flow. It uses the same Checkout Form only after iyzico explicitly confirms that payment method on the merchant account. Configure the Merchant Notifications URL as `https://api.lecturesift.com/billing/iyzico/webhook` and ask iyzico to enable both Bank Transfer/EFT and `X-IYZ-SIGNATURE-V3`. Set `IYZICO_BANK_TRANSFER_ENABLED=true` only after that separate activation is confirmed. iyzico does not document a Checkout Form initialize field that preselects the transfer tab; the product UI must tell the buyer to choose the visible Bank Transfer/EFT tab instead of modifying the hosted URL or DOM. The webhook verifies the ordered HPP signature fields, performs a second server-to-server Checkout Form retrieve, and activates access only when `paymentStatus` is `SUCCESS`; `INIT_BANK_TRANSFER` remains pending. Signature activation alone must not be treated as protected bank-transfer activation.

Instagram credentials are read only from `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_ACCOUNT_ID`, and
`INSTAGRAM_APP_SECRET`. Publishing routes additionally require `INSTAGRAM_ADMIN_TOKEN` as a Bearer
token, so they remain disabled by default. Never place any of these values in source control.

Transient job files are stored under `/tmp/lecturesift` and expire automatically.
