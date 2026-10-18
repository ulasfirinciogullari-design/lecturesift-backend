\set ON_ERROR_STOP on
\set QUIET on
\pset tuples_only on
\pset format unaligned

-- Read-only count of privileges that the application schemas grant to roles
-- other than the object owner, the database owner, pg_database_owner and
-- PUBLIC: schema, relation, column, routine and type ACLs plus global and
-- app-schema default ACLs. Separate runtime logins (an API or worker role)
-- are exactly such grantees. The automatic Render replacement restores the
-- OVH dump with --no-acl and keeps only owner privileges, so it would silently
-- drop every grant counted here; rollback_postgres_to_render.sh therefore
-- refuses to replace a Render database whose count is not zero.
WITH database_owner AS (
  SELECT datdba AS oid
  FROM pg_catalog.pg_database
  WHERE datname = pg_catalog.current_database()
), app_ns AS (
  SELECT oid, nspowner, nspacl
  FROM pg_catalog.pg_namespace
  WHERE nspname IN ('public', 'lecturesift_worker')
), acl_sources AS (
  SELECT ns.nspowner AS owner_oid, ns.nspacl AS acl
  FROM app_ns ns
  UNION ALL
  SELECT rel.relowner, rel.relacl
  FROM pg_catalog.pg_class rel
  JOIN app_ns ns ON ns.oid = rel.relnamespace
  UNION ALL
  SELECT rel.relowner, att.attacl
  FROM pg_catalog.pg_attribute att
  JOIN pg_catalog.pg_class rel ON rel.oid = att.attrelid
  JOIN app_ns ns ON ns.oid = rel.relnamespace
  WHERE att.attnum > 0 AND NOT att.attisdropped
  UNION ALL
  SELECT proc.proowner, proc.proacl
  FROM pg_catalog.pg_proc proc
  JOIN app_ns ns ON ns.oid = proc.pronamespace
  UNION ALL
  SELECT typ.typowner, typ.typacl
  FROM pg_catalog.pg_type typ
  JOIN app_ns ns ON ns.oid = typ.typnamespace
  UNION ALL
  SELECT dacl.defaclrole, dacl.defaclacl
  FROM pg_catalog.pg_default_acl dacl
  WHERE dacl.defaclnamespace = 0
     OR dacl.defaclnamespace IN (SELECT oid FROM app_ns)
)
SELECT 'RUNTIME_ACL_GRANTS|' || count(*)
FROM acl_sources src
CROSS JOIN LATERAL pg_catalog.aclexplode(src.acl) AS item
WHERE item.grantee <> 0
  AND item.grantee <> src.owner_oid
  AND item.grantee <> (SELECT oid FROM database_owner)
  AND item.grantee <> 'pg_database_owner'::pg_catalog.regrole::pg_catalog.oid;

SELECT 'RUNTIME_ACL_COMPLETE|v1';
