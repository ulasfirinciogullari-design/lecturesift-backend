\set ON_ERROR_STOP on
\set QUIET on
\pset tuples_only on
\pset format unaligned

-- The migration rehearsal can inspect the currently deployed, pre-release
-- schema before the new payment-provider session table exists.  Every other
-- caller remains strict by default, and the rehearsal must run this manifest
-- again without the override after applying the current schema migration.
\if :{?LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS}
\else
\set LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS off
\endif

SELECT 'DATABASE|' || (current_setting('server_version_num')::integer / 10000) || '|' ||
       pg_encoding_to_char(encoding) || '|' || datcollate || '|' || datctype || '|' ||
       datlocprovider::text || '|' || coalesce(datcollversion, '') || '|' ||
       CASE WHEN current_setting('TimeZone') IN ('UTC', 'Etc/UTC', 'GMT')
            THEN 'UTC' ELSE current_setting('TimeZone') END
FROM pg_database
WHERE datname = current_database();

SELECT 'DATABASE_SIZE|' || pg_database_size(current_database());

WITH expected(table_name) AS (
  VALUES
    ('billing_users'),
    ('billing_user_profiles'),
    ('billing_user_preferences'),
    ('billing_auth_tokens'),
    -- Preserved legacy table from the first verification implementation. The
    -- current application no longer writes it, but migration must not discard
    -- its audit/history row without a separate retention decision.
    ('billing_email_verifications'),
    ('billing_subscriptions'),
    ('billing_manual_orders'),
    ('billing_payment_orders'),
    ('billing_payment_provider_sessions'),
    ('billing_payment_consents'),
    ('billing_usage_events'),
    ('lecturesift_guest_trials'),
    ('lecturesift_instagram_rewards'),
    ('lecturesift_rewarded_ad_claims'),
    ('lecturesift_email_change_requests'),
    ('lecturesift_runtime_metrics'),
    ('lecturesift_admin_credit_events'),
    ('lecturesift_admin_account_events'),
    ('lecturesift_account_activity'),
    ('lecturesift_refund_requests'),
    ('lecturesift_contact_messages'),
    ('lecturesift_contact_replies'),
    ('lecturesift_cost_events'),
    ('lecturesift_cost_actuals')
),
actual AS (
  SELECT table_name
  FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
)
SELECT 'TABLE_DIFF|missing|' || e.table_name
FROM expected e LEFT JOIN actual a USING (table_name)
WHERE a.table_name IS NULL
  AND NOT (
    e.table_name = 'billing_payment_provider_sessions'
    AND :'LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS'::boolean
  )
UNION ALL
SELECT 'TABLE_DIFF|unexpected|' || a.table_name
FROM actual a LEFT JOIN expected e USING (table_name)
WHERE e.table_name IS NULL
ORDER BY 1;

-- Emit both the compact fingerprint and the canonical object inventory from
-- one catalog expression so the two forms of evidence cannot drift apart.
-- SCHEMA_OBJECT records contain metadata only, never row data or credentials.
WITH objects(item) AS (
  SELECT format(
    'C|%I.%I|%s|%I|%s|%s|%s|%s|%s',
    n.nspname, c.relname, a.attnum, a.attname,
    format_type(a.atttypid, a.atttypmod), a.attnotnull,
    coalesce(pg_get_expr(d.adbin, d.adrelid), ''), a.attidentity, a.attgenerated
  )
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid
  LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
  WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    AND a.attnum > 0 AND NOT a.attisdropped
  UNION ALL
  SELECT format('K|%s|%I|%s|%s', conrelid::regclass::text, conname,
                convalidated, pg_get_constraintdef(oid, true))
  FROM pg_constraint WHERE connamespace = 'public'::regnamespace
  UNION ALL
  SELECT format('I|%I|%I|%s', tablename, indexname, indexdef)
  FROM pg_indexes WHERE schemaname = 'public'
), output(sort_key, line) AS (
  SELECT 0,
         'SCHEMA|' || count(*) || '|' ||
         coalesce(md5(string_agg(item, E'\n' ORDER BY item)), '')
  FROM objects
  UNION ALL
  SELECT 1, 'SCHEMA_OBJECT|' || item
  FROM objects
)
SELECT line FROM output ORDER BY sort_key, line;

