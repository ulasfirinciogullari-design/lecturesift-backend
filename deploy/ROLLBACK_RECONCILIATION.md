# Render rollback reconciliation gate

This runbook closes the three gates that deliberately remain open after
`rollback_postgres_to_render.sh`: reverse Redis logical state, the Cloudflare
R2 post-cutover object delta, and payment-provider callback reconciliation.
It is for an **OVH to still-fenced Render** rollback only.

The tracked validator is evidence-only. It never changes Redis, R2, a payment
provider, DNS, Caddy, Netlify, Render, or PostgreSQL. A successful result means
that four root-private evidence documents are current and internally
consistent. It does not make an unsafe merge safe and does not authenticate a
provider export on its own.

## Stop conditions

Do not return user traffic to Render when any of these is true:

- either database, API, worker, queue, scheduler, or Instagram publisher can
  still write unexpectedly;
- an iyzico protected bank transfer, card payment, PayTR payment, refund, or
  provider retry remains pending or unmatched;
- the exact `PAYMENT_TOKEN_BINDING_SECRET` used to create a pending checkout is
  unavailable;
- the two hosts do not point to the same authoritative R2 endpoint and bucket,
  or the pre-cutover R2 inventory proof is missing;
- any R2 key referenced by the reconciled database cannot be verified;
- Redis contains queued/working jobs, processing locks, an unsupported jobs
  schema, or state beyond the reviewed `lecturesift:jobs:v2` replacement;
- a provider cannot produce or replay one genuine signed positive callback on
  the Render drain endpoint;
- evidence is older than its 30-minute validity window.

Never copy an RDB between provider/version families, guess a row/object merge,
delete a production R2 object during rollback, fabricate a signed callback, or
mark a payment paid by hand.

## Evidence layout

Use one 32-lowercase-hex rollback ID. Keep raw provider exports, inventories,
Redis captures, logs, and hashes in a separate root-only review directory. The
validator bundle itself contains exactly these four root-owned `0600` files:

```text
/var/lib/lecturesift/provider-rollback-evidence/rollback-<rollback-id>/
  context.json
  redis.json
  r2.json
  payments.json
```

The bundle directory must be root-owned `0700`, not a symlink. JSON field sets
are exact; `validate_rollback_reconciliation_bundle.py` is the authoritative
schema. Evidence files contain hashes, counts, timestamps, and booleans, not
tokens, Redis values, object keys, user identities, or payment payloads.

The PostgreSQL input must be the original root-private
`RECONCILIATION_VERIFIED` written under:

```text
/var/backups/lecturesift/postgres-rollback/postgres-rollback-<UTC>/
```

Do not copy or rename that marker into the bundle.
The marker itself contains the rollback ID, original cutover ID, exact OVH
release revision and exact Render release revision. The PostgreSQL tool derives
the cutover ID from the previously finalized provider-cutover proof, verifies
the OVH checkout and health revision, and verifies the Render health revision;
the validator rejects any disagreement with the bundle or CLI expectations.

## 0. Preserve rollback prerequisites before cutover

Before the initial Render-to-OVH cutover, capture and escrow:

1. a complete, deterministic R2 inventory that HMACs each object key and binds
   version ID (when available), size, ETag/checksum and last-modified time;
2. the HMAC manifest of every R2 key referenced by the frozen database;
3. the exact R2 endpoint/bucket bindings as SHA-256 digests;
4. configured payment providers, callback URL digests, pending-order export,
   provider transaction export and callback audit window;
5. continued root-only access to frozen Render PostgreSQL and Redis plus the
   Render worker-stop proof.

The cutover finalizer derives the configured provider set directly from the
fixed root-only runtime configuration and atomically creates
`/var/lib/lecturesift/provider-cutover/payment-provider-baseline-<cutover-id>.json`.
It contains provider names and configuration-presence metadata only—never
credentials—and binds the original cutover ID, admitted revision and exact
`provider-cutover.ok` digest. Production preflight validates this immutable
baseline before first start, so a crash after final-proof publication cannot
silently proceed without it. At least one complete production payment-provider
credential set is an explicit production requirement; partial or empty sets
fail closed.

If the baseline was not captured, do not invent it during rollback. Keep OVH
authoritative and prepare a separately reviewed recovery instead.

## 1. Establish the rollback freeze

Stop the durable reconciler before stopping either concrete ingress owner; an
enabled selector otherwise restores the owner recorded by durable state within
30 seconds. Do not reboot while it remains enabled during reconciliation. If a
reboot cannot be ruled out, disable the selector first and record that change
for the reviewed recovery plan.

```sh
sudo systemctl stop lecturesift-ingress-selector.service
sudo systemctl is-active --quiet lecturesift-ingress-selector.service && exit 1
sudo systemctl stop lecturesift-ingress.service
sudo systemctl disable --now lecturesift-instagram.timer
sudo systemctl stop lecturesift-instagram.service
sudo bash /opt/lecturesift/deploy/verify_instagram_publishers_stopped.sh
```

