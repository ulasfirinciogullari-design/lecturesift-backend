\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned

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
UNION ALL
SELECT 'TABLE_DIFF|unexpected|' || a.table_name
FROM actual a LEFT JOIN expected e USING (table_name)
WHERE e.table_name IS NULL
ORDER BY 1;

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
)
SELECT 'SCHEMA|' || count(*) || '|' ||
       coalesce(md5(string_agg(item, E'\n' ORDER BY item)), '')
FROM objects;

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
SELECT 'ANOMALY|invalid_payment_provider_token_digest|' || count(*)
FROM billing_payment_provider_sessions
WHERE token_digest !~ '^[0-9a-f]{64}$'
UNION ALL
SELECT 'ANOMALY|payment_provider_session_mismatch|' || count(*)
FROM billing_payment_provider_sessions s
JOIN billing_payment_orders o ON o.reference = s.order_reference
WHERE s.provider <> o.provider
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