SELECT format(
  'SELECT %L || ''|'' || count(*)::bigint || ''|'' || '
  'coalesce(bit_xor(hashtextextended(to_jsonb(t)::text, 0)), 0) || ''|'' || '
  'coalesce(sum(hashtextextended(to_jsonb(t)::text, 0)::numeric), 0) '
  'FROM %I.%I t;',
  'TABLE|' || table_name, table_schema, table_name
)
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name
\gexec

SELECT 'ANOMALY|negative_user_credit|' || count(*)
FROM billing_users WHERE credit_minutes < 0
UNION ALL
SELECT 'ANOMALY|case_insensitive_duplicate_email|' || count(*)
FROM (SELECT lower(email) FROM billing_users GROUP BY lower(email) HAVING count(*) > 1) x
UNION ALL
SELECT 'ANOMALY|invalid_subscription_period|' || count(*)
FROM billing_subscriptions WHERE ends_at <= starts_at
UNION ALL
SELECT 'ANOMALY|multiple_active_subscriptions|' || count(*)
FROM (
  SELECT user_id FROM billing_subscriptions WHERE status = 'active'
  GROUP BY user_id HAVING count(*) > 1
) x
UNION ALL
SELECT 'ANOMALY|negative_usage_minutes|' || count(*)
FROM billing_usage_events WHERE minutes <= 0
UNION ALL
SELECT 'ANOMALY|negative_manual_amount|' || count(*)
FROM billing_manual_orders WHERE amount_minor < 0
UNION ALL
SELECT 'ANOMALY|manual_timestamp_reversal|' || count(*)
FROM billing_manual_orders WHERE updated_at < created_at
UNION ALL
SELECT 'ANOMALY|negative_payment_amount|' || count(*)
FROM billing_payment_orders WHERE amount_minor < 0 OR provider_amount_minor < 0
UNION ALL
SELECT 'ANOMALY|payment_timestamp_reversal|' || count(*)
FROM billing_payment_orders WHERE updated_at < created_at
UNION ALL
SELECT 'ANOMALY|paid_provider_amount_mismatch|' || count(*)
FROM billing_payment_orders
WHERE status = 'paid' AND provider_amount_minor IS DISTINCT FROM amount_minor
UNION ALL
SELECT 'ANOMALY|paid_card_plan_without_subscription|' || count(*)
FROM billing_payment_orders o
WHERE o.status = 'paid' AND o.plan_code <> 'credit'
  AND NOT EXISTS (
    SELECT 1 FROM billing_subscriptions s WHERE s.source_reference = o.reference
  )
UNION ALL
SELECT 'ANOMALY|paid_manual_plan_without_subscription|' || count(*)
FROM billing_manual_orders o
WHERE o.status = 'paid' AND o.plan_code <> 'credit'
  AND NOT EXISTS (
    SELECT 1 FROM billing_subscriptions s WHERE s.source_reference = o.reference
  )
UNION ALL
SELECT 'ANOMALY|credit_audit_math_error|' || count(*)
FROM lecturesift_admin_credit_events
WHERE balance_after <> balance_before + minutes_delta
   OR balance_before < 0 OR balance_after < 0
UNION ALL
SELECT 'ANOMALY|negative_cost_event|' || count(*)
FROM lecturesift_cost_events WHERE quantity_microunits < 0 OR cost_microusd < 0
UNION ALL
SELECT 'ANOMALY|invalid_cost_period|' || count(*)
FROM lecturesift_cost_actuals
WHERE period_end < period_start OR subtotal_minor < 0 OR tax_minor < 0
ORDER BY 1;