1. Put OVH in `drain`; block checkout creation and all ordinary writes while
   retaining exact signed callback paths.
2. Stop the OVH worker, scheduler and Instagram publisher. Prove Celery and the
   logical jobs state contain no queued/working job or processing lock.
3. Keep Render in `freeze` with its worker and all schedulers stopped.
4. Let already-issued callbacks settle on OVH. Reconcile provider exports to
   local orders until card, protected bank transfer, refund and retry windows
   contain zero pending/unmatched items.
5. Move OVH from `drain` to `freeze`. Re-prove both workers stopped and both
   queues empty. Record the freeze start in UTC.

Do not change user traffic or provider callbacks yet.
Record separately reviewed root-private stop-proof digests for both hosts'
schedulers and Instagram publishers. The context, Redis, R2 and payment
artifacts must all carry the same four digests; booleans without those bound
proofs are rejected.

## 2. Reconcile PostgreSQL

Run the existing evidence-only PostgreSQL command first. Review its dump,
schema, ACL and manifest evidence. Only when the reviewed OVH application
state is the desired source may the explicitly authorized second invocation
replace the approved application schemas on still-fenced Render.

Both invocations require these non-secret identity values in addition to the
existing six fence/confirmation flags:

```sh
LECTURESIFT_PROVIDER_ROLLBACK_ID="$ROLLBACK_ID"
LECTURESIFT_PROVIDER_CUTOVER_ID="$ORIGINAL_CUTOVER_ID"
LECTURESIFT_OVH_RELEASE_REVISION="$OVH_REVISION"
LECTURESIFT_RENDER_RELEASE_REVISION="$RENDER_REVISION"
```

The supplied cutover ID must match the immutable original cutover proof. The
OVH checkout must be clean. Both health endpoints must report their supplied
40-character revision while frozen; values typed by an operator alone are not
accepted as revision evidence.

Retain the original `RECONCILIATION_VERIFIED`; the final validator hashes it
and checks its timestamp, traffic flag and Render/OVH equality evidence. A
PostgreSQL success is not Redis, R2 or payment success.
The marker also binds the canonical reconciled database manifest and the
trusted read-only census of all provider values present in payment orders and
provider sessions. Unknown provider values stop the procedure.

## 3. Reverse the Redis logical state

This step requires a separately reviewed one-use transport because Render and
OVH use different provider/runtime boundaries. The transport must implement
all of the following; a generic `redis-cli --rdb`, `FLUSHALL`, or key scan/write
script is forbidden.

1. Read the OVH `lecturesift:jobs:v2` value twice while OVH is frozen. Treat an
   absent value as the canonical empty version-2 document. Require version 2,
   object-shaped jobs, zero queued/working jobs and zero processing locks.
2. Acquire a token-bound, expiring target write lock. Re-prove Render has no
   active broker state.
3. Capture the exact prior Render key presence and bytes into a root-private,
   fsynced rollback copy before the first write. Hash presence metadata and raw
   bytes separately.
4. Capture a confidential all-key/type/value/absolute-expiry manifest of every
   Render Redis key other than the one reviewed jobs key and migration lock.
5. Replace only `lecturesift:jobs:v2`; obtain the target provider's documented
   durability acknowledgement on the same authenticated connection.
6. Re-read OVH and Render. Require byte/canonical hashes and terminal job
   counts to match. Re-capture the non-job manifest and require equality.
7. Release the lock only by matching its random token, then prove it absent.

If any acknowledgement or comparison fails, keep traffic unchanged and the
target locked. Restore the exact prior target value/absence from the captured
copy only through the reviewed inverse operation, then re-prove it. Do not
continue with ambiguous Redis state.

`redis.json` binds the source before/after hash, target before/after hash,
rollback-copy hash, non-job manifests, terminal counts, lock checks,
durability acknowledgement and the same Render worker-stop proof used by
PostgreSQL.

## 4. Reconcile the R2 delta without deleting objects

LectureSift's live object bucket is shared and authoritative. Returning the
application to Render therefore retains post-cutover objects; it does not copy
them back to a second bucket.

1. Prove OVH and Render configurations resolve to the same endpoint and bucket
   digests. Do not place plaintext bindings in evidence JSON.
2. Under the full writer freeze, capture a complete inventory twice. Include
   HMAC key, version ID where available, size, provider checksum/ETag and
   modification time in the root-private raw manifest. Require both inventory
   digest, object count and total bytes to remain identical.
3. Diff the frozen inventory against the pre-cutover baseline. Classify added,
   modified and deleted keys. Retain every added/modified object. Do not restore
   or delete anything merely to make the old inventory reappear.
4. From the reconciled PostgreSQL snapshot, enumerate every live source,
   result, download, slide and artifact reference. HEAD each exact key with the
   Render runtime credential and record a secret-free verification manifest.
   Every reference must resolve. A key deleted since cutover is acceptable only
   when the reconciled database contains no reference to it.
5. In `migration-probe/<rollback-id>/` only, prove write/read/hash/delete with
   the Render credential and prove the probe key absent. Never use a production
   job prefix for this check.
