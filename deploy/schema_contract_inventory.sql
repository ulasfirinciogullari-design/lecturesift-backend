\set ON_ERROR_STOP on
\set QUIET on
\pset tuples_only on
\pset format unaligned

-- Read-only inventory of every public base or partitioned table, independent
-- of the connected role's table privileges. schema_contract_state.py uses it
-- only to choose which frozen manifest/verifier family describes the live
-- database; callers must still strict-verify the full manifest of that family.
SELECT 'PUBLIC_TABLE|' || relation.relname
FROM pg_catalog.pg_class relation
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'public' AND relation.relkind IN ('r', 'p')
ORDER BY relation.relname COLLATE "C";

SELECT 'INVENTORY_COMPLETE|v1|' || count(*)
FROM pg_catalog.pg_class relation
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'public' AND relation.relkind IN ('r', 'p');