-- Avoid parsing a relation that legitimately does not exist in the pre-release
-- source schema.  Missing-table compatibility is never reported as a zero
-- anomaly: it is emitted as an explicit SCHEMA_COMPAT record.  In strict mode
-- the same absence produces both TABLE_DIFF and a non-zero anomaly.
SELECT CASE WHEN to_regclass('public.billing_payment_provider_sessions') IS NULL
            THEN 'off' ELSE 'on' END AS payment_provider_sessions_present
\gset
\if :payment_provider_sessions_present
SELECT 'ANOMALY|invalid_payment_provider_token_digest|' || count(*)
FROM billing_payment_provider_sessions
WHERE token_digest !~ '^[0-9a-f]{64}$'
UNION ALL
SELECT 'ANOMALY|payment_provider_session_mismatch|' || count(*)
FROM billing_payment_provider_sessions s
JOIN billing_payment_orders o ON o.reference = s.order_reference
WHERE s.provider <> o.provider
ORDER BY 1;
\else
SELECT CASE
         WHEN :'LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS'::boolean
           THEN 'SCHEMA_COMPAT|legacy_missing_table|billing_payment_provider_sessions|integrity_checks_deferred_to_current_schema_migration'
         ELSE 'ANOMALY|required_payment_provider_sessions_table_missing|1'
       END;
\endif

SELECT 'STATUS|subscription|' || status || '|' || count(*)
FROM billing_subscriptions GROUP BY status
UNION ALL
SELECT 'STATUS|manual_order|' || status || '|' || count(*)
FROM billing_manual_orders GROUP BY status
UNION ALL
SELECT 'STATUS|payment_order|' || status || '|' || count(*)
FROM billing_payment_orders GROUP BY status
UNION ALL
SELECT 'STATUS|refund|' || status || '|' || count(*)
FROM lecturesift_refund_requests GROUP BY status
UNION ALL
SELECT 'STATUS|contact|' || status || '|' || count(*)
FROM lecturesift_contact_messages GROUP BY status
UNION ALL
SELECT 'STATUS|rewarded_ad|' || status || '|' || count(*)
FROM lecturesift_rewarded_ad_claims GROUP BY status
ORDER BY 1;

SELECT 'UNVALIDATED_FK|' || conrelid::regclass::text || '|' || conname
FROM pg_constraint
WHERE contype = 'f' AND NOT convalidated
ORDER BY 1;

