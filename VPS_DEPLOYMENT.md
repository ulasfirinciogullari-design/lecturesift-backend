# LectureSift OVH VPS deployment

The production stack runs API, one Celery worker, PostgreSQL, Redis and Caddy
on the VPS. The private core and public ingress have separate systemd owners.
Netlify and private Cloudflare R2 remain external.

## Safety rules

- Never commit `/etc/lecturesift/runtime.env`,
  `/etc/lecturesift/{api,worker,instagram}.env`,
  `/etc/lecturesift/postgres.env` or `/etc/lecturesift/restic.env`, and never
  print their contents. `runtime.env` is only a root-side source of truth; it
  is not mounted into any container. Preflight derives the three service files
  atomically and verifies their exact content and root-only permissions. The
  API receives the full application configuration but never host-only restic
  repository/storage/encryption credentials. The worker receives only
  database, queue, OpenAI, R2, processing and cost values.
  It never receives account/session, admin, payment, email, Instagram or legal
  secrets. The scheduled Instagram container receives only its publishing
  credentials and public media URL settings. Restic remains separate from all
  application containers.
- Only ports 22, 80 and 443 may be public. PostgreSQL and Redis stay on the
  internal Compose network.
- Keep Render available for rollback until data, payment callbacks, uploads
  and one real analysis job pass on the new API.
- Never run both Instagram schedulers at the same time.
  `INSTAGRAM_DAILY_AUTOMATION_ENABLED` stays `false` through cutover and is
  changed only after the Render cron is confirmed disabled.
- The Compose stack is a single-host 4-vCPU/8-GB profile. Every long-running
  container has a hard memory/CPU/PID ceiling, a lower reservation and a
  bounded `/tmp`. The one worker stays at concurrency 1 so FFmpeg, Tesseract
  and LibreOffice can use a useful 3.5-GiB envelope without allowing two large
  jobs to exhaust the host. PostgreSQL and Redis also have engine-level memory
  and temporary-file ceilings. Do not raise one service limit without lowering
  another or moving the worker to a separate host.
- Worker HTTP(S) egress is deny-by-default at the Docker-network boundary: the
  worker joins only the internal backend network and uses the dedicated Squid
  proxy. Squid resolves every destination and denies private, loopback,
  link-local, carrier-grade NAT, documentation and multicast ranges before
  allowing ports 80/443. This covers ordinary redirects and fresh proxy DNS
  resolutions used by Python, boto3, yt-dlp and FFmpeg. It is still a network
  boundary, not a proof about every future third-party downloader: any new
  binary must be tested to confirm it honors `HTTP_PROXY`/`HTTPS_PROXY`, and
  the worker must never be reattached directly to the `egress` network.
- Every OVH application image is bound to one clean, full Git commit.
  `deploy/release.sh prepare` rejects a non-root invocation, a symlinked,
  non-root-owned/writable or non-Git deployment root, every tracked change and every untracked
  file, then atomically records the 40-hex HEAD in root-only
  `/run/lecturesift/release.env`. `release.sh build` rechecks the source before
  and after the build and accepts the image only when both its OCI revision
  label and baked `LECTURESIFT_BUILD_REVISION` match that marker. The API
  container receives the marker separately and cannot become healthy when its
  `/health.revision` differs. Never place either build identity variable in
  `runtime.env`, manually retag an older image, or run a production Compose
  build/up around this helper. The helper builds from a root-only temporary
  `git archive` of that immutable commit rather than the mutable checkout, so
  a transient file edit during Docker context creation cannot inherit the
  commit's trusted label.
- Keep `PAYMENT_TOKEN_BINDING_SECRET` stable and copy the exact same value to
  every API host. If it must be rotated, place the previous value in
  `PAYMENT_TOKEN_BINDING_LEGACY_SECRET` before changing the active value, keep
  it through iyzico's maximum pending-transfer and webhook-retry window, then
  remove it. Never rotate both values at once while a payment is pending.

## First installation

1. Install Docker Engine, the Compose plugin, Git, UFW, fail2ban and restic.
2. Copy the repository to `/opt/lecturesift`, recursively set the checkout and
   `.git` metadata to `root:root`, and remove group/other write permissions.
   Pull reviewed releases as root with fast-forward-only semantics; never let
   a deployment or web-service user modify code that systemd executes as root.
3. Create `/etc/lecturesift` as `root:root` mode `0700`. Copy
   `deploy/env.example` to `/etc/lecturesift/runtime.env` and
   `deploy/database.env.example` to `/etc/lecturesift/postgres.env`. Copy
   `deploy/restic.env.example` to `/etc/lecturesift/restic.env`. Populate them
   from production. Generate different random passwords for the API and worker;
   URL-encode each in `DATABASE_URL` and `LECTURESIFT_WORKER_DATABASE_URL`
   respectively, and repeat the matching raw values in `postgres.env`. Then set
   all three source files to owner `root:root` and mode `0600`.
   Do not manually create or edit `api.env`, `worker.env` or `instagram.env`.
   `deploy/preflight.sh` calls `deploy/generate_role_envs.py`, which refuses a
   symlinked, non-root or overly permissive source/destination and creates the
   role files as root mode `0600`. Any missing, stale or policy-violating role
   file blocks Compose startup. After changing `runtime.env`, use
   `sudo systemctl reload lecturesift`; its first `ExecReload` validates and
   regenerates the role files before Compose changes any running container.
   Do not use a blind restart for configuration changes because a bad value
   would stop a healthy stack before validation. The backup and
   Instagram units deliberately use read-only validation and fail when the
   generated files are stale.
   Database startup is also split by role: `provision_database_role.sh`
   bootstraps/rotates the two unprivileged logins, runs the `migration` profile
   once as `POSTGRES_USER`, installs masked worker views, and proves the final
   grants before API or worker can start. Never run the migration profile with
   an API/worker environment file and never grant either runtime role `CREATE`.
4. Do not copy systemd fragments manually. Keep every LectureSift unit inactive
   and disabled through the exact rehearsal. The exact admitted promotion under
   Data cutover installs and verifies the complete unit set without starting or
   enabling anything.
