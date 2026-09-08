# Product release operations

Source is being verified in PR67. Neither capability flag is opened by these
changes. Current production API is Render; the development OS account cannot
read its database/provider credentials and has no passwordless sudo. A health
response is not migration, restore or provider-access evidence.

The release adds exactly five referral and three assistant tables. The frozen
v4 catalog contract includes PostgreSQL 18 columns, NOT NULL constraints, unique
constraints and indexes. `product_schema_release.migrate` validates the frozen
before manifest, locks existing tables, compares schema and row fingerprints,
adds only absent whole tables, verifies the resulting transition, and commits
all DDL together. An incompatible existing table or stale row rejects the run.
Historical cutover v2/v3 and recovery v1/v2 files retain their exact meaning.

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
is enabled. Image/video analysis is bounded; image/video generation is a separate
unfinished capability and is not advertised as available.
