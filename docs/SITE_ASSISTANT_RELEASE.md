# Site assistant

Chat and assistant credit sales were activated on Render on September 9, 2026,
after the product schema, recovery contract, separate runtime roles and real
provider access were verified. The live guest endpoint returned a Turkish
answer and registration action. See [activation evidence](PRODUCT_ACTIVATION.md)
for the exact release and the checks that remain unverified live.

The assistant uses the existing verified billing session. Server-authored context
contains the site map, only that user's plan/minutes and recent lesson titles,
and optionally an owned completed lesson summary. Allowlisted navigation/theme
proposals require a click. Purchases, cancellation, refunds and closure use the
existing account/checkout screens. Model output cannot execute arbitrary APIs.

Guests receive at most three short AI replies daily, instructed to use 3–5
sentences and invite signup. Daily keyed address digests and a durable global
spend ceiling bound this trial. Responses use plain text and translated page
names; navigation and registration buttons are rendered by the site. When
capability is off, the widget reports that personal AI chat is unavailable.

Verified accounts receive 50 welcome credits once, valid for 365 days. New
subscription snapshots include Lite 500 / Plus 1,500 / Pro 4,000 / Max 10,000
credits per allowance period. Annual purchases grant one month at a time;
expired/replaced allowances do not carry over. Prior snapshots retain their
minute rights; a missing assistant-credit field means zero included credits.

ai_1000 / ai_3000 / ai_10000 are distinct one-time purchases, using existing
consent, immutable price snapshots, verified callbacks and manual reconciliation.
They grant neither minutes, downloads, subscription replacement nor referrals.
Top-ups expire 365 days after paid completion. Refund requests freeze the
associated credit grant; a rejected refund restores unused access.

## Model, media and accounting

Reviewed model: gpt-5.6-luna, Responses API, reasoning none, no hosted tools,
600 output-token ceiling, one attempt, 45-second timeout. No silent expensive
model upgrade. Real release-account text access and the live guest route were
verified separately; automated checks use a synthetic provider without paid API calls.

Credits = ceil((input tokens + 6 × output tokens)/1,000), minimum one per answer.
History and image input count. A conservative amount is reserved first; actual
usage settles it. Owner-row locks serialize replicas. Request IDs bind owner and
payload, preventing double calls/charges. Failed answers restore customer credit;
unknown provider cost remains in the platform ceiling. Lost reservations expire
after two minutes. The daily ceiling is 100,000 weighted credits ($20 at the
reviewed model rate), shared with guest trials. Re-review on model/rate changes.

JPEG/PNG/WebP are resized in the browser and decoded/re-encoded on the server,
at most 512 pixels per side. Remote URLs, SVG, animation and oversized inputs
are rejected. Videos up to 60 seconds / 50 MB become three sampled frames in
the browser; the UI and prompt explicitly say visuals only, without audio.
Full video/audio analysis uses Workspace with existing minute/job limits.
Video generation is not implemented: chat can supply prompts/storyboards
and discuss images or lesson results. No production media processing was tested.

Input text/images are not stored by the assistant. Output may be cached for
15 minutes for idempotent retry; wallet access clears expired cached responses.
Browser chat lives in memory and resets on account change, new chat or page
close. `store:false` avoids Responses application-state storage, not all provider
abuse-monitoring retention. Users are told inputs go to OpenAI. Export and both
closure paths cover assistant ledgers even when disabled. The source maintenance entry point `python -m deploy.assistant_cache_prune` clears
idle answer caches after 15 minutes and guest digests older than two complete UTC
days, in batches of at most 1,000 each. It retains credit balances, request IDs and
global spend totals, and still runs if chat is later disabled. A false schema
release capability makes it inert. The API lifespan now invokes the same bounded maintenance every minute. Multiple
replicas may run it safely; feature disablement does not stop retention after the
schema capability is opened. Errors and full batches emit non-secret operational
alerts. This schedule is included in the deployed API lifespan.
The interval means normal scheduled deletion can occur up to one minute after
cache expiry, or later if maintenance fails; this requires operational monitoring.

## Release contract

`assistant_catalog.SCHEMA_RECOVERY_RELEASE_READY` and the API's
`ASSISTANT_ENABLED` are both true after the verified release. Both are required.
Requests never create schema. The
three versioned tables in `assistant_wallet.METADATA` are:

- assistant_credit_grants_v1: owned grants and dated remaining balances.
- assistant_credit_requests_v1: reservations, request digests, usage, answer cache.
- assistant_daily_budget_v1: global daily spend and keyed guest counters.

The versioned integrity, role and backup/recovery contracts include these exact
columns and indexes. Synthetic PostgreSQL 18 checks cover restored balances,
in-flight reservations, refund races, erasure and credit SKU report labels.
Production migration separately verified preservation of every existing row and
schema object, followed by real API/worker authority checks and managed backup
evidence. Do not create tables ad hoc or run the older core-only cutover scripts
against the expanded schema. A real paid top-up has not been placed as a test.

Official sources reviewed 2026-09-08:

- https://developers.openai.com/api/docs/models/gpt-5.6-luna
- https://developers.openai.com/api/docs/guides/images-vision
- https://developers.openai.com/api/docs/guides/structured-outputs


Image creation is enabled as a separate authenticated action after its real
provider check. See [the image release contract](ASSISTANT_IMAGE_RELEASE.md).
Text chat does not silently invoke paid image creation. Video generation remains
unavailable; video attachment analysis samples visual frames only.