5. Point `api.lecturesift.com` to the VPS IPv4 as a DNS-only record.
6. Only after the exact rehearsal and admitted promotion have succeeded, during
   the very first bootstrap run
   `sudo env LECTURESIFT_PREFLIGHT_CONTEXT=bootstrap-infrastructure
   LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE=YES
   LECTURESIFT_BOOTSTRAP_INFRASTRUCTURE_ONLY=YES
   /opt/lecturesift/deploy/preflight.sh`
   before any Compose command. This validates the master files and creates the
   root-only role files that Compose requires even for a build. Never put that
   override in `runtime.env` or a persistent service file. Then run
   `sudo bash /opt/lecturesift/deploy/release.sh build`; it idempotently builds the
   pinned application/proxy images only when the clean HEAD is not already the
   verified local image, and its build-time image smoke check must pass. Then
   run the staging-Caddy command shown immediately after promotion under Data
   cutover. This is the first permitted service start after promotion; the digest-pinned
   temporary Caddy owns ports 80/443 and continues proxying the still-frozen
   Render API. While it is active,
   start and verify only `postgres` and `redis`; the infrastructure bootstrap
   is not permission to start API/worker. Complete the provider-cutover steps
   below and create the atomic `provider-cutover.ok` gate before invoking
   `lecturesift.service`. Its first production preflight creates the durable
   ingress state, which keeps both target API and worker in `freeze`. Only after
   frozen health/revision/readiness and the prior exact rehearsal are accepted
   may the dedicated handoff tool exchange staging Caddy for production Caddy.
   Never stop the staging container by hand; use the exact handoff/activation/
   rollback sequence under Data cutover.
   Startup and reload also run `deploy/resource_guard.sh`. It refuses fewer
   than four online CPUs or 7 GiB of visible RAM, validates the 1-GiB media and
   100-MiB document ingress contracts, checks existing API/worker work-volume
   budgets, and requires enough free Docker storage to preserve a 10-GiB host
   reserve plus one 8-GiB job workspace and another maximum-size upload.
   Temporary OCR/office files are on bounded in-container tmpfs mounts; durable
   sources and results belong in R2, not `/tmp`. A failed resource gate leaves
   the existing stack unchanged.
