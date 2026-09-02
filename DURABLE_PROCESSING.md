# LectureSift durable media processing

This runbook keeps long video jobs recoverable across deploys and service restarts. Never place credentials in this repository or in build logs.

## Required services

The OVH production target runs the API, Celery worker, PostgreSQL and Redis 7.4
inside the private Compose networks in `compose.yaml`. Redis uses `noeviction`
and persistent AOF/RDB storage; only Caddy is public. A private S3-compatible
object-storage bucket (Cloudflare R2) remains authoritative for uploaded
sources and generated artifacts. Render remains only the temporary rollback
source until the cutover in `VPS_DEPLOYMENT.md` is completed.

## Private R2 configuration

- Bucket name: choose a private, non-public bucket.
- Region: `auto`.
- Lifecycle safety rule: delete objects with the `jobs/` prefix after 731 days. Application cleanup normally removes original sources immediately after successful processing and removes results when their account retention expires (1-730 days depending on the plan); the bucket rule is only a final safety net beyond the longest plan retention.
- API token: restrict it to object read/write access for this bucket only.
- Do not enable an `r2.dev` public URL or public custom domain.

Store these values in root-owned `/etc/lecturesift/runtime.env`. The
`deploy/generate_role_envs.py` gate derives separate root-only API and worker
environment files; never mount the master environment or host-only restic
credentials into a container:

- `S3_ENDPOINT_URL=https://<cloudflare-account-id>.r2.cloudflarestorage.com`
- `S3_REGION=auto`
- `S3_BUCKET=<private-bucket-name>`
- `S3_ACCESS_KEY_ID=<scoped-access-key>`
- `S3_SECRET_ACCESS_KEY=<scoped-secret-key>`

The same gate rewrites `LECTURESIFT_WORKER_DATABASE_URL` to `DATABASE_URL`
only inside `worker.env`; it never copies that login into `api.env`. The worker
role has no direct privilege on public tables. Its search path resolves masked
views for plan/credit decisions and grants only INSERT on usage/runtime metrics,
UPDATE on credit balance and guest reservation fields, and the SELECT columns
required by those paths. Password hashes, auth tokens, raw payment orders,
subscriptions outside entitlement fields, admin/audit records and contact
messages remain inaccessible. Rotate the API and worker passwords separately,
update both source URLs, then use `systemctl reload lecturesift`; preflight and
the post-migration privilege probe fail closed on any mismatch.

Set `CELERY_BROKER_URL` and `REDIS_URL` to the private Compose Redis service.
`LECTURESIFT_REQUIRE_DURABLE_PROCESSING=true` prevents a production request
from silently falling back to a restart-sensitive in-process thread when the
queue, worker, or storage is unavailable.

## Release verification

1. Run the isolated restore/application/format rehearsal first.
2. Confirm `/rollout/health` reports connected queue and storage, at least one reachable worker, and `durable_processing_ready: true`.
3. Submit a small authenticated test video and confirm it reaches `done` through `queue_mode: celery`.
4. Reload the VPS service during a second test job and confirm the job remains visible and completes.
5. Confirm source objects under `jobs/<job-id>/sources/` are deleted after success.
6. Confirm the output can be downloaded only with the owning user's authenticated session.
7. Only then promote the tested commit to `main`.
