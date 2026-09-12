# Product release operations

PR67 was merged and deployed as `2b7f3a34fba5322452ae91361d838635ddfec477`
on September 9, 2026. Netlify published the frontend at 01:00 UTC; Render API and
worker deployments completed at 01:02 UTC. The API activation deployment
completed at 01:06 UTC. Chat, image creation, assistant credit sales and recurring
referrals are now enabled on the API. Migration tools never open capability
flags themselves. Private release credentials and evidence remain outside the
repository. Health checks, real provider requests and database evidence are
distinct checks; none proves an actual paid checkout.

The release adds exactly five referral and three assistant tables. The frozen
v4 catalog contract includes PostgreSQL 18 columns, NOT NULL constraints, unique
constraints and indexes. `product_schema_release.migrate` validates the frozen
before manifest, locks existing tables, compares schema and row fingerprints,
adds only absent whole tables, verifies the resulting transition, and commits
all DDL together. An incompatible existing table or stale row rejects the run.
Historical cutover v2/v3 and recovery v1/v2 files retain their exact meaning.

## Managed Render release

The Render database started with 24 core tables. At 00:45 UTC on September 9,
the reviewed release added purchase terms and all eight product tables in one
transaction. The real before/after v4 manifests prove that every existing row
and schema object was preserved. A managed export dated 00:46 UTC is available.
Do not apply the OVH host/container cutover scripts to this deployment.

`python -m deploy.psql_product_release --confirm-product-schema-v1
--add-legacy-purchase-terms --evidence-dir /private/fresh-evidence` uses a private
owner `DATABASE_URL` and the existing psql client, with certificate verification.
It captures both manifests while holding one transaction and bounded table
locks, adds only the nine frozen tables, and checks every old row fingerprint
and schema object before committing. No existing record is rewritten. The
legacy purchase-terms addition requires its separate explicit option. A failed
check closes the connection and rolls back all additions; a lost commit
acknowledgement requires a fresh database inspection before retrying.

For Render, retain the private service configuration snapshot and managed
logical export before migration. Take another export after migration and verify
the strict v4 manifest. Managed PITR remains the production recovery mechanism;
no customer database is copied into CI. The allocated synthetic PostgreSQL 18
CI service exercises actual dump/restore of all product ledgers, pending
reservations, cached answers and old coupons. This proves the versioned recovery
contract; it is not a claim that a production restore has been performed.

Distinct API and worker logins now pass real read-only login/authority checks:
the API has CRUD access to the eight product tables; the worker has none and
sees masked account fields through the eleven reviewed compatibility views.
Neither login owns the database, creates schema/temp objects, inherits roles,
has administrative attributes or accesses the retained verification table.
The managed owner cannot ALTER superuser/replication/bypass-RLS attributes, so
their false CREATE ROLE defaults are preserved and verified instead. Passwords
use SCRAM verifiers. External checks retain full TLS certificate verification
with channel binding disabled for Render's TLS gateway.

Both running services now use the distinct logins and
`LECTURESIFT_PRODUCT_SCHEMA_VERSION=1`. Original owner credentials are retained
only in private release configuration. The API has `ASSISTANT_ENABLED=true`,
`ASSISTANT_IMAGES_ENABLED=true`, `LECTURESIFT_REFERRALS_ENABLED=true` and a
permanent timezone-qualified referral campaign boundary. The worker's product
flags remain false. A real isolated Render worker job verified its internal
database login, masked search path, denied product-table access and successful
read-only billing/rollout/cost initialization. The processing queue was empty
before the release; the deployed worker is reachable.