7. Initialize and test the off-site restic repository. During the first
   provider cutover, API/worker are intentionally still stopped, so do not use
   `backup.sh` to manufacture the restore proof required by the production
   gate. After the PostgreSQL and Redis cutover proofs exist, use the dedicated
   `deploy/seed_first_cutover_backup.sh` bridge and the exact sequence in
   `deploy/FIRST_CUTOVER_SEED.md`. It writes the same
   `lecturesift-backup-v2` and `configuration-snapshot-v1` formats as normal
   backups, but never starts/stops API/worker or changes Caddy/DNS. Restore the
   still-current seed through `restic_restore_rehearsal.sh` before running the
   provider finalizer. Once production has safely started, `backup.sh` installs an
   atomic, same-boot, two-hour `drain` marker that the already-running API reads
   per request. It never recreates API/Caddy, so authenticated payment callbacks
   remain reachable while new jobs are blocked. It waits up to 20 minutes for
   the queue to empty, stops the worker, takes the PostgreSQL/Redis checkpoint,
   then starts/verifies the worker before atomically clearing the marker. The
   backup unit also runs `recover_backup_runtime.sh` as `ExecStopPost`, so a
   killed/timed-out backup gets the same idempotent cleanup; after a host reboot
   the old boot ID invalidates any residue. Any drain, snapshot, restic, or
   runtime-restore failure makes the backup fail closed; the script never stops
   or recreates Caddy/API. Enable the daily timer only after observing
   this complete cycle once. A
   local-only backup on the same VPS is not sufficient for production cutover.
   The local timestamped PostgreSQL/Redis checkpoint intentionally contains no
   environment files. Immediately before the off-site upload, `backup.sh`
   creates a private ephemeral `configuration-snapshot-v1` package containing
   only the exact six root-owned mode-`0600` env files (`runtime.env`,
   `api.env`, `worker.env`, `instagram.env`, `postgres.env`, `restic.env`) and
   the fixed non-secret deploy-identity allowlist defined in
   `deploy/configuration_snapshot.py`, including the validated one-line
   `/run/lecturesift/release.env` commit marker. Each entry is copied through a
   no-symlink regular-file descriptor, hashed and recorded in a manifest; any
   missing, extra, non-root, wrong-mode or changing source aborts the backup.
   The package exists only in the Restic stage, is encrypted client-side, and
   the EXIT cleanup removes the plaintext stage on success or failure. Do not
   broaden this allowlist to `/etc`, `rehearsal.env`, Docker credentials, `.git`
   or arbitrary directories.
   Run `sudo deploy/restic_restore_rehearsal.sh` after the first off-site
   snapshot. It restores only the latest snapshot tagged `lecturesift` into a
   disposable database-size-bound tmpfs after reserving 2 GiB for the host, validates
   both dumps without attaching live volumes, removes the restored payload, and records non-secret evidence under
   `/var/lib/lecturesift/recovery-drills/`.
   Production recovery is fixed to the separate EU
   `lecturesift-production-backups` bucket and `/restic` repository prefix;
   normal preflight rejects any other repository target. The R2 token must be
   limited to that backup bucket and must differ from the application's object
   token. Keep `restic/data/` and `restic/snapshots/` locked for 90 days,
   `restic/config` and `restic/keys/` locked indefinitely, and leave
   `restic/locks/` plus `restic/index/` unlocked so normal repository locking
   and index maintenance still work. Never apply an R2 lifecycle deletion rule
   to the restic repository.
   `backup.sh` keeps **every** snapshot for at least 92 days (90-day object
   lock plus a two-day upload/timer safety margin) and does not run automated
   prune. Restic prune can repack or delete young data packs even when the
   snapshots selected by `forget` are old, which would collide with the R2
   lock. Reclaim space only through a separately reviewed prefix rotation:
   stop writing the old prefix, keep it untouched for at least 92 further
   days, complete a restore drill from the replacement prefix, then run
   maintenance against the now-fully-aged old prefix with a separate
   administrative credential. Never weaken/remove the production lock merely
   to make a daily prune pass.
   Before removing the bootstrap override, encrypt the random restic password
   into an escrow artifact stored on a different host/account. On that other
   host, decrypt a copy and prove the recovered password opens this exact
   repository with `restic cat config`; use `restic key list --json` to identify
   the key whose `current` field is true. Restic 0.16.4 may show only its unique
   prefix, so resolve that prefix against the full IDs from `restic list keys`
   and record the one matching 64-hex ID. Do not copy the plaintext
   or encrypted artifact back to the VPS. Calculate the encrypted artifact's
   SHA-256 there, then record only its non-secret fingerprint, recovered key ID
   and repository binding on the VPS:

   ```text
   sudo env LECTURESIFT_RESTIC_ESCROW_CONFIRM=YES \
     LECTURESIFT_RESTIC_ESCROW_RECOVERY_TESTED=YES \
     LECTURESIFT_RESTIC_ESCROW_CIPHERTEXT_SHA256=<64-hex-ciphertext-hash> \
     LECTURESIFT_RESTIC_ESCROW_KEY_ID=<64-hex-current-key-id> \
     /opt/lecturesift/deploy/record_restic_escrow.sh
   ```

   Repeat this procedure whenever the repository or restic password changes.
   The marker contains no password, repository URL or storage credential and
   is cryptographically bound to the repository ID, exact current restic key
   and encrypted artifact. Password/key rotation changes that key ID and makes
   old evidence fail preflight until escrow is replaced and retested.
   Set the three `LECTURESIFT_*_RECOVERY_CONFIRMED`/retention confirmation
   values to `true` only after their checks pass. Remove the bootstrap override.
   Every normal `lecturesift.service` start now requires an accessible restic
   repository, a latest off-site production snapshot no older than 48 hours,
   repository-bound escrow evidence, and a root-owned successful isolated drill
   no older than 90 days. Snapshot freshness and drill cadence are independent:
    a new daily snapshot does not invalidate an older successful drill. Missing,
   stale, symlinked or failed evidence blocks startup.
   Compose uses bounded `on-failure` restart policies rather than
   `unless-stopped`; systemd owns daemon/host boot and runs preflight before
   the private core. After preflight prepares the exact marker, systemd
   runs the idempotent release builder before any Compose `up`; reload follows
   the same order. Restarting Docker also restarts the bound LectureSift unit
   through the same gates; the separately bound ingress unit cannot start until
   that private core and its exact ingress gate pass. Keep Docker `live-restore` disabled as supplied in
   `deploy/docker-daemon.json`; enabling it would deliberately keep public
   containers alive while systemd cannot enforce the preflight boundary.
   Set `LECTURESIFT_OPS_ALERT_EMAIL` to a monitored mailbox. A failed scheduled
   backup writes root-only evidence under `/var/lib/lecturesift/backup-alerts/`
   and systemd invokes `lecturesift-backup-alert@.service` to send a Resend/SMTP
   alert; only a later fully successful off-site backup clears that marker.
   Normal quarterly drills select the latest snapshot and record
   `drill_scope=current-latest`. Before deliberately restoring an older retained
   backup, run the same isolated drill with
   `LECTURESIFT_RESTIC_REHEARSAL_SNAPSHOT_ID=<snapshot-id>`; its
   `explicit-backup` marker can authorize only that exact backup-set hash and
   cannot satisfy the normal current-drill startup gate.
   The drill obtains the selected snapshot's restic restore size before writing
   its payload and requires two times that size plus 5 GiB of free host reserve
   for the payload and private cache. It fails closed when capacity cannot be
   proved and safely reconciles only strictly named payload directories older
   than one hour after an interrupted prior drill. Run a large drill on a
   separate trusted recovery host if the production VPS cannot meet this gate;
   never weaken the reserve or isolated validation limits.
   For a host-configuration recovery, select an already verified production
   snapshot ID and run:

   ```text
   sudo /opt/lecturesift/deploy/recover_configuration_snapshot.sh <snapshot-id>
   ```

   The command downloads only the bounded configuration subtree, checks the
   exact allowlist, root-only modes, manifest and both recorded hashes, and
   leaves it under `/var/lib/lecturesift/configuration-recovery/`. It never
   writes live `/etc/lecturesift` or `/opt/lecturesift`. Review the recovered
   identity files against the checked-out release, then follow the generated
   `OPERATOR_STEPS.txt`: explicitly install only each required env file with
   `install -o root -g root -m 0600`, run
   `generate_role_envs.py --check`, run `preflight.sh`, and use the validated
   reload procedure. `restic.env` recovery still requires the separately
   escrowed Restic password/key material to open the repository; the repository
   cannot serve as its own only key escrow.
   Enable the Instagram timer only after disabling Render cron.

## Data cutover

Before the maintenance window, stop the normal VPS API and worker. Independently
review and install `deploy/trusted_stage_release_controller.sh` as root-owned,
non-writable `/usr/local/sbin/lecturesift-release-stage-controller`. Generate
the revision-pinned operator wrapper with
`deploy/generate_stage_release_wrapper.py`; that wrapper may invoke only this
fixed controller. The controller bounds transports/resources and proves the
complete tree against the version-2 private allowlist before any candidate
shell or Dockerfile is evaluated. Never clone and root-execute a staging helper
directly from a transported bundle.

