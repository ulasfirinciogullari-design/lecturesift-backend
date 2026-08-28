# LectureSift durable media processing

This runbook keeps long video jobs recoverable across deploys and service restarts. Never place credentials in this repository or in build logs.

## Required services

1. A persistent Render Key Value instance in the same region as the API, configured with `noeviction` and journal + snapshot persistence.
2. The `lecturesift-worker` Render background worker from `render.yaml`.
3. A private S3-compatible object-storage bucket. Cloudflare R2 Standard is the preferred initial provider because it is S3-compatible and includes a small free allowance.

Confirm the current prices in the provider dashboards before creating paid resources. Render's smallest continuously running worker and persistent Key Value instance are recurring charges. R2 is usage-based after its monthly free allowance.

## Private R2 configuration

- Bucket name: choose a private, non-public bucket.
- Region: `auto`.
- Lifecycle safety rule: delete objects with the `jobs/` prefix after 731 days. Application cleanup normally removes original sources immediately after successful processing and removes results when their account retention expires (1-730 days depending on the plan); the bucket rule is only a final safety net beyond the longest plan retention.
- API token: restrict it to object read/write access for this bucket only.
- Do not enable an `r2.dev` public URL or public custom domain.

Store these values in both the Render web service and worker environments:

- `S3_ENDPOINT_URL=https://<cloudflare-account-id>.r2.cloudflarestorage.com`
- `S3_REGION=auto`
- `S3_BUCKET=<private-bucket-name>`
- `S3_ACCESS_KEY_ID=<scoped-access-key>`
- `S3_SECRET_ACCESS_KEY=<scoped-secret-key>`

Render wires `CELERY_BROKER_URL` and `REDIS_URL` from the private Key Value service. `LECTURESIFT_REQUIRE_DURABLE_PROCESSING=true` prevents a production request from silently falling back to a restart-sensitive in-process thread when the queue, worker, or storage is unavailable.

## Release verification

1. Deploy to the test environment first.
2. Confirm `/rollout/health` reports connected queue and storage, at least one reachable worker, and `durable_processing_ready: true`.
3. Submit a small authenticated test video and confirm it reaches `done` through `queue_mode: celery`.
4. Restart the web service during a second test job and confirm the job remains visible and completes.
5. Confirm source objects under `jobs/<job-id>/sources/` are deleted after success.
6. Confirm the output can be downloaded only with the owning user's authenticated session.
7. Only then promote the tested commit to `main`.
