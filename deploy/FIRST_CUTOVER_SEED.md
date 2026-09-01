# First provider-cutover recovery seed

`seed_first_cutover_backup.sh` closes the first-cutover recovery-proof gap.
The normal `backup.sh` deliberately requires an already-running API and worker
so it can install and later remove its drain fence. The provider cutover must
not start those writers before the PostgreSQL, Redis, R2 and release proofs
have been joined into `provider-cutover.ok`. The seed tool therefore captures
the already-migrated PostgreSQL and Redis directly while every application
writer remains stopped.

This is a one-time cutover tool, not a replacement for the daily backup timer.
It never starts or stops a Compose service, changes Caddy/DNS, runs `restic
forget`/`prune`, or interprets an unproved snapshot as success.

## Fixed prerequisites

- Run as root from the clean, root-owned `/opt/lecturesift` checkout.
- `/etc/lecturesift/{runtime,api,worker,instagram,postgres,restic}.env` must
  already exist as root-owned mode `0600` files and the generated role files
  must pass `generate_role_envs.py --check`. Consequently, this seed cannot run
  before the runtime/database configuration and infrastructure bootstrap exist.
- `postgres` and `redis` must already be running from the verified provider
  migrations. API, worker and every rehearsal writer must remain stopped.
- Render must still report exact `freeze` mode, its worker must be suspended
  with no instance according to the official GET-only Render API proof, both
  source/target queues must be empty, and both databases must have zero pending
  provider payments.
- `/root/.lecturesift-render-cutover-control.env` must contain only the
  root-private Render API token and exact worker service ID/name. Its secret is
  never logged or stored in evidence. The target Redis digest is captured by a
  trusted host standard-library RESP reader against the validated official
  Redis container, not by the candidate application image.
- The same 32-lowercase-hex cutover ID and 40-lowercase-hex clean Git revision
  used for PostgreSQL and Redis are required. Both migration success proofs
  must use the current evidence version and match them and the canonical Render
  endpoint fingerprint. Version-1 proofs are deliberately rejected because
  they did not bind the strict migrated-target manifest.
- The Restic repository must already be initialized and must be exactly:

  `s3:https://<32-lowercase-hex-R2-account-id>.eu.r2.cloudflarestorage.com/lecturesift-production-backups/restic`

  Put that value, the random Restic password and the dedicated host-only R2
  backup token only in `/etc/lecturesift/restic.env`. Export the token to
  Restic as `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`; never place a secret in
  a command line, repository, screenshot or log. On a genuinely empty prefix,
  initialize once with `restic init`, then verify `restic cat config`. If the
  prefix is not proven empty and dedicated, stop rather than reinitializing it.
- A fresh repository-bound `r2-retention-lock.ok` and a matching, independently
  recovered off-host password/key escrow proof must already exist. The probe
  is safe to run before application startup, but only after Restic
  initialization. Neither proof can be created before `restic.env` exists.

## Exact first-cutover order

1. Complete the infrastructure-only preflight and exact release build. Keep
   API/worker stopped.
2. Initialize/verify the dedicated repository, run the R2 retention probe, and
   complete `record_restic_escrow.sh` from the independently recovered secret.
3. Freeze/drain Render and reconcile provider payments.
4. Run `migrate_postgres.sh`, then `migrate_redis_state.sh`, with one cutover ID
   and revision. Resolve any fail-stop marker; never bypass one.
5. Run the seed:

   ```text
   sudo env \
     LECTURESIFT_PROVIDER_CUTOVER_ID=<same-32-hex-id> \
     LECTURESIFT_EXPECTED_BUILD_REVISION=<same-40-hex-revision> \
     LECTURESIFT_FIRST_CUTOVER_SEED_CONFIRM=YES \
     LECTURESIFT_SOURCE_FROZEN=YES \
     LECTURESIFT_SOURCE_WORKER_STOPPED=YES \
     LECTURESIFT_PROVIDER_RECONCILED=YES \
     bash /opt/lecturesift/deploy/seed_first_cutover_backup.sh
   ```

   The script first opens one exported read-only PostgreSQL snapshot. Before
   `pg_dump` consumes it, the script runs the same strict
   `rehearsal_manifest.sql` and canonical line contract inside that snapshot
   as PostgreSQL migration. That fresh, snapshot-bound SHA-256 must exactly equal
   `migrated_target_manifest_sha256` in the root-only
   `postgres-cutover.ok`; a changed target fails before any backup is taken.
   The script then exports one PostgreSQL MVCC snapshot, binds both `pg_dump`
   and the recovery manifest to it, forces and validates a Redis 7.4 AOF/RDB
   checkpoint, creates the exact `lecturesift-backup-v2` payload plus the
   existing `configuration-snapshot-v1`, uploads it with the fixed production
   host/tags, reopens the full 64-hex Restic snapshot, and deletes its
   root-private plaintext staging directory before atomically writing
   `first-cutover-seed.ok`. The seed proof repeats the verified migrated-target
   manifest SHA-256 and must match the PostgreSQL proof, binding the captured
   backup to the already-proven migrated state. Plaintext cleanup is ordinary
   filesystem deletion, not a guarantee of storage-media sanitization.
6. Do not allow another snapshot writer to run. Immediately execute:

   ```text
   sudo bash /opt/lecturesift/deploy/restic_restore_rehearsal.sh
   ```

   Do not set `LECTURESIFT_RESTIC_REHEARSAL_SNAPSHOT_ID`: finalization requires
   a `current-latest` proof. The drill must restore the seed and record its
   exact snapshot ID, backup-set hash and repository hash. A different latest
   snapshot fails closed.
7. Run `finalize_provider_cutover.sh` with the same ID/revision and its explicit
   confirmation. It accepts only recovery evidence that exactly matches the
   seed proof and then atomically creates `provider-cutover.ok`. Only after that
   may normal production preflight build/start API and worker.

## Failure handling

- The shared backup lock prevents seed, normal backup, restore and rehearsal
  disk operations from racing.
- Any failure leaves the global provider-cutover fence in place and leaves
  API/worker stopped. The script's cleanup terminates its exported MVCC session,
  removes its transient manifest and deletes only its uniquely named private
  staging directory.
- A crash after immutable upload but before the seed proof can leave an extra
  unproved R2 snapshot. Do not forge a proof or delete locked data. Re-prove the
  frozen state and rerun; only the atomically recorded snapshot can satisfy the
  exact restore/finalizer gate.
- The resulting proof states that a compatible seed was uploaded and restored.
  It does not claim that Redis or R2 rollback was performed.