Stage the exact candidate, then run the disposable rehearsal only through the separately
reviewed, fixed
`/usr/local/sbin/lecturesift-exact-rehearsal-controller`. Never invoke candidate
`deploy/run_exact_rehearsal.sh` directly. The trusted controller requires the
revision-specific root-only tree/both-controllers/orchestrator hash allowlist described
in `deploy/EXACT_REHEARSAL_SAFETY.md` before crossing into candidate code.
Candidate rehearsal code runs as root and controls Docker, which is
host-root-equivalent: this is an explicit reviewed-code trust boundary, not a
sandbox or a safe way to evaluate an unreviewed commit on the production host.
On the OVH host, first run
`sudo bash /opt/lecturesift/deploy/check_shell_syntax.sh`; its
`SHELL_SYNTAX_OK` record is a mandatory pre-rehearsal gate for every tracked
deployment shell script. CI runs the same gate on Linux. Native Windows test
runs skip only this runtime check when Bash is unavailable.
Do not invoke `rehearsal_restore.sh` directly: after independent allowlisting,
the tracked candidate outer wrapper proves the base PostgreSQL roles/main
database, Redis data, Docker resources and host listeners returned to their
baselines on both success and failure. Follow
`deploy/EXACT_REHEARSAL_SAFETY.md` for the trust boundary, evidence chain and
SHA-specific bootstrap regeneration. The inner rehearsal creates a
uniquely named PostgreSQL database, starts the localhost-only isolated stack,
runs both the account/R2/worker E2E and every supported document/OCR/audio/video
format E2E, then removes the rehearsal containers and force-drops the cloned
database on success or failure. It keeps only root-only dump metadata/manifests
and non-secret E2E reports under `/var/backups/lecturesift/rehearsal`; no database
clone or raw production dump is handed off to a later command or retained
indefinitely; non-secret reports are pruned after 30 days. The rehearsal first
proves exact manifest equality between the read-only source and its raw restored
clone. The sole pre-release compatibility exception is an explicitly recorded
missing `billing_payment_provider_sessions` table; no missing-table anomaly is
reported as zero. The owner migration then runs only on the disposable clone,
after which a strict table diff, schema fingerprint, constraints and anomaly
report must pass. Existing `DATABASE`, `STATUS` and table-data fingerprints must
remain unchanged, and the new table must be empty when it was absent at source.
The migration also emits a canonical `SCHEMA_OBJECT` inventory. The reviewed
revision contract fixes every column, index and constraint of the sole allowed
new table, while all other public-table column/index/constraint records must be
byte-identical before and after migration. This is deliberately a migration
delta contract, not a golden declaration of preserved legacy schemas, views,
functions, grants or extensions. Both application E2Es are mandatory. Their
generated UUIDs and addresses are then consumed by a rehearsal-only cleanup
primitive that refuses any non-timestamped rehearsal database; the final strict
manifest must equal the pre-E2E migrated manifest exactly.
If the source changes between the before/after manifests, the rehearsal fails
and must be repeated. Supply `LECTURESIFT_REHEARSAL_S3_ENDPOINT_URL`,
`LECTURESIFT_REHEARSAL_S3_BUCKET`,
`LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID`, and
`LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY` in
`/etc/lecturesift/rehearsal.env` (or the absolute path named by
`LECTURESIFT_REHEARSAL_ENV_FILE`), using `deploy/rehearsal.env.example` as the
key-name template. The rehearsal file must be a regular,
non-symlink, root-owned file with mode `0400` or `0600`; never put these values
in `runtime.env`. The endpoint must be an explicit Cloudflare R2 HTTPS
endpoint, and both the bucket and least-privilege token must be distinct from
production. Different names and secret values are not sufficient evidence:
before candidate containers start, the exact rehearsal positively verifies the
token on the rehearsal bucket and requires read-only list and random-missing-
object GET requests against the production bucket to return an explicit
authorization denial. It never writes to either bucket during this capability
gate; success, an ambiguous error or an unavailable network fails closed, and
the secret-free proof is bound into admission. The host parses this file as dotenv data without executing it and
rejects every key outside the fixed rehearsal allowlist. It similarly reads
the production database/API/worker dotenv files only on the host to prove that
no rehearsal password, token or key equals a production value. It then writes
short-lived root-owned mode-`0600` API and worker env files in the private run
directory; it never mounts or passes production role env files to candidate
containers. A production-admitting run requires
`LECTURESIFT_REHEARSAL_OPENAI_API_KEY`, and it must be a dedicated
non-production key. Without it, document/OCR diagnostics may still run and the
AI-backed format cases are recorded as intentional provider skips rather than
false passes, but the exact artifact validator rejects the result and no
release admission can be written. Admission requires successful MP3 and MP4
AI/audio/video cases with an empty skipped-case set.

The API and worker attach only to the labeled internal
`lecturesift_rehearsal_backend` network, never the production Compose backend.
The validated production PostgreSQL container is temporarily attached to this
network under the `postgres` alias so clone-only roles can reach only that
necessary endpoint; production Redis stays detached and must fail runtime DNS
probes from both candidates. Their only external paths are two
separate Squid containers with separately generated policies and backend
aliases. Each API/worker-to-proxy link uses a different temporary internal
Docker network, so the API cannot resolve or connect to the worker proxy. The
API proxy can reach only the dedicated R2 endpoint. The worker proxy can reach
R2 and, only with the dedicated AI key, `api.openai.com`.
The stack verifies actual container network/env state, denies arbitrary proxy
and direct Internet probes, and proves the allowed R2 path through rollout
health before E2E acceptance. Billing, email and Instagram providers remain
disabled and no live provider credential enters either role. The orchestrator invokes
`deploy/rehearsal_stack.sh` itself. The candidate API publishes no host port,
binds only to its own container loopback on port 8000, and is probed through
fixed allowlisted paths from an unprivileged process inside that container.
Do not invoke the stack later with a database name because the clone is already
destroyed. The rehearsal never shares the production Redis/Celery queue or
object bucket, API/worker work volumes, or public traffic.
The Instagram health probe must return the exact safe `LS-IG-01` disabled-provider
response because no live Instagram credential is admitted to the candidate.
Its two dedicated
work volumes are size-bounded, non-executable tmpfs volumes (512 MiB API,
2 GiB worker), are empty at start and removed together with the rehearsal
containers before the orchestrator can report success.

The two fixed outer trust controllers keep bounded Git/archive review trees in
root-owned mode-`0700` directories below
`/var/lib/lecturesift/controller-state`, not the VPS's small `/run` tmpfs.
Their 12-GiB staging and 8-GiB exact-review gates therefore measure the disk
that holds those potentially large trees. The source-comparison scratch trees
also stay inside the matching controller run directory. Generated dotenv and
other secret-bearing inner state remains in root-only `/run` and is removed at
the end of the rehearsal. Persistent controller residue is reconciled only
under its controller lock, after every candidate directory has passed strict
name, ownership, mode, device, age and nested-mount validation; any unknown or
recent entry blocks all deletion.

