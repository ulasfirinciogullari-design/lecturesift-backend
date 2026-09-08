"""Explicit additive product migration, bound to a validated frozen manifest.

No runtime path imports this module. The operator supplies a v4 legacy manifest
from the frozen database and a private evidence directory. Existing schema and
row fingerprints must match that evidence inside the migration transaction.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re

from sqlalchemy import create_engine, text

from deploy import verify_schema_transition_v4 as verifier

ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT / "schema_contract_payment_provider_sessions_v1.txt"
PRESERVED = ROOT / "schema_contract_billing_email_verifications_v1.txt"
CATALOG_SQL = """
SELECT 'SCHEMA_OBJECT|' || item FROM (
 SELECT format('C|%I.%I|%s|%I|%s|%s|%s|%s|%s', n.nspname,c.relname,a.attnum,a.attname,
 format_type(a.atttypid,a.atttypmod),a.attnotnull,coalesce(pg_get_expr(d.adbin,d.adrelid),''),a.attidentity,a.attgenerated) item
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_attribute a ON a.attrelid=c.oid
 LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
 WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped
 UNION ALL SELECT format('K|%s|%I|%s|%s',conrelid::regclass::text,conname,convalidated,pg_get_constraintdef(oid,true))
 FROM pg_constraint WHERE connamespace='public'::regnamespace
 UNION ALL SELECT format('I|%I|%I|%s',tablename,indexname,indexdef) FROM pg_indexes WHERE schemaname='public'
) objects ORDER BY item
"""
DATABASE_SQL = """
SELECT 'DATABASE|' || (current_setting('server_version_num')::integer / 10000) || '|' ||
pg_encoding_to_char(encoding) || '|' || datcollate || '|' || datctype || '|' ||
datlocprovider::text || '|' || coalesce(datcollversion, '') || '|' ||
CASE WHEN current_setting('TimeZone') IN ('UTC','Etc/UTC','GMT') THEN 'UTC' ELSE current_setting('TimeZone') END
FROM pg_database WHERE datname=current_database()
"""


def catalog(connection):
    return set(connection.execute(text(CATALOG_SQL)).scalars())


def table_names(connection):
    return set(connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")).scalars())


def fingerprints(connection, names):
    result = set()
    for name in sorted(names):
        if name not in verifier.EXPECTED_TABLES:
            raise RuntimeError("Unexpected table inventory")
        row = connection.execute(text(
            "SELECT count(*)::bigint, coalesce(bit_xor(hashtextextended(to_jsonb(t)::text,0)),0), "
            "coalesce(sum(hashtextextended(to_jsonb(t)::text,0)::numeric),0) FROM public.\"" + name + "\" t"
        )).one()
        result.add("TABLE|" + name + "|" + "|".join(str(value) for value in row))
    return result


def migrate(connection, before: Path, after: Path):
    """Caller owns one transaction; any mismatch aborts all DDL."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL 18 is required")
    verifier.verify_legacy(before, CONTRACT, PRESERVED)
    lines = before.read_text().splitlines()
    if connection.execute(text(DATABASE_SQL)).scalar_one() not in lines:
        raise RuntimeError("Database identity differs from reviewed evidence")
    names = table_names(connection)
    # Core purchase terms/provider sessions must already have their own reviewed
    # migration. This release only admits the eight product tables.
    if names - verifier.EXPECTED_TABLES or (verifier.EXPECTED_TABLES - names) - verifier.PRODUCT_TABLES:
        raise RuntimeError("Core schema must be current before product migration")
    connection.execute(text("SET LOCAL lock_timeout='10s'"))
    connection.execute(text("SET LOCAL statement_timeout='120s'"))
    for name in sorted(names):
        connection.execute(text('LOCK TABLE public."' + name + '" IN ACCESS EXCLUSIVE MODE'))
    if catalog(connection) != {line for line in lines if line.startswith('SCHEMA_OBJECT|')}:
        raise RuntimeError("Schema changed after manifest capture")
    if fingerprints(connection, names) != {line for line in lines if line.startswith('TABLE|')}:
        raise RuntimeError("Rows changed after manifest capture")
    ddl = (ROOT / 'product_tables_v1.sql').read_text()
    ddl = re.sub(r'^--.*$', '', ddl, flags=re.MULTILINE)
    for statement in ddl.split(';'):
        statement = statement.strip()
        if not statement:
            continue
        match = re.search(r'(?:CREATE TABLE|ON) public\.([a-z0-9_]+)', statement)
        if not match or match[1] not in verifier.PRODUCT_TABLES:
            raise RuntimeError("DDL escaped reviewed product tables")
        if match[1] not in names:
            connection.execute(text(statement))
    new_names = table_names(connection)
    objects = catalog(connection)
    rows = fingerprints(connection, new_names)
    payload = '\n'.join(sorted(line.removeprefix('SCHEMA_OBJECT|') for line in objects))
    digest = hashlib.md5(payload.encode(), usedforsecurity=False).hexdigest()
    retained = [line for line in lines if not line.startswith((
        'SCHEMA|', 'SCHEMA_OBJECT|', 'TABLE|', 'SCHEMA_COMPAT|', 'MANIFEST_COMPLETE|',
    ))]
    retained += [f'SCHEMA|{len(objects)}|{digest}', *sorted(objects), *sorted(rows)]
    retained += ['ANOMALY|' + verifier.PRODUCT_ANOMALIES[name] + '|0' for name in sorted(new_names - names)]
    counts = [f'{family}|{sum(line.startswith(family + "|") for line in retained)}' for family in verifier.MANIFEST_FAMILIES]
    retained.append('MANIFEST_COMPLETE|v4|' + '|'.join(counts))
    after.write_text('\n'.join(retained) + '\n')
    after.chmod(0o600)
    verifier.verify_transition(before, after, CONTRACT, PRESERVED)
    return len(new_names - names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--confirm-product-schema-v1', action='store_true')
    args = parser.parse_args()
    if not args.confirm_product_schema_v1:
        parser.error('Explicit product schema confirmation is required')
    if any(os.getenv(key, '').lower() == 'true' for key in ('ASSISTANT_ENABLED','LECTURESIFT_REFERRALS_ENABLED')):
        parser.error('Keep both features disabled during schema migration')
    directory = args.evidence_dir
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077:
        parser.error('Evidence directory must be private and must not be a symlink')
    after = directory / 'product-after-v4.txt'
    if after.exists() or after.is_symlink():
        parser.error('Use a fresh evidence directory')
    url = os.getenv('DATABASE_URL', '')
    if not url:
        parser.error('DATABASE_URL must be set in the private owner environment')
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            count = migrate(connection, args.before, after)
        print(f'product_tables_added={count}')
        print('product_schema_state=current')
    except Exception:
        after.unlink(missing_ok=True)
        raise SystemExit('Product migration failed; transaction rolled back. No feature was activated.') from None
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
