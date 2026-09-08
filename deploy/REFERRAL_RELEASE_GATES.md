# Referral preview contract (not production-enabled)

This implementation is inert in the normal release. The environment flag
`LECTURESIFT_REFERRALS_ENABLED` defaults off, and the source-controlled
`SCHEMA_RECOVERY_RELEASE_READY = False` capability cannot be overridden by
environment configuration. Even an existing referral schema plus a true flag
does not activate it. The explicit migration entry point refuses DDL while
the capability is false. Never create these tables manually on the current
production database: recovery manifest v2 rejects unexpected tables.

## Rules implemented in the isolated preview

- A verified inviter creates a random public code. Only a brand-new account
  may bind it in the registration transaction. Existing users cannot attach
  a code later. Both accounts must remain verified and open.
- The invitee's first successful paid Lite/Plus/Pro/Max subscription and later
  paid subscription purchases/renewals qualify; free, the 1-TRY test, top-ups,
  failed/pending orders and admin grants do not. The owner selected this scope
  on 8 September 2026. The invitee must have verified email before payment.
  An annual purchase produces one payment event, not twelve monthly rewards;
  quota refreshes never qualify. This does not introduce automatic card charging.
- Reserve at most five reward purchases per inviter per UTC calendar month,
  shared by first purchases and renewals. Reserved slots remain consumed if
  subsequently blocked. Reservation month is when qualification is recorded
  (also for delayed operator repair). A sixth event receives `cap_reached`;
  it is not carried forward. Each invitee can reserve at most one renewal
  event per UTC month; further events retain `monthly_limit` without a reward.
- On the first purchase the inviter chooses 60 bonus minutes OR a 10% coupon
  capped at 50 TRY; the invitee receives 30 minutes once. On subsequent eligible
  purchases the inviter chooses 30 minutes OR a 5% coupon capped at 25 TRY;
  no repeat invitee bonus is granted. These renewal amounts are a pre-release
  implementation draft, not activated commercial terms. Choice is editable
  only while pending, never after release. Coupons are noncash, account-bound,
  single-use and valid for 90 days after release. New rewards support a chosen
  coupon currency; redemption requires that exact currency and a monthly
  subscription. Annual billing, test/free/top-ups and stacked coupons do not
  qualify for redemption. Legacy v1 coupons remain TRY-only.
- No spendable reward is issued automatically: 14 days must elapse and an
  administrator must explicitly confirm provider reconciliation with a
  non-secret evidence reference. Refund requests (unless rejected),
  account closure blocks release. V1 first-purchase cancellation/replacement/
  expiry rules are preserved. For renewals, normal replacement or expiry of
  the purchased term does not erase a valid paid event; cancelled subscriptions,
  refund requests and unverified/closed accounts still block release.
- A coupon is reserved atomically with the server-priced order and immutable
  purchase terms. Pending provider results retain the reservation. A terminal
  failed/rejected order may release it; a rejected manual order cannot later
  be approved. Successful orders consume it once. Coupon initialization never
  silently falls back to full price.

## API

User authentication is the existing Bearer session. GET
`/billing/referrals` returns `{ok:true, referrals:{enabled,...}}` and is read-only.
POST `/billing/referrals/code` creates the code; the returned link uses
`register.html?ref=LSR-...`. Registration accepts optional `referral_code`.
Invalid/unavailable attribution does not break signup and is reported as
`referral_status`; there is no later attribution route.

POST `/billing/referrals/rewards/{id}/choice` accepts
`{"reward_choice":"minutes"}` or `{"reward_choice":"coupon","currency":"EUR"}`.
The optional currency selects coupon terms while pending; it is fixed at
release. Available checkout currencies are reported separately from the
22-currency policy catalog. Unsupported coupon issuance fails closed while
the minute option remains available; a currency is never silently substituted.
Both `/billing/checkout` and `/billing/manual-transfer/orders` accept optional
`coupon_code`. Prices are always recomputed by the server.

The existing ADMIN_ADMIN Bearer guard protects GET `/billing/admin/referrals`,
POST `/billing/admin/referrals/{id}/release` (requires boolean
`provider_reconciled:true` and `evidence_reference`), and POST
`/billing/admin/referrals/reconcile-order` (`order_reference`). The last
operation only repairs deferred qualification/coupon reconciliation; it cannot
release credits. It also repairs reservations for known terminal failed or
rejected orders; it never treats a failed order as a paid qualification.
Repeated release/payment callbacks cannot grant twice.

Referral processing is a separate transaction after the payment commits.
A referral failure cannot roll back or obscure the payment. A callback retry
or explicit administrator reconciliation repairs a deferred ledger entry.
Do not put tokens, card details, personal data or provider response bodies in
the reconciliation reference.

Summary history contains only the latest 50 rewards and coupons, with
`has_more_rewards` / `has_more_coupons` flags. Totals are SQL aggregates over
all qualifying history. `earned_minutes` is lifetime referral credits added,
not the account's currently spendable balance; pending minutes are not usable.