On the next locked run,
strictly named residue older than one hour from a SIGKILL/power loss is
reconciled under the outer lock only after a complete no-delete validation of
every fixed name, dual label, run age, internal-network topology and endpoint.
The validated production PostgreSQL attachment is then disconnected before the
dedicated network is removed; unknown, mixed-run or recent state blocks without
deleting anything. Production preflight independently fail-stops while any such
residue or attachment exists.
The clone receives three timestamp-derived disposable database roles: one
clone owner plus separate API and worker roles. The restored database is owned
by the clone owner, `pg_restore` uses `--role=<clone-owner> --no-owner --no-acl`,
and candidate migration receives only that clone-owner URL. Production
`POSTGRES_USER`/`POSTGRES_PASSWORD` remain confined to the trusted PostgreSQL
client and are never passed to candidate code. Rehearsal provisioning therefore
cannot rotate live role passwords or settings. Database and role comments bind cleanup to the
exact timestamped clone. Before `createdb`, the orchestrator atomically writes
and fsyncs a root-owned mode-`0600` marker under the fixed mode-`0700`
`/var/lib/lecturesift/rehearsal-provenance` registry. This durable binding lets
the next locked run safely recognize a power loss between database creation and
`COMMENT ON DATABASE`; unknown, malformed or recent registry state blocks.
Normal cleanup and stale cleanup both refuse a rehearsal-like database or role
without the matching validated marker, and the marker is removed and its parent
directory fsynced only after the database and all three disposable roles are proven
absent. Roles are dropped after their database so cluster-global role settings
cannot remain after a successful run.
The outer gate invokes the inner script's locked `--reconcile-only` mode before
capturing its host/database baseline. That mode never drops a database or role:
it exits successfully only when no timestamped rehearsal database/role exists
and the registry is empty after removing strictly parsed markers older than one
hour whose database and all three derived roles are already absent. A recent,
malformed or unknown marker, or any matching database/role, fails closed for
operator inspection. Its sole success record is
`REHEARSAL_RECONCILE_OK|database_or_role_modified=false|provenance_empty=true`.

After the exact rehearsal succeeds, promote that same admitted commit into the
fixed production root before any migration or service start:

```sh
REVISION=<exact-40-hex-rehearsed-commit>
sudo env LECTURESIFT_PROMOTE_RELEASE_CONFIRM=PROMOTE-EXACT-REHEARSED-RELEASE \
  LECTURESIFT_PROMOTE_LEGACY_ROOT_CONFIRM=PRESERVE-UNVERIFIED-LEGACY-ROOT \
  bash "/srv/lecturesift/worktrees/$REVISION/deploy/promote_rehearsed_release.sh" \
  "$REVISION"
```

The promotion clones only the clean admitted revision into a root-owned
same-filesystem incoming tree, replaces `/opt/lecturesift` using two atomic
same-filesystem renames under a durable fail-stop transaction marker, preserves the previous tree under
`/opt/.lecturesift-previous`, revalidates the admission and pinned images, and
installs the exact systemd fragments. It neither enables nor starts a service.
Any validation or installation failure restores the prior production tree and
release marker. A successful retry is verification-only and does not create a
second rollback copy. If
`/var/lib/lecturesift/release-promotion/release-promotion.in-progress` remains,
the rollback is not proven: keep every service stopped and investigate instead
of deleting the marker or retrying. Do not copy candidate files into `/opt` or systemd manually and
do not continue unless the command reports the exact revision with
`services_started=false`.

The unit installer separately fences fragment replacement with
`/var/lib/lecturesift/systemd-unit-backups/systemd-install.in-progress` only
after its complete root-only rollback set is durable. Production preflight
rejects either transaction marker. If either remains after interruption, keep
the selector and every application unit stopped and restore/verify the recorded
rollback set; never delete a marker merely to make startup pass.

The extra legacy-root confirmation is valid only for this first promotion from
the reviewed `ubuntu:ubuntu` mode-`0775`, extracted `/opt/lecturesift` tree.
The tool never executes or sources that untrusted tree: it requires the exact
fixed path and reviewed ownership/mode, rejects nested mounts, and moves it
intact into the root-private rollback directory. It accepts this path only
while that rollback directory has never existed and verifies the same
filesystem inode identity if rollback is needed. Omit the confirmation on every later
promotion; an existing Git production tree must always be clean and exact.

Only now return to bootstrap step 6 and start (but do not enable) staging Caddy:

```sh
sudo systemctl start lecturesift-caddy-staging.service
```

Starting it
before rehearsal/promotion invalidates the stopped-unit admission contract;
the installer also refuses an already-enabled staging unit.

1. Populate the fixed, root-owned `/root/.lecturesift-render-source.env` with
   `SOURCE_DATABASE_URL` (the external Render PostgreSQL URL with exactly
   `sslmode=verify-full`) and
   `SOURCE_HEALTH_URL` (the direct Render `/health` URL). It must be a regular,
   non-symlink file with mode `0400` or `0600`. `sslmode=require` and
   `sslmode=verify-ca` are intentionally rejected because they do not prove the
   requested Render hostname. The migration tools parse this dotenv strictly
   as data and pass only canonical, non-secret libpq endpoint variables to
   PostgreSQL clients. The password is written to a single-link mode-`0600`
   file in a root-only `/run` session, mounted read-only as `PGPASSFILE`, and
   removed when the Docker client exits; it is never a Docker/host environment
   value. Health and Redis child processes receive only their endpoint family;
   only the source-identity fingerprint process receives all four source URLs.
   Never put the database URL or password in the repository, a process command
   line, or logs.
   Separately create `/root/.lecturesift-render-cutover-control.env` as a
   root-owned, single-link regular file with mode `0400` or `0600`. It must
   contain exactly `RENDER_API_TOKEN`, the exact background-worker
   `RENDER_WORKER_SERVICE_ID`, `RENDER_WORKER_SERVICE_NAME`,
   `RENDER_INSTAGRAM_CRON_SERVICE_ID`, and
   `RENDER_INSTAGRAM_CRON_SERVICE_NAME`. The token needs
   read access to those two services only. Cutover tools issue only four
   official Render GET requests (each service plus its instances), require the
   exact worker and cron to be suspended with no listed instance, and bind the secret-free
   result digest into every provider proof. They never send Celery remote-control
   commands to the live Render Redis service.