-- Terminal completeness record. Consumers must verify that this is the final
-- record and that every declared family count exactly matches the preceding
-- output. This makes a truncated manifest, an older query, or a silently
-- omitted table/anomaly fail closed even when the remaining lines look valid.
WITH expected(table_name) AS (
  VALUES
    ('billing_users'),
    ('billing_user_profiles'),
    ('billing_user_preferences'),
    ('billing_auth_tokens'),
    ('billing_email_verifications'),
    ('billing_subscriptions'),
    ('billing_manual_orders'),
    ('billing_payment_orders'),
    ('billing_payment_provider_sessions'),
    ('billing_payment_consents'),
    ('billing_usage_events'),
    ('lecturesift_guest_trials'),
    ('lecturesift_instagram_rewards'),
    ('lecturesift_rewarded_ad_claims'),
    ('lecturesift_email_change_requests'),
    ('lecturesift_runtime_metrics'),
    ('lecturesift_admin_credit_events'),
    ('lecturesift_admin_account_events'),
    ('lecturesift_account_activity'),
    ('lecturesift_refund_requests'),
    ('lecturesift_contact_messages'),
    ('lecturesift_contact_replies'),
    ('lecturesift_cost_events'),
    ('lecturesift_cost_actuals')
),
actual AS (
  SELECT table_name
  FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
),
schema_objects AS (
  SELECT format(
    'C|%I.%I|%s|%I|%s|%s|%s|%s|%s',
    namespace.nspname, relation.relname, attribute.attnum, attribute.attname,
    format_type(attribute.atttypid, attribute.atttypmod),
    attribute.attnotnull,
    coalesce(pg_get_expr(default_value.adbin, default_value.adrelid), ''),
    attribute.attidentity, attribute.attgenerated
  ) item
  FROM pg_class relation
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
  LEFT JOIN pg_attrdef default_value
    ON default_value.adrelid = relation.oid
   AND default_value.adnum = attribute.attnum
  WHERE namespace.nspname = 'public'
    AND relation.relkind IN ('r', 'p')
    AND attribute.attnum > 0
    AND NOT attribute.attisdropped
  UNION ALL
  SELECT format(
    'K|%s|%I|%s|%s', constraint_entry.conrelid::regclass::text,
    constraint_entry.conname, constraint_entry.convalidated,
    pg_get_constraintdef(constraint_entry.oid, true)
  )
  FROM pg_constraint constraint_entry
  WHERE constraint_entry.connamespace = 'public'::regnamespace
  UNION ALL
  SELECT format('I|%I|%I|%s', index_entry.tablename,
                index_entry.indexname, index_entry.indexdef)
  FROM pg_indexes index_entry
  WHERE index_entry.schemaname = 'public'
),
table_diffs AS (
  SELECT 'missing|' || expected.table_name item
  FROM expected
  LEFT JOIN actual USING (table_name)
  WHERE actual.table_name IS NULL
    AND NOT (
      expected.table_name = 'billing_payment_provider_sessions'
      AND :'LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS'::boolean
    )
  UNION ALL
  SELECT 'unexpected|' || actual.table_name
  FROM actual
  LEFT JOIN expected USING (table_name)
  WHERE expected.table_name IS NULL
),
status_records AS (
  SELECT status FROM billing_subscriptions WHERE status IS NOT NULL GROUP BY status
  UNION ALL
  SELECT status FROM billing_manual_orders WHERE status IS NOT NULL GROUP BY status
  UNION ALL
  SELECT status FROM billing_payment_orders WHERE status IS NOT NULL GROUP BY status
  UNION ALL
  SELECT status FROM lecturesift_refund_requests WHERE status IS NOT NULL GROUP BY status
  UNION ALL
  SELECT status FROM lecturesift_contact_messages WHERE status IS NOT NULL GROUP BY status
  UNION ALL
  SELECT status FROM lecturesift_rewarded_ad_claims WHERE status IS NOT NULL GROUP BY status
),
counts AS (
  SELECT
    (SELECT count(*) FROM schema_objects) schema_object_count,
    (SELECT count(*) FROM actual) table_count,
    (SELECT count(*) FROM table_diffs) table_diff_count,
    15 + CASE
      WHEN to_regclass('public.billing_payment_provider_sessions') IS NOT NULL
        THEN 2
      WHEN NOT :'LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS'::boolean
        THEN 1
      ELSE 0
    END anomaly_count,
    (SELECT count(*) FROM status_records) status_count,
    CASE
      WHEN to_regclass('public.billing_payment_provider_sessions') IS NULL
       AND :'LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS'::boolean
        THEN 1
      ELSE 0
    END schema_compat_count,
    (
      SELECT count(*) FROM pg_constraint
      WHERE contype = 'f' AND NOT convalidated
    ) unvalidated_fk_count
)
SELECT
  'MANIFEST_COMPLETE|v2' ||
  '|DATABASE|1' ||
  '|DATABASE_SIZE|1' ||
  '|SCHEMA|1' ||
  '|SCHEMA_OBJECT|' || schema_object_count ||
  '|TABLE|' || table_count ||
  '|TABLE_DIFF|' || table_diff_count ||
  '|ANOMALY|' || anomaly_count ||
  '|STATUS|' || status_count ||
  '|SCHEMA_COMPAT|' || schema_compat_count ||
  '|UNVALIDATED_FK|' || unvalidated_fk_count
FROM counts;
