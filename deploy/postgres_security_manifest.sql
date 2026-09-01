\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned

-- Deterministic, password-free inventory of the PostgreSQL authority surface.
-- The caller supplies the three expected roles as psql literals. Row data and
-- password hashes are never read. JSONB keeps every object on one escaped line.
\if :{?owner_user}
\else
\echo 'owner_user is required'
\quit 3
\endif
\if :{?api_user}
\else
\echo 'api_user is required'
\quit 3
\endif
\if :{?worker_user}
\else
\echo 'worker_user is required'
\quit 3
\endif

WITH objects(item) AS (
  SELECT jsonb_build_object(
    'family', 'role',
    'name', r.rolname,
    'login', r.rolcanlogin,
    'superuser', r.rolsuper,
    'inherit', r.rolinherit,
    'createdb', r.rolcreatedb,
    'createrole', r.rolcreaterole,
    'replication', r.rolreplication,
    'bypassrls', r.rolbypassrls,
    'connlimit', r.rolconnlimit,
    'validuntil', coalesce(r.rolvaliduntil::text, ''),
    'config', coalesce(
      (SELECT jsonb_agg(value ORDER BY value)
       FROM unnest(r.rolconfig) AS role_config(value)),
      '[]'::jsonb
    ),
    'expected_kind', CASE r.rolname
      WHEN :'owner_user' THEN 'owner'
      WHEN :'api_user' THEN 'api'
      WHEN :'worker_user' THEN 'worker'
      ELSE 'other'
    END
  )::text
  FROM pg_roles r
  WHERE r.rolname !~ '^pg_'

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'expected_role',
    'kind', required.kind,
    'name', required.role_name,
    'present', expected.oid IS NOT NULL
  )::text
  FROM (
    VALUES
      (:'owner_user', 'owner'),
      (:'api_user', 'api'),
      (:'worker_user', 'worker')
  ) AS required(role_name, kind)
  LEFT JOIN pg_roles expected ON expected.rolname = required.role_name

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'membership',
    'role', granted_role.rolname,
    'member', member_role.rolname,
    'grantor', grantor_role.rolname,
    'admin_option', membership.admin_option,
    'inherit_option', membership.inherit_option,
    'set_option', membership.set_option
  )::text
  FROM pg_auth_members membership
  JOIN pg_roles granted_role ON granted_role.oid = membership.roleid
  JOIN pg_roles member_role ON member_role.oid = membership.member
  JOIN pg_roles grantor_role ON grantor_role.oid = membership.grantor
  WHERE granted_role.rolname !~ '^pg_' OR member_role.rolname !~ '^pg_'

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'database',
    'name', db.datname,
    'owner', owner.rolname,
    'encoding', pg_encoding_to_char(db.encoding),
    'collate', db.datcollate,
    'ctype', db.datctype,
    'locale_provider', db.datlocprovider,
    'collation_version', coalesce(db.datcollversion, ''),
    'comment', coalesce(shobj_description(db.oid, 'pg_database'), ''),
    'allow_connections', db.datallowconn,
    'connection_limit', db.datconnlimit,
    'is_template', db.datistemplate
  )::text
  FROM pg_database db
  JOIN pg_roles owner ON owner.oid = db.datdba
  WHERE db.datname = current_database()

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'database_acl',
    'database', db.datname,
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_database db
  CROSS JOIN LATERAL aclexplode(
    coalesce(db.datacl, acldefault('d', db.datdba))
  ) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
  WHERE db.datname = current_database()

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'role_setting',
    'role', setting_role.rolname,
    'database', coalesce(db.datname, 'ALL'),
    'setting', setting.value
  )::text
  FROM pg_db_role_setting role_setting
  JOIN pg_roles setting_role ON setting_role.oid = role_setting.setrole
  LEFT JOIN pg_database db ON db.oid = role_setting.setdatabase
  CROSS JOIN LATERAL unnest(role_setting.setconfig) setting(value)
  WHERE setting_role.rolname !~ '^pg_'
    AND (role_setting.setdatabase = 0 OR db.datname = current_database())

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'schema',
    'name', namespace.nspname,
    'owner', owner.rolname
  )::text
  FROM pg_namespace namespace
  JOIN pg_roles owner ON owner.oid = namespace.nspowner
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'schema_acl',
    'schema', namespace.nspname,
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_namespace namespace
  CROSS JOIN LATERAL aclexplode(
    coalesce(namespace.nspacl, acldefault('n', namespace.nspowner))
  ) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'relation',
    'name', format('%I.%I', namespace.nspname, relation.relname),
    'kind', relation.relkind,
    'owner', owner.rolname,
    'persistence', relation.relpersistence,
    'row_security', relation.relrowsecurity,
    'force_row_security', relation.relforcerowsecurity,
    'replica_identity', relation.relreplident,
    'options', coalesce(
      (SELECT jsonb_agg(value ORDER BY value)
       FROM unnest(relation.reloptions) relation_option(value)),
      '[]'::jsonb
    )
  )::text
  FROM pg_class relation
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  JOIN pg_roles owner ON owner.oid = relation.relowner
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')
    AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'relation_acl',
    'relation', format('%I.%I', namespace.nspname, relation.relname),
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_class relation
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  CROSS JOIN LATERAL aclexplode(
    coalesce(
      relation.relacl,
      acldefault(
        CASE relation.relkind WHEN 'S' THEN 's'::"char" ELSE 'r'::"char" END,
        relation.relowner
      )
    )
  ) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')
    AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'column_acl',
    'relation', format('%I.%I', namespace.nspname, relation.relname),
    'column', attribute.attname,
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_attribute attribute
  JOIN pg_class relation ON relation.oid = attribute.attrelid
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')
    AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
    AND attribute.attnum > 0
    AND NOT attribute.attisdropped

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'view_definition',
    'view', format('%I.%I', namespace.nspname, relation.relname),
    'definition', pg_get_viewdef(relation.oid, true)
  )::text
  FROM pg_class relation
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')
    AND relation.relkind IN ('v', 'm')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'function',
    'name', format(
      '%I.%I(%s)', namespace.nspname, proc.proname,
      pg_get_function_identity_arguments(proc.oid)
    ),
    'owner', owner.rolname,
    'kind', proc.prokind,
    'security_definer', proc.prosecdef,
    'leakproof', proc.proleakproof,
    'volatility', proc.provolatile,
    'parallel', proc.proparallel,
    'config', coalesce(
      (SELECT jsonb_agg(value ORDER BY value)
       FROM unnest(proc.proconfig) proc_config(value)),
      '[]'::jsonb
    ),
    'definition', pg_get_functiondef(proc.oid)
  )::text
  FROM pg_proc proc
  JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
  JOIN pg_roles owner ON owner.oid = proc.proowner
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'function_acl',
    'function', format(
      '%I.%I(%s)', namespace.nspname, proc.proname,
      pg_get_function_identity_arguments(proc.oid)
    ),
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_proc proc
  JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
  CROSS JOIN LATERAL aclexplode(
    coalesce(proc.proacl, acldefault('f', proc.proowner))
  ) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'policy',
    'relation', format('%I.%I', namespace.nspname, relation.relname),
    'name', policy.polname,
    'command', policy.polcmd,
    'permissive', policy.polpermissive,
    'roles', coalesce(
      (SELECT jsonb_agg(role_name ORDER BY role_name)
       FROM (
         SELECT CASE role_ids.role_oid
                  WHEN 0 THEN 'PUBLIC' ELSE policy_role.rolname
                END role_name
         FROM unnest(policy.polroles) AS role_ids(role_oid)
         LEFT JOIN pg_roles policy_role ON policy_role.oid = role_ids.role_oid
       ) names),
      '[]'::jsonb
    ),
    'using', coalesce(pg_get_expr(policy.polqual, policy.polrelid), ''),
    'check', coalesce(pg_get_expr(policy.polwithcheck, policy.polrelid), '')
  )::text
  FROM pg_policy policy
  JOIN pg_class relation ON relation.oid = policy.polrelid
  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'default_acl',
    'owner', owner.rolname,
    'schema', coalesce(namespace.nspname, 'ALL'),
    'object_type', defaults.defaclobjtype,
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_default_acl defaults
  JOIN pg_roles owner ON owner.oid = defaults.defaclrole
  LEFT JOIN pg_namespace namespace ON namespace.oid = defaults.defaclnamespace
  CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')
     OR defaults.defaclnamespace = 0

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'parameter_acl',
    'parameter', parameter.parname,
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_parameter_acl parameter
  CROSS JOIN LATERAL aclexplode(parameter.paracl) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'tablespace',
    'name', tablespace.spcname,
    'owner', owner.rolname,
    'options', coalesce(
      (SELECT jsonb_agg(value ORDER BY value)
       FROM unnest(tablespace.spcoptions) tablespace_option(value)),
      '[]'::jsonb
    )
  )::text
  FROM pg_tablespace tablespace
  JOIN pg_roles owner ON owner.oid = tablespace.spcowner

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'tablespace_acl',
    'tablespace', tablespace.spcname,
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_tablespace tablespace
  CROSS JOIN LATERAL aclexplode(
    coalesce(tablespace.spcacl, acldefault('t', tablespace.spcowner))
  ) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'type',
    'name', format('%I.%I', namespace.nspname, type_entry.typname),
    'owner', owner.rolname,
    'kind', type_entry.typtype,
    'category', type_entry.typcategory,
    'preferred', type_entry.typispreferred,
    'defined', type_entry.typisdefined
  )::text
  FROM pg_type type_entry
  JOIN pg_namespace namespace ON namespace.oid = type_entry.typnamespace
  JOIN pg_roles owner ON owner.oid = type_entry.typowner
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'type_acl',
    'type', format('%I.%I', namespace.nspname, type_entry.typname),
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_type type_entry
  JOIN pg_namespace namespace ON namespace.oid = type_entry.typnamespace
  CROSS JOIN LATERAL aclexplode(
    coalesce(type_entry.typacl, acldefault('T', type_entry.typowner))
  ) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
  WHERE namespace.nspname IN ('public', 'lecturesift_worker')

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'large_object',
    'oid', large_object.oid,
    'owner', owner.rolname
  )::text
  FROM pg_largeobject_metadata large_object
  JOIN pg_roles owner ON owner.oid = large_object.lomowner

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'large_object_acl',
    'oid', large_object.oid,
    'grantor', grantor.rolname,
    'grantee', CASE acl.grantee WHEN 0 THEN 'PUBLIC' ELSE grantee.rolname END,
    'privilege', acl.privilege_type,
    'grantable', acl.is_grantable
  )::text
  FROM pg_largeobject_metadata large_object
  CROSS JOIN LATERAL aclexplode(
    coalesce(large_object.lomacl, acldefault('L', large_object.lomowner))
  ) acl
  JOIN pg_roles grantor ON grantor.oid = acl.grantor
  LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'security_coverage',
    'contract', 'postgres-security-v1',
    'parameter_acl_rows', (SELECT count(*) FROM pg_parameter_acl),
    'tablespaces', (SELECT count(*) FROM pg_tablespace),
    'types', (
      SELECT count(*) FROM pg_type type_count
      JOIN pg_namespace namespace ON namespace.oid = type_count.typnamespace
      WHERE namespace.nspname IN ('public', 'lecturesift_worker')
    ),
    'large_objects', (SELECT count(*) FROM pg_largeobject_metadata)
  )::text

  UNION ALL
  SELECT jsonb_build_object(
    'family', 'extension',
    'name', extension.extname,
    'version', extension.extversion,
    'schema', namespace.nspname,
    'relocatable', extension.extrelocatable
  )::text
  FROM pg_extension extension
  JOIN pg_namespace namespace ON namespace.oid = extension.extnamespace
), emitted(sort_key, line) AS (
  SELECT 0, 'SECURITY_MANIFEST|v1'
  UNION ALL
  SELECT 1, 'SECURITY_OBJECT|' || item FROM objects
  UNION ALL
  SELECT 2, 'SECURITY_COMPLETE|v1|' || count(*)::text || '|' ||
            coalesce(md5(string_agg(item, E'\n' ORDER BY item)), md5(''))
  FROM objects
)
SELECT line
FROM emitted
ORDER BY sort_key, line;