## Exact schema and activation work remaining

Separate metadata in `lecturesift/referrals.py` declares:

1. `billing_referral_codes`: user PK, unique random code, creation time.
2. `billing_referral_rewards`: UUID PK, unique invitee, indexed inviter,
   unique nullable payment reference, immutable policy/minute amounts,
   state/choice, UTC reservation month and qualification/hold/release times,
   administrator reconciliation reference.
3. `billing_referral_coupons`: random code PK, indexed owner, unique reward
   and nullable order references, bounded percent/cap/currency, reservation/
   consumption state and discount, creation/expiry/use times.
4. `billing_referral_renewal_rewards`: separate per-payment renewal history,
   deterministic `rr-` ID namespace, unique order reference, immutable policy
   and minute amounts, reservation month and a unique invitee/month slot.
   V1 reward rows remain the permanent registration attribution and first
   purchase history; their unique invitee constraint is not removed.
5. `billing_referral_reward_preferences`: selected coupon currency keyed by
   reward ID, separate from historical reward table definitions.

`lecturesift/referral_policy.py` preserves immutable legacy first, regional
first and renewal versions. Frozen regional caps cover 22 catalog currencies;
they are based on the 8 September Lite price ratios, not live exchange rates.
TRY first/renewal caps remain 50/25, USD 1.50/0.75, EUR 1.41/0.70;
JPY 188/94 and KRW 1702/851 use whole minor units. These are application policy
amounts, not claims of current FX equivalence or provider availability.
Shared coupons resolve their source by its ID namespace and validate against
that source's policy, so an older earned coupon retains its original terms.
Summary/history, administrator release, account export/closure and rehearsal
cleanup must cover both ledgers and currency preferences. Legacy three-table
exports and closures remain available after disabling the program; the
five-table runtime must fail closed when the new schema is missing or
incompatible. Language and chosen currency are independent. API configuration
capabilities are not proof of actual successful multi-currency settlement.

The exact column types/nullability are in that module. No foreign keys enter
the core billing metadata; registration, close, secret-free export and
proof-bound rehearsal purge have explicit hooks.

Before activation, a separate reviewed release MUST:

1. Add immutable cutover schema/verifier v4 and recovery v3 inventories and
   contracts for these exact five tables, preserving all historical versions.
2. Route backup/restore/rehearsal/rollback and configuration snapshots by the
   new versions; add API grants, keep worker access absent, and validate roles.
3. Prove actual PostgreSQL 18 DDL, concurrent shared cap/per-invitee month/
   coupon/release behavior, migration conservation of v1 rows and coupons,
   backup restoration, close/export and E2E cleanup. `create_all` alone is
   not evidence of an upgrade or preservation of existing data.
4. Flip the source capability only in that reviewed release; explicitly
   migrate with owner authority before opting in through the runtime flag.

The current guarded `deploy/migrate_referrals.py` entry point still has its
historical `--confirm-referral-schema-v1` interface. It is not the versioned
upgrade required above and must not be used as a shortcut to enable five tables.
The activation review must also establish a payment baseline/start boundary:
late reconciliation of old paid orders must not silently advertise or introduce
retroactive renewal rewards. The preview currently classifies paid orders after
registration attribution; a production campaign start has not been established.

## Residual cost and fraud risk

Each credited pair costs 90 processing minutes, or 30 processing minutes plus
at most 50 TRY coupon face value. Five pairs cap an inviter's monthly benefit
at 300 minutes or 250 TRY face value; mixed choices share the same five slots.
Including all five invitee rewards, that inviter's five pairs can cost up to
450 processing minutes, or 150 processing minutes plus 250 TRY coupon face
value; mixed choices remain bounded by the same five-pair count.
This is NOT a site-wide campaign budget. Different verified emails can belong
to one person; email verification alone does not prove different humans.
The operator must check self-referral/payment abuse during reconciliation.

There is no automatic provider refund/chargeback clawback today. A later
chargeback can leave already-spent bonus minutes unrecoverable. A refund
record blocks an unused coupon at redemption, but it does not magically
recover prior consumption. The hold and manual check reduce, not eliminate,
this risk. Do not advertise guaranteed immediate rewards, global cost limits,
automatic cash refunds or a fully activated production program.

Each renewal event instead grants 30 inviter minutes or at most 25 TRY coupon
face value, with no invitee minutes. Renewals share the same five-event monthly
cap; they do not add a second allowance on top of the original limit. A mixed
month is bounded by the larger first-purchase scenarios above. This still is
not a site-wide budget, a net-profit calculation or automatic fraud prevention.
The TRY scenarios above must not be summed directly with foreign-currency
coupon amounts; actual accounting requires separately verified settlement and
cost data. Merely displaying localized caps does not produce financial evidence.