2. Put the live Render API into exact `freeze` mode, stop its worker, drain all
   queued/active work, and reconcile every provider payment. Keep Render
   fenced for the whole cutover. A manual statement that the queue is empty is
   not enough: `deploy/migrate_postgres.sh` checks the direct health response
   and requires the combined count of manual `pending` plus card
   `created`/`pending` orders to remain exactly zero before and after capture.
3. From a clean, reviewed checkout, record one 32-hex cutover ID and its exact
   40-hex Git revision in the root operator session. Use those same two values
   for PostgreSQL, Redis and finalization; never generate a second ID halfway
   through the maintenance window. Run `sudo env
   LECTURESIFT_PROVIDER_CUTOVER_ID=<32-hex-cutover-id>
   LECTURESIFT_EXPECTED_BUILD_REVISION=<40-hex-clean-HEAD>
   LECTURESIFT_POSTGRES_CUTOVER_CONFIRM=YES
   LECTURESIFT_SOURCE_FROZEN=YES LECTURESIFT_SOURCE_WORKER_STOPPED=YES
   LECTURESIFT_PROVIDER_RECONCILED=YES bash deploy/migrate_postgres.sh`. It exports
   one Render MVCC snapshot, runs the manifest and `pg_dump` against that same
   read-only snapshot, and requires exact live before/snapshot/after equality.
   The source database is never schema-migrated or otherwise written by this
   command. A pre-release source may carry only the explicitly recorded missing
   `billing_payment_provider_sessions` compatibility marker; every other missing
   table, unvalidated constraint or data anomaly remains fatal. Before
   replacing OVH PostgreSQL it stops only the OVH API and worker, records a
   root-only target rollback dump, and preserves 5 GiB of host reserve. It
   restores without source owners/ACLs and proves the raw target manifest is
   exactly equal to the stable Render snapshot before any owner migration runs.
   It then applies the current schema on OVH, provisions the least-privilege
   database roles, and requires a strict manifest with no compatibility marker,
   table difference, unvalidated constraint or anomaly. `DATABASE`, `STATUS`
   and every pre-existing table-data fingerprint must survive that migration;
   if the provider-session table was absent at source it must be newly created
   and empty, while an already existing table and all of its rows must be
   preserved. Real one-shot API and worker table-access probes run only against
   this migrated target and must leave its strict manifest unchanged. Render's
   freeze, stopped worker/queue and zero pending payments are rechecked after
   target verification. Both the source manifest hash and strict migrated-target
   manifest hash are bound into the atomic PostgreSQL cutover proof. The
   verified run and both dumps remain under the fixed root-only
   `/var/backups/lecturesift/postgres-cutover` directory until final acceptance.
   Any unproved post-mutation failure restores the target rollback dump when
   possible and leaves a blocking marker under
   `/var/lib/lecturesift/migration-fail-stop`; operators must inspect it before
   deliberately clearing it. The script never touches Caddy, DNS or frontend
   configuration and leaves API/worker stopped even after success. It also
   leaves a global `provider-cutover.in-progress` fence and an atomic,
   source-fingerprint/revision-bound PostgreSQL proof; PostgreSQL success alone
   can never reopen production startup.
4. Do not run `deploy/restore.sh` during provider migration: that script is for
   same-version VPS disaster recovery and intentionally replaces Redis data.
   Stop any rehearsal containers. In the root-only
   source environment, provide `SOURCE_HEALTH_URL=https://<render-api>/health`.
   Run `deploy/migrate_redis_state.sh` with
   the same `LECTURESIFT_PROVIDER_CUTOVER_ID` and
   `LECTURESIFT_EXPECTED_BUILD_REVISION`, plus
   `LECTURESIFT_REDIS_MIGRATION_CONFIRM=YES`,
   `LECTURESIFT_SOURCE_FROZEN=YES`, and
   `LECTURESIFT_SOURCE_WORKER_STOPPED=YES`. The script independently verifies
   live freeze mode and the independently proved suspended Render worker, holds a target
   migration lock, copies only versioned `lecturesift:jobs:v2` JSON, and
   performs final source and target rereads. Never import a Render Valkey RDB
   into VPS Redis. Before the first target mutation it retains the exact previous
   raw value in the root-only migration run directory and writes canonical
   metadata recording whether the key existed, its byte length, and its SHA-256
   digest. An existing empty value is therefore never confused with an absent
   key. Successful cleanup deliberately preserves both rollback files and prints
   their paths; diagnostics and terminal output never include the payload.
   Target Redis evidence is an all-key, two-pass logical manifest over each
   key's type, serialized `DUMP` digest and absolute-expiry policy. Key names and
   values are HMAC-protected by a fixed root-only random salt. The reader is a
   tracked pure-standard-library RESP client running on the trusted host against
   the identity-stable official `redis:7.4-alpine` container; it never imports
   the candidate application image or its packages. Only the exact migration
   lock is excluded, and the non-job before/after comparison permits the one
   reviewed jobs-state replacement while rejecting every other mutation.
   If a failed migration cannot durably prove restoration of the previous
   target value, it leaves the Redis write lock in place and creates
   `/var/lib/lecturesift/migration-fail-stop/redis-state-unproven`. Production
   preflight will refuse to start until an operator verifies/repairs the target
   Redis value and then deliberately removes that root-only marker.