6. Re-prove application-bucket versioning/retention evidence. Restic backup
   bucket evidence is not a substitute for live-object-bucket evidence.

`r2.json` requires the two frozen inventories to match, every database
reference to resolve, all post-cutover additions/modifications to be retained,
zero reconciliation copy/delete operations and zero production-object
mutations.
It also carries the reconciled PostgreSQL manifest digest, proving that the
database-reference manifest was derived for the same frozen data plane.

## 5. Hand payment callbacks to Render and reconcile

Keep OVH frozen, both workers stopped, both queues empty and checkout creation
blocked. Put Render into `drain`; this permits only the exact signed payment
callback routes while ordinary writes remain blocked.

For each configured provider, one at a time:

1. Export provider events from before the freeze through the provider's full
   retry/pending horizon. Hash the untouched root-private export.
2. Export local orders and provider-session bindings before callback handover.
   Correlate reference, provider, method, amount, currency and terminal status.
   Protected iyzico transfers are asynchronous and must be included.
3. Change only that provider's callback URL to the HTTPS Render endpoint. Keep
   user traffic unchanged.
4. Use the provider's own resend/test mechanism to deliver one genuine signed
   positive event. Confirm Render accepts it, invalid signatures are rejected,
   and replaying the same provider event is idempotent. Do not manufacture an
   HMAC/signature locally as the positive proof.
5. Export provider and local state again. Require every examined event to
   match, zero amount/currency differences, zero pending provider/local
   payments, zero unmatched events/orders and zero accepted signature failure.
6. Hash the provider configuration, exports, local manifests and callback audit
   without copying raw payloads into the four-file validator bundle.

If a provider cannot send a genuine signed event, restore its callback to the
still-authoritative side only while that side is in callback-only `drain`, and
stop the rollback. Never route callbacks to two writable databases.

`payments.json` lists exactly the reconciled provider census in sorted order.
The validator derives that required set as the union of the immutable original
cutover baseline, the current fixed root-only runtime configuration, and every
provider value found by the trusted frozen PostgreSQL order/session census.
Place that same set in `context.json.expected_configured_providers`; it is not
supplied through an operator CLI flag. Thus a later-enabled PayTR configuration
or a historical provider cannot be omitted by coordinating the bundle files.
Every provider requires at least one matched event and one idempotent duplicate
replay. Each provider result carries the same reconciled database-manifest
digest. Manual and asynchronous transfer pending counts must be zero.

## 6. Validate the bound evidence

Close the evidence window only after Redis, R2 and every callback provider
passes. `valid_until_utc` may be at most 30 minutes after
`evidence_closed_at_utc`. Compute the raw file hashes first, put those hashes in
`context.json`, and leave `user_traffic_changed=false`.

Run from the exact reviewed release checkout:

```sh
sudo python3 deploy/validate_rollback_reconciliation_bundle.py \
  --bundle "/var/lib/lecturesift/provider-rollback-evidence/rollback-$ROLLBACK_ID" \
  --postgres-marker "/var/backups/lecturesift/postgres-rollback/$POSTGRES_RUN/RECONCILIATION_VERIFIED" \
  --provider-baseline "/var/lib/lecturesift/provider-cutover/payment-provider-baseline-$ORIGINAL_CUTOVER_ID.json" \
  --provider-cutover-proof "/var/lib/lecturesift/provider-cutover/provider-cutover.ok" \
  --expected-rollback-id "$ROLLBACK_ID" \
  --expected-cutover-id "$ORIGINAL_CUTOVER_ID" \
  --expected-ovh-revision "$OVH_REVISION" \
  --expected-render-revision "$RENDER_REVISION"
```

The baseline/proof paths are fixed in production, and the current provider
configuration is always read from `/etc/lecturesift/runtime.env`; neither can
be redirected by a CLI option.

Capture its single-line, secret-free JSON output into a new root-owned `0600`
file using a temporary file plus fsync/atomic rename. Do not use shell `source`
on JSON or raw provider artifacts. Any rejection means traffic remains
unchanged.

## 7. Reviewed traffic switch and observation

The readiness proof says `ready-for-reviewed-user-traffic-switch`; it does not perform that switch.

1. Re-check that the proof has not expired and each artifact hash still
   matches.
2. Switch the stable API/frontend origin to Render. Change DNS only if the
   stable hostname itself moves. Keep OVH frozen and available.
3. Remove Render drain only after the public health, account/session, upload,
   R2 read/write, OCR/PDF/PPTX/video, admin and payment-callback checks pass.
4. Start exactly one Render worker and prove recovery/queue health. Keep every
   Instagram publisher disabled until explicitly selected on one host.
5. Observe payment callbacks, R2 operations, queues and logs through the full
   provider retry horizon. Re-run provider/local reconciliation before deleting
   any OVH rollback source.

On any failure, stop. Do not alternate traffic between diverging databases. If
Render was not made writable, return the callback URL to callback-only OVH and
keep user traffic on OVH. If Render was made writable, freeze both again and
start a new rollback ID and evidence cycle; never reuse the old proof.
