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

## Uptime alert

`.github/workflows/uptime.yml` checks the site from outside every 10 minutes
(and on manual dispatch): `https://lecturesift.com/` must return 200 with the
homepage hero, and `https://api.lecturesift.com/health` must return 200 with
`ok: true` and a version. A failing address is retried twice about 30 seconds
apart before it counts as down. A confirmed failure opens one issue labelled
`outage` ("Site erişilemiyor: ...") that mentions the repository owner, so
GitHub emails them; later runs comment only when the set of failing addresses
changes, and the first healthy run comments "Site yeniden erişilebilir" and
closes the issue. It uses only the workflow's `GITHUB_TOKEN` (`issues: write`,
`contents: read`). GitHub runs scheduled workflows from `main` only, may delay
them under load, and pauses them in public repositories after 60 days without
repository activity; re-enable it from the Actions tab if that happens.

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

Signup asks only for email, password and the existing consent. Name, surname and phone are optional on the account; the plans checkout collects whatever the selected provider requires (iyzico: name, surname, GSM number, address, city, zip code; PayTR: name, address, phone) and `/billing/checkout` refuses a missing field with `LS-PAY-03` and its `missing_fields` before any order is created. Buyer data is never filled in for the customer. A guest who signs up (or signs in) in the same browser tab sends the signed guest session token; the server then moves that guest's results and library placements to the account once, after the email is verified. Usage minutes stay with the guest trial.

"Google ile devam et" is optional and off by default. To enable it, create an OAuth 2.0 client of type "Web application" in Google Cloud Console (APIs & Services → Credentials), add exactly `https://lecturesift.com` under Authorized JavaScript origins, and set its public client id as `GOOGLE_CLIENT_ID` on the API. No client secret is used. With `GOOGLE_CLIENT_ID` empty the button never appears and `/billing/google` returns 404. The API verifies each ID token with Google's tokeninfo endpoint (token sent in a POST body) and also checks the audience, issuer, expiry and `email_verified`. A new address gets a verified account. The Google subject id (`sub`) is stored as a `google_link` row in `billing_auth_tokens` (no schema change) and later sign-ins match on it. An existing account with the same email is linked only when Google is authoritative for the address (`gmail.com`/`googlemail.com`, or an `hd` claim equal to the email domain), and the owner gets an email when that link is made; for other addresses the API answers 409 `LS-GOOGLE-04` and the person signs in with the password. A password reset removes Google links. `/billing/google` is limited per client and by a global ceiling. `GET /billing/auth-config` publishes the client id and signup fields to the static pages.

A signed-in person can link a Google login whose address differs from the account's under Account → Security. `POST /billing/me/google/link` takes a Google ID token, verified exactly as for sign-in, plus the current password; `POST /billing/me/google/unlink` takes the current password and removes every link of the account. A Google login linked to another account moves to the current one. The owner is emailed on every link and unlink, and the other account's owner when a link moves away. An account created through Google has a random password, so its owner first sets one with the emailed password reset (which also removes its Google links) and then links again. Both endpoints share per-account and per-network limits, and linking also counts toward the Google ceiling. `/billing/me` reports `google_linked`.

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

## Product analytics (GA4)

Page scripts send GA4 funnel events only through `window.lectureSiftTrack(name, params)` in `frontend/analytics.js`. It sends nothing on previews or local origins, before the visitor allows analytics, for unknown event names, or on pages outside the funnel. It drops unknown parameters and any text that could be an email address, URL, path or file name. The workspace still loads no Google tag when it opens; its first funnel event loads GA4 only (no page view, no Google Ads tag), with the URL query and fragment removed.

| Event | Sent when | Parameters |
| --- | --- | --- |
| `trial_start` | The API accepts a guest's first upload | `source_type` (`document` or `media`) |
| `trial_complete` | That guest job finishes and its result is shown | `source_type` |
| `trial_limit_reached` | A guest sees the used-trial sign-up wall (on return, on trying again or on `LS-GUEST-05`), or a source exceeds the trial allowance (`LS-GUEST-04`) | `reason` (`trial_used` or `source_limit`) |
| `sign_up` | Registration is accepted, before email verification | `method` |
| `login` | Password sign-in succeeds | `method` |
| `begin_checkout` | A plan's checkout form opens on the plans page | `currency`, `value`, `items` (`item_id` is the plan code) |
| `add_payment_info` | Hosted checkout or a manual bank-transfer order starts | `payment_type`, `items` |
| `purchase` | The account page, where payments return, confirms the order as paid; once per order per browser tab | `transaction_id` (random order reference), `value`, `currency`, `items` |
| `result_download` | A result package or file download starts | `content_type` (`package` or `file`), `file_extension` |

Each trial event is sent at most once per page load, and `trial_complete` at most once per browser tab, including when the guest comes back to a job that finished while they were away. An event counts as sent only when the tag accepts it, and an event fired before the consent script is ready waits for the visitor's saved choice. In GA4 Admin, mark `trial_start`, `sign_up` and `purchase` as key events. To see `source_type`, `reason`, `content_type` or `file_extension` in reports, register them as event-scoped custom dimensions. The site is not a single-page app, so "Page changes based on browser history events" can be switched off in the web stream's Enhanced measurement settings; otherwise tab switches on the account and workspace pages, which rewrite the URL fragment, can count as page views.