5. Initialize the exact dedicated EU R2 Restic repository, complete the
   off-host Restic-password escrow proof, and run the root-only R2 retention
   probe once. It must produce
   `/var/lib/lecturesift/recovery-drills/r2-retention-lock.ok`. While Render
   remains frozen and API/worker remain stopped, run
   `deploy/seed_first_cutover_backup.sh` with the same cutover ID/revision and
   all four explicit seed confirmations. The seed tool first requires the
   matching version-2 PostgreSQL and Redis proofs. Inside one exported read-only
   PostgreSQL snapshot, and before `pg_dump` consumes it, the seed runs the same
   strict rehearsal manifest and canonical line contract used by PostgreSQL
   migration. It hashes that snapshot-bound result and requires exact equality
   with the migrated-target manifest hash in `postgres-cutover.ok`; any
   post-migration drift fails closed. It then
   creates one exact-format, encrypted Restic snapshot from the already-running
   target PostgreSQL/Redis, deletes its root-private plaintext staging directory
   (ordinary filesystem deletion, not guaranteed storage-media sanitization),
   and records that target-manifest hash together with the snapshot, backup-set,
   repository and step-proof hashes in the root-only
   `first-cutover-seed.ok`. It never runs Compose lifecycle commands, changes
   traffic, or performs Restic forget/prune. Follow
   `deploy/FIRST_CUTOVER_SEED.md` exactly.

   With no other snapshot writer running, immediately run
   `deploy/restic_restore_rehearsal.sh` without an explicit snapshot override.
   Its resulting `current-latest` proof must identify the exact seed snapshot,
   backup set and repository; a recent but different snapshot is rejected.
   Then, while Render remains frozen and both sides still have zero pending
   payments/queues, run `deploy/finalize_provider_cutover.sh` with the same
   ID/revision and
   `LECTURESIFT_PROVIDER_CUTOVER_FINALIZE_CONFIRM=YES`. The finalizer rechecks
   the direct Render freeze response and provider identity, the exact Render
   revision, absence of both its worker and Instagram cron instances, both queues,
   pending provider orders, clean Git revision, seed proof, and
   repository-bound recovery and retention evidence. Only exact-matching
   PostgreSQL, Redis and first-cutover seed proofs can
   create `/var/lib/lecturesift/provider-cutover/provider-cutover.ok`; its
   atomic creation does not start API/worker and does not touch Caddy or DNS.
   It is not evidence that a Redis or R2 rollback was performed.
6. Keep the disabled `lecturesift-caddy-staging.service` active and Render
   public. Start only the private core, then enable the single durable selector
   (never either concrete ingress unit) and prove the independently routed endpoints:

   ```sh
   sudo systemctl enable lecturesift.service
   sudo systemctl start lecturesift.service
   sudo systemctl enable lecturesift-ingress-selector.service
   sudo systemctl start lecturesift-ingress-selector.service
   sudo bash /opt/lecturesift/deploy/verify_production_ingress.sh prepare
   ```

   `lecturesift.service` starts only PostgreSQL, Redis, the egress proxy, API
   and worker; it never starts Caddy. Production preflight creates and validates
   root-owned `/var/lib/lecturesift/ingress-state/provider-ingress.state` before
   the containers start. That state is mounted read-only into API and worker;
   every state except `activated` forces runtime `freeze`. The `prepare` gate
   runs the OVH health/readiness/revision probes from inside the API container
   against its private loopback listener and requires `freeze`,
   then separately requires the public staging TLS `/health` response to match
   the final-evidence-bound direct Render source fingerprint, full revision,
   explicit `render` provider identity and `freeze` mode. The selector is the
   only enabled ingress unit. On every boot and at a bounded 30-second runtime
   interval it reconciles durable state to one
   runtime owner: pre-handoff/interrupted states converge to Render, awaiting
   activation converges to frozen OVH (or safely restores Render), and activated
   converges only to OVH. Because OVH is
   deliberately non-writable, do not attempt login, upload, callback or job
   mutations here; those were exercised by the exact isolated rehearsal.
7. Perform the only approved public ingress handoff:

   ```sh
   sudo bash /opt/lecturesift/deploy/handoff_production_ingress.sh handoff
   ```

   Before changing either unit, the tool atomically records
   `handoff-in-progress` bound to the OVH release/final proof and the Render
   source revision captured by finalization. It stops staging, starts production
   without changing either concrete unit's enablement, and requires the
   public OVH HTTPS health/readiness/revision contract while OVH still reports
   `freeze`. Success durably records `awaiting-activation`; it does **not** open
   writes. Re-running the command reconciles a complete frozen handoff or safely
   restores Render from an ambiguous pre-activation crash. Every transition is
   serialized by a fixed root-only lock. Never start Compose Caddy directly and
   never enable either concrete ingress unit; only the selector is enabled.
8. Before activation, any ingress/TLS/read-only acceptance failure may use:

   ```sh
   sudo bash /opt/lecturesift/deploy/handoff_production_ingress.sh rollback
   ```

   Restoration first proves OVH still reports `freeze`. If OVH health is
   unavailable, it first stops the production ingress and target core and proves
   API, worker and Instagram publishers inactive. Only then may it prove the
   finalized direct Render source is still frozen, worker/cron-suspended and
   queue-idle, record `restore-in-progress`, and start staging. Bounded polling
   declares success only when public TLS reports the exact final-bound Render
   revision and `render` identity. The private OVH probe is never evidence that public
   traffic returned to Render.
9. When the frozen public OVH checks pass, perform the separate write activation:

   ```sh
   sudo bash /opt/lecturesift/deploy/handoff_production_ingress.sh activate
   ```

   Immediately before the transition, the tool re-proves the direct Render
   `freeze`, exact final-bound revision/source identity, worker and Instagram
   cron suspension, empty source queue, and stopped/disabled OVH Instagram
   timer/service/container. Any failure preserves `awaiting-activation`.
   The single atomic, fsynced transition from `awaiting-activation` to
   `activated` is the write-enablement point. Only afterward run external login,
   email, admin, upload/download, processing and real signed payment-callback
   acceptance. Keep Render frozen and retain its data. A manual `rollback`
   request at or after activation always refuses and points to
   `deploy/ROLLBACK_RECONCILIATION.md`; never switch traffic back based on a
   belief that OVH received no writes. Freeze both sides and complete the full
   PostgreSQL, Redis, R2 and provider reconciliation procedure. Do not enable
   either Instagram scheduler until the final production acceptance is complete.
   Caddy/DNS is the last gate, not part of either data migration command.

### First-start crash recovery

An interrupted start after the one-time first-start marker is armed is
deliberately crash-fenced. Never delete
`provider-first-start.in-progress`. Keep the Render proxy public, stop every
target writer/ingress, bring up only the target data stores, and run the
validated recovery:

