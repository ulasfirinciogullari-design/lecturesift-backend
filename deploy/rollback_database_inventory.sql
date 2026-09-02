\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned

-- Value-safe provider-local control-plane evidence.  Role settings and ACLs
-- are represented only by deterministic digests; no password or row value is
-- emitted.  These records are compared byte-for-byte before and after target
-- replacement so schema reset cannot silently mutate database-level policy.
WITH current_db AS (
  SELECT d.*
  FROM pg_database d
  WHERE d.datname = current_database()
)
SELECT 'DATABASE_ATTRIBUTES|' || datallowconn || '|' || datconnlimit || '|' ||
       datistemplate || '|' || pg_encoding_to_char(encoding) || '|' ||
       datcollate || '|' || datctype || '|' || datlocprovider || '|' ||
       coalesce(datcollversion, '')
FROM current_db;

WITH current_db AS (
  SELECT oid, datacl FROM pg_database WHERE datname = current_database()
)
SELECT 'DATABASE_ACL_DIGEST|' || md5(coalesce(datacl::text, ''))
FROM current_db;

WITH current_db AS (
  SELECT oid FROM pg_database WHERE datname = current_database()
), settings AS (
  SELECT md5(r.rolname || E'\n' || array_to_string(s.setconfig, E'\n')) AS item
  FROM pg_db_role_setting s
  JOIN current_db d ON d.oid = s.setdatabase
  JOIN pg_roles r ON r.oid = s.setrole
)
SELECT 'DATABASE_ROLE_SETTINGS_DIGEST|' ||
       coalesce(md5(string_agg(item, E'\n' ORDER BY item)), md5(''))
FROM settings;

WITH defaults AS (
  SELECT md5(r.rolname || '|' || coalesce(n.nspname, '') || '|' ||
             d.defaclobjtype || '|' || coalesce(d.defaclacl::text, '')) AS item
  FROM pg_default_acl d
  JOIN pg_roles r ON r.oid = d.defaclrole
  LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
)
SELECT 'DATABASE_DEFAULT_ACL_DIGEST|' ||
       coalesce(md5(string_agg(item, E'\n' ORDER BY item)), md5(''))
FROM defaults;

WITH extensions AS (
  SELECT e.extname || '|' || e.extversion || '|' || n.nspname AS item
  FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
)
SELECT 'DATABASE_EXTENSION_DIGEST|' ||
       coalesce(md5(string_agg(item, E'\n' ORDER BY item)), md5(''))
FROM extensions;

WITH app_schemas AS (
  SELECT nspname FROM pg_namespace
  WHERE nspname IN ('public', 'lecturesift_worker')
)
SELECT 'APP_SCHEMA_SET|' || coalesce(string_agg(nspname, ',' ORDER BY nspname), '')
FROM app_schemas;

WITH unexpected AS (
  SELECT nspname
  FROM pg_namespace
  WHERE nspname NOT IN ('public', 'lecturesift_worker', 'information_schema')
    AND nspname !~ '^pg_'
)
SELECT 'UNAPPROVED_SCHEMA|' || count(*) || '|' ||
       coalesce(md5(string_agg(nspname, E'\n' ORDER BY nspname)), md5(''))
FROM unexpected;

WITH app_extensions AS (
  SELECT e.extname
  FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
  WHERE n.nspname IN ('public', 'lecturesift_worker')
)
SELECT 'APP_EXTENSION_COUNT|' || count(*) || '|' ||
       coalesce(md5(string_agg(extname, E'\n' ORDER BY extname)), md5(''))
FROM app_extensions;

WITH db_owner AS (
  SELECT datdba FROM pg_database WHERE datname = current_database()
), permitted_owner AS (
  SELECT datdba AS oid FROM db_owner
  UNION
  SELECT oid FROM pg_roles WHERE rolname = 'pg_database_owner'
), owned_objects AS (
  SELECT 'schema|' || n.nspname AS item, n.nspowner AS owner
  FROM pg_namespace n WHERE n.nspname IN ('public', 'lecturesift_worker')
  UNION ALL
  SELECT 'class|' || n.nspname || '.' || c.relname, c.relowner
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname IN ('public', 'lecturesift_worker')
  UNION ALL
  SELECT 'routine|' || n.nspname || '.' || p.proname || ':' || p.oid::regprocedure,
         p.proowner
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname IN ('public', 'lecturesift_worker')
  UNION ALL
  SELECT 'type|' || n.nspname || '.' || t.typname, t.typowner
  FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
  WHERE n.nspname IN ('public', 'lecturesift_worker')
)
SELECT 'APP_OWNER_ANOMALY|' || count(*) || '|' ||
       coalesce(md5(string_agg(item, E'\n' ORDER BY item)), md5(''))
FROM owned_objects
WHERE owner NOT IN (SELECT oid FROM permitted_owner);

WITH acl_items AS (
  SELECT 'schema|' || n.nspname || '|' || coalesce(n.nspacl::text, '') AS item
  FROM pg_namespace n WHERE n.nspname IN ('public', 'lecturesift_worker')
  UNION ALL
  SELECT 'class|' || n.nspname || '.' || c.relname || '|' ||
         coalesce(c.relacl::text, '')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname IN ('public', 'lecturesift_worker')
  UNION ALL
  SELECT 'routine|' || n.nspname || '.' || p.oid::regprocedure || '|' ||
         coalesce(p.proacl::text, '')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname IN ('public', 'lecturesift_worker')
  UNION ALL
  SELECT 'type|' || n.nspname || '.' || t.typname || '|' ||
         coalesce(t.typacl::text, '')
  FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
  WHERE n.nspname IN ('public', 'lecturesift_worker')
)
SELECT 'APP_ACL_DIGEST|' ||
       coalesce(md5(string_agg(item, E'\n' ORDER BY item)), md5(''))
FROM acl_items;

-- Target reconstruction deliberately permits only its database owner.  Null
-- ACL arrays are expanded to PostgreSQL defaults so PUBLIC function EXECUTE
-- is detected unless the reset path explicitly revoked it.
WITH db_owner AS (
  SELECT datdba FROM pg_database WHERE datname = current_database()
), expanded AS (
  SELECT x.grantee, n.nspowner AS owner
  FROM pg_namespace n
  CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) x
  WHERE n.nspname IN ('public', 'lecturesift_worker')
  UNION ALL
  SELECT x.grantee, c.relowner
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN LATERAL aclexplode(coalesce(
    c.relacl, acldefault(CASE WHEN c.relkind = 'S' THEN 's'::"char" ELSE 'r'::"char" END,
                             c.relowner))) x
  WHERE n.nspname IN ('public', 'lecturesift_worker')
    AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
  UNION ALL
  SELECT x.grantee, p.proowner
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) x
  WHERE n.nspname IN ('public', 'lecturesift_worker')
)
SELECT 'APP_ACL_NONOWNER|' || count(*)
FROM expanded, db_owner
WHERE grantee <> db_owner.datdba;
