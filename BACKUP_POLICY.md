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

After the OVH cutover, the private Compose PostgreSQL service is the source of
truth. `deploy/backup.sh` places the API in drain mode, stops the worker after
the queue is empty, and captures PostgreSQL plus Redis as one quiescent backup
set. It records an immutable versioned schema/data manifest and sends the
checksummed set to the off-site S3-compatible restic repository. A local VPS
copy alone is not a production backup. PostgreSQL dump and manifest import the
same exported MVCC snapshot, so a concurrently arriving billing callback
cannot bind metadata to a different point in time. The backup also refuses to
start without capacity for both staging copies plus 5 GiB of host reserve.

`LECTURESIFT_DATABASE_RECOVERY_CONFIRMED=true` is only a configuration
prerequisite. Normal startup additionally requires a current restic snapshot,
repository-bound recovery evidence and a successful isolated restore drill;
the boolean cannot substitute for those executable gates. During the cutover
window, Render PostgreSQL/PITR remains a rollback source only. Do not mix the
Render provider-migration path with the VPS Redis 7.4 disaster-recovery path.

The off-site repository is the dedicated EU R2 bucket
`lecturesift-production-backups` under the `/restic` prefix, never the live
application-object bucket. R2 retains `restic/data/` and `restic/snapshots/`
for 90 days and retains `restic/config` plus `restic/keys/` indefinitely;
repository locks and indexes remain writable. The backup policy keeps every
snapshot for 92 days before `forget` can select it. Automated prune is disabled
because it may delete or repack a still-locked young data pack independently of
snapshot age. Space reclamation uses a reviewed, fully aged prefix-rotation
procedure only after the replacement repository has passed a restore drill.

Every off-site snapshot also contains a versioned configuration package. It is
created only in the private ephemeral Restic staging directory, after the
unencrypted local PostgreSQL/Redis checkpoint is complete, and is encrypted on
the client before it leaves the host. The package is limited to the six exact
root-owned mode-`0600` environment files (`runtime.env`, `api.env`,
`worker.env`, `instagram.env`, `postgres.env`, and `restic.env`) plus an exact
allowlist of non-secret deployment identity files, including the one-line
root-only `/run/lecturesift/release.env` commit marker. It never walks `/etc`,
copies `rehearsal.env`, `.git`, Docker credentials, or application data. A
manifest records paths, source modes and SHA-256 hashes without recording or
printing any environment value. The ephemeral plaintext stage is removed on
success and failure; configuration is not added to the seven-day local backup.

## 3. Queue and file recovery

The private Compose Redis 7.4 service holds the durable job-state/queue
coordination data and uses AOF plus RDB persistence. `deploy/backup.sh` captures
a verified same-version RDB only after the application is quiescent. Never
import a Render/Valkey RDB into this service; provider migration uses the
frozen logical JSON migration documented in `VPS_DEPLOYMENT.md`.

Uploaded media and generated study packs stay in a private S3-compatible bucket
and follow each plan's retention period. Configure the bucket's lifecycle rules,
verify that expired objects are removed, and set
`LECTURESIFT_OBJECT_RETENTION_CONFIRMED=true`. Do not make the bucket public.

## 4. Restore drills and escrow

The latest off-site production snapshot must never be more than 48 hours old.
At least every 90 days and before a high-risk change, run
`sudo deploy/restic_restore_rehearsal.sh`. It restores the latest tagged backup
into network-isolated, dynamically sized disposable storage; verifies the
checksums, PostgreSQL database/schema/data fingerprints and Redis RDB; removes
the payload; and writes only root-owned non-secret evidence under
`/var/lib/lecturesift/recovery-drills/`.
The drill also verifies the exact configuration manifest, file set,
permissions and hashes before recording success. To recover configuration,
run `sudo deploy/recover_configuration_snapshot.sh <snapshot-id>`. It restores
only the bounded configuration subtree into
`/var/lib/lecturesift/configuration-recovery/`, validates it, and leaves live
`/etc/lecturesift` and `/opt/lecturesift` untouched. A root operator must review
the recovered deployment identity and explicitly install each intended env
file with owner `root:root` and mode `0600`, then run role-env validation,
production preflight and the documented validated reload. Never copy the whole
recovery directory over `/etc` or `/opt`.
Before downloading a snapshot, the drill obtains its exact restic restore size,
requires room for the restored bytes, an equally sized private cache and 5 GiB
of host reserve, and fails closed if capacity cannot be proved. At startup it
also removes only strictly named drill payloads older than one hour, covering a
previous SIGKILL or power loss without touching unrelated paths.

Keep the random restic password in an encrypted artifact on a different
host/account. Recover it there, prove it opens the exact repository/current
restic key, and record only the ciphertext hash, exact key ID and repository
binding with `deploy/record_restic_escrow.sh`. Repeat after any password, key or
repository rotation. The plaintext and encrypted artifact never belong in this
repository or on the VPS.

Post-cutover disaster recovery uses only `deploy/restore.sh` with a backup made
by `deploy/backup.sh`. It validates the selected set against its exact recent
drill marker before stopping traffic and restores PostgreSQL/Redis only after
the immutable compatibility manifest passes. The detailed cutover, rehearsal,
restore and rollback procedures are authoritative in `VPS_DEPLOYMENT.md`.

Historical source-only checkpoint (not a current release or data-recovery
point): LectureSift V4.1 (`0e1c8d29e880c1836866ba8eab33a227e402b64b`).