```sh
sudo systemctl stop lecturesift-ingress-selector.service
sudo systemctl stop lecturesift-ingress.service lecturesift.service
sudo systemctl start lecturesift-caddy-staging.service
sudo docker compose --project-directory /opt/lecturesift \
  --file /opt/lecturesift/compose.yaml up -d --wait --wait-timeout 300 postgres redis
sudo docker compose --project-directory /opt/lecturesift \
  --file /opt/lecturesift/compose.yaml stop --timeout 120 worker api caddy egress-proxy
sudo env LECTURESIFT_PROVIDER_FIRST_START_RECOVERY_CONFIRM=YES \
  bash /opt/lecturesift/deploy/verify_provider_first_start.sh recover
sudo systemctl reset-failed lecturesift.service
sudo systemctl start lecturesift.service
sudo systemctl enable lecturesift-ingress-selector.service
sudo systemctl restart lecturesift-ingress-selector.service
sudo bash /opt/lecturesift/deploy/verify_production_ingress.sh prepare
```

Recovery revalidates the final release proof, armed marker, optional consumed
proof, current PostgreSQL data/authority/role-login evidence and complete Redis
manifest under the shared migration lock. It writes a durable recovery audit
proof before removing the fence. Any mismatch leaves the fence in place for
investigation; there is no marker-deletion bypass.

## Post-cutover disaster recovery

`deploy/restore.sh` is only for a backup created by `deploy/backup.sh` from the
same VPS Redis 7.4 series. It restores PostgreSQL and replaces the Redis 7.4
volume after checking checksums, LectureSift application/schema identity,
the full schema/data manifest and exact successful restore-drill evidence for
the selected backup set. Recovery manifests are immutable, versioned files
(`deploy/recovery_manifest_vN.sql`); never edit or delete an old version after
it has produced a backup. Add a new version and update `backup.sh` when the
application schema changes so retained historical backups remain testable.
Each backup also carries an explicit `application_schema_compatibility` value;
restore tooling accepts only compatibility contracts that this deployed code
knows how to migrate safely.
Its disposable PostgreSQL validation uses the recorded
live database size to create a dynamic memory-capped tmpfs, disables swap and
requires a 2 GiB host reserve instead of imposing an arbitrary fixed ceiling;
all validation data is removed before any live service is stopped.
Patch releases within PostgreSQL major 18 are normalized in the database
identity contract; encoding, locale provider/collation version and timezone
remain exact gates because changing them requires an explicit reindex/migration
procedure rather than silently accepting a mutable image-tag change.
Run it only with both
`LECTURESIFT_RESTORE_CONFIRM=YES` and
`LECTURESIFT_REDIS_RESTORE_SOURCE_VERSION=7.4`. It must never be used for a
Render/Valkey migration dump. Provider migration always uses PostgreSQL restore
followed by the logical Redis JSON migration described above. Before stopping
anything, the restore runs the full production preflight and regenerates and
validates all role-specific environment files; the one-time recovery bootstrap
override is rejected. After data replacement it brings up PostgreSQL and Redis
first, then requires both API and worker health, and starts Caddy last. Any
failure after the stop boundary keeps Caddy, API and worker stopped and reports
whether that stopped state could be confirmed; database services may remain
available for repair.

`restore.sh` restores application data only. It deliberately does not extract
or overwrite host configuration. Use the isolated configuration recovery
command above as a separate operator-reviewed procedure before a data restore
when rebuilding a lost host; never make configuration installation an implicit
side effect of PostgreSQL/Redis recovery.

## Rollback

Never flip DNS/frontend traffic back while discarding writes created on OVH.
There is no safe automatic row merge between two independently writable
PostgreSQL databases. Freeze both OVH and Render, stop both workers, drain the
OVH queue, reconcile the payment provider, and keep the old Render database
fenced. Then run `bash deploy/rollback_postgres_to_render.sh` with all six explicit
freeze/worker/provider confirmation flags. It takes an exported-snapshot OVH
dump plus exact before/snapshot/after manifests and a separate Render rollback
dump. If the two databases already match, it records that fact without writing.
If they differ, the default behavior is to stop and write
`RECONCILIATION_REQUIRED`; it does not guess a merge.

The evidence-only invocation is:

```sh
sudo env LECTURESIFT_POSTGRES_ROLLBACK_CONFIRM=YES \
  LECTURESIFT_OVH_FROZEN=YES LECTURESIFT_OVH_WORKER_STOPPED=YES \
  LECTURESIFT_RENDER_FROZEN=YES LECTURESIFT_RENDER_WORKER_STOPPED=YES \
  LECTURESIFT_PROVIDER_RECONCILED=YES \
  LECTURESIFT_PROVIDER_ROLLBACK_ID=<32-hex-rollback-id> \
  LECTURESIFT_PROVIDER_CUTOVER_ID=<original-32-hex-cutover-id> \
  LECTURESIFT_OVH_RELEASE_REVISION=<exact-40-hex-ovh-commit> \
  LECTURESIFT_RENDER_RELEASE_REVISION=<exact-40-hex-render-commit> \
  bash deploy/rollback_postgres_to_render.sh
```

Only after reviewing that evidence may an operator rerun with
`LECTURESIFT_RENDER_REPLACE_CONFIRM=REPLACE_STILL_FENCED_RENDER`. That explicit
mode replaces the complete approved application schema set (`public` plus
`lecturesift_worker` when present), including data and canonical schema-only
definitions. It rejects every unapproved user schema and any extension located
inside an application schema before mutation. Source owners/ACLs are not
portable across providers: restored objects are explicitly reconstructed as
the Render database owner, PUBLIC schema/table/sequence/routine privileges are
revoked, and a catalog inventory proves the owner-only app ACL policy. Render's
database-level ACL, role settings, default ACLs, collation attributes and
extension inventory must remain byte-identical to their pre-operation evidence.
The tool proves strict manifest and schema equality and attempts to restore the
original Render dump with its original ACLs on any failure. A still-fenced pre-release Render schema is inspected with the
same single explicit provider-session compatibility marker, so its rollback
dump can be restored and proven without pretending the missing table is an
anomaly-free current schema. The OVH replacement itself must always pass the
strict current-schema manifest. It still does not switch traffic. Reconcile the logical Redis job
state in the reverse direction with a separately reviewed procedure, reconcile
R2 objects created after cutover, re-test signed payment callbacks, and only
then return traffic to Render. A PostgreSQL success marker explicitly records
Redis and R2 reconciliation as incomplete so it cannot be mistaken for a
complete rollback.
