# LectureSift Backup and Recovery Policy

This policy separates source recovery from user-data recovery. Database dumps,
uploaded media, generated study packs, access tokens, and environment secrets
must never be copied into Git history or GitHub workflow artifacts.

## 1. Source and deployment recovery

1. `main` retains the full Git history used by Netlify and Render.
2. `backup/latest` is refreshed after every push to `main` and once per day.
3. `backup/stable` points to the latest manually verified production release and
   is moved only after live checks pass.
4. The backup workflow creates repository and frontend-only archives. GitHub
   retains each archive for 90 days.

Before every production release, run the complete tests, publish to `main`,
verify the Netlify interface and Render health endpoint, then move
`backup/stable` only after both checks pass.

## 2. Account, order, and subscription recovery

The paid managed Render Postgres database is the source of truth. Render's
point-in-time recovery must be enabled and checked on the database Recovery
page. A recovery creates an isolated replacement database; validate that copy
before reconnecting services. Never restore over the only production copy.

After confirming the current recovery window in Render, set
`LECTURESIFT_DATABASE_RECOVERY_CONFIRMED=true` on the backend. Reconfirm it
after changing the database plan, workspace plan, region, or provider.

## 3. Queue and file recovery

The Render Key Value service is a processing queue, not the source of truth.
Use `Journal + Snapshot`; a lost queued job may be retried from its persistent
job record and private object storage.

Uploaded media and generated study packs stay in a private S3-compatible bucket
and follow each plan's retention period. Configure the bucket's lifecycle rules,
verify that expired objects are removed, and set
`LECTURESIFT_OBJECT_RETENTION_CONFIRMED=true`. Do not make the bucket public.

## 4. Restore drills

At least quarterly and before a high-risk migration:

1. restore a non-production Postgres copy to a recent timestamp;
2. verify user, order, subscription, credit, and audit counts;
3. download one test study pack through the application, not a public bucket URL;
4. verify a queued test job can be retried safely;
5. record the date, operator, result, and follow-up action outside the repository;
6. set `LECTURESIFT_RECOVERY_DRILL_CONFIRMED=true` only after the drill passes.

The admin readiness panel displays these three confirmations. They are operator
attestations, not substitutes for actually performing the checks.

If a code release fails, restore from `backup/stable`. If data is damaged, use
an isolated point-in-time database recovery and contact Render support when
needed. Rotate any credential that might have been exposed during an incident.

Initial verified source recovery point: LectureSift V4.1
(`0e1c8d29e880c1836866ba8eab33a227e402b64b`).