CI run 34295667581 at c7491bd verified 1,224 tests and the browser checks,
including nine-table managed migration, complete rollback on rejection, and
actual synthetic PostgreSQL 18 dump/restore. Final activation run 34296969142
at 52aef40 passed 1,224 tests (3 skips) and 34 browser checks (2 skips). The
squashed live commit has the same tree. Live assistant/theme files and the
official iyzico artwork match the reviewed source bytes. The live catalog
advertises chat and 200-credit image creation. A real guest request at 01:08 UTC
returned a Turkish site-guidance answer, a registration action and zero charged
credits. Real provider checks separately returned text and one valid JPEG.
These results do not claim a live paid checkout, production restore or a
successful YouTube download. See `YOUTUBE_DOWNLOAD_SUPPORT.md` for the separate
media access evidence.

## Reviewable activation sequence

1. Use the real database owner's private release environment. Freeze/drain the
   API and stop workers with the existing release controls. Reconcile outstanding
   payments and take a current verified backup. Do not put credentials in argv,
   logs, this document or chat. The old core migration must already be complete.
2. Run `deploy/rehearsal_manifest_v4.sql` with psql's
   `LECTURESIFT_ALLOW_LEGACY_PRODUCT_TABLES=on` into a private before file. This
   option permits absent product tables; it does not permit malformed ones.
3. From the release checkout, run
   `python -m deploy.product_schema_release --confirm-product-schema-v1 --before /private/before-v4.txt --evidence-dir /private/fresh-evidence`.
   Both `ASSISTANT_ENABLED` and `LECTURESIFT_REFERRALS_ENABLED` remain false.
   The evidence directory must be private and empty of prior output. Any failure
   rolls back the table additions; never repair by manually creating a few tables.
4. Reapply the existing `postgres-app-role.sh` runtime grants. Verify that the API
   can use all eight tables and the worker cannot read or modify them. Run the
   strict v4 manifest and verifier again. The old provider cutover/rollback scripts
   remain core-only and deliberately reject this expanded inventory; do not use
   them as product migration or downgrade tools.
5. Set private runtime `LECTURESIFT_PRODUCT_SCHEMA_VERSION=1` so normal backups
   select recovery v3. The restore and restore-rehearsal commands recognize v1,
   v2 and v3 metadata; the configuration snapshot format v3 preserves all new
   release contracts while still verifying previous v1/v2 snapshots.
6. Take a product-aware backup and restore it into the dedicated rehearsal
   environment. Compare schema and all row fingerprints, including cached
   answers, in-flight reservations, old coupons and unused credits. Validate
   owner export/erasure, refund and recurring-reward concurrency there.
7. Run `python -m deploy.assistant_provider_probe` in the private release account.
   It makes one model request capped at 16 output tokens and reports usage only.
   It must succeed before chat is offered. Ensure that API logs are monitoring
   the maintenance loop's error/full-batch alerts.
8. Set a permanent timezone-qualified `LECTURESIFT_REFERRAL_CAMPAIGN_START_AT`.
   Only orders created at/after this boundary can newly qualify; later callback
   reconciliation must not manufacture rewards for older purchases.
9. Open the source capabilities in a reviewed release only after these checks,
   then opt into the two runtime flags. Verify guest trial, owned chat, included
   credits, top-up settlement, coupon redemption and subscription renewal using
   the actual deployment. Respect existing purchase snapshots throughout.

The API lifespan contains the minute-by-minute cache maintenance schedule; no
new paid scheduler is needed. Disabled chat still prunes after schema capability
is enabled. Image/video analysis is bounded. Image creation is now a separately switched,
owned 200-credit action using one 1024x1024 medium-quality JPEG. It requires a
real `python -m deploy.assistant_provider_probe --image` check before setting
`ASSISTANT_IMAGES_ENABLED=true`; chat
access alone does not prove image access. The real September 9 provider probe
returned one valid 46,344-byte JPEG with reported usage; synthetic tests remain
separate from that provider-access evidence.
See `docs/ASSISTANT_IMAGE_RELEASE.md` for cost and retention details.

Video generation remains unavailable. OpenAI has announced the Sora/Videos API
shutdown for September 24, 2026 with no replacement listed:
https://developers.openai.com/api/docs/deprecations . A durable video provider
with verified account access, pricing and owned asynchronous job recovery must
be selected before offering paid video generation.
