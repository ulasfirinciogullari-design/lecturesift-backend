"""Explicit additive workspace release, preserving all v4 schema and rows.

Run only after CI upgrade/restore evidence and a fresh managed backup. The
runtime never imports this driver. Owner credentials stay in the environment.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

from deploy.psql_product_release import Session, ReleaseError, connection_environment, CONTRACT, PRESERVED
from deploy import verify_schema_transition_v5 as verifier

ROOT = Path(__file__).resolve().parent


def migrate(evidence_dir, environment, *, api_role, worker_role, command=('psql',)):
    directory = Path(evidence_dir)
    if (directory.is_symlink() or not directory.is_dir()
            or directory.stat().st_mode & 0o077 or any(directory.iterdir())):
        raise ReleaseError('Use a fresh private evidence directory')
    if (not all(re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', role or '') for role in (api_role, worker_role))
            or api_role == worker_role):
        raise ReleaseError('Two distinct existing runtime roles are required')
    session = Session(command, environment)
    after = directory / 'after-v5.txt'
    committed = False
    try:
        session.execute(r"""BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='45s';
SET LOCAL idle_in_transaction_session_timeout='60s';
SELECT pg_advisory_xact_lock(hashtextextended('lecturesift.workspace-schema-v1',0));
SELECT format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE',table_name)
FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'
ORDER BY table_name
\gexec
""")
        manifest = (ROOT / 'rehearsal_manifest_v5.sql').read_text()
        before = directory / 'before-v5.txt'
        before.write_text(session.execute('\\set LECTURESIFT_ALLOW_LEGACY_WORKSPACE_TABLES on\n' + manifest))
        before.chmod(0o600)
        verifier.verify_legacy(before, CONTRACT, PRESERVED)
        names = {line.split('|')[1] for line in before.read_text().splitlines() if line.startswith('TABLE|')}
        missing = verifier.EXPECTED_TABLES - names
        if names - verifier.EXPECTED_TABLES or missing - verifier.WORKSPACE_TABLES:
            raise ReleaseError('Every v4 table must already be current')
        ddl = re.sub(r'^--.*$', '', (ROOT / 'workspace_tables_v1.sql').read_text(), flags=re.MULTILINE)
        statements = []
        for statement in ddl.split(';'):
            statement = statement.strip()
            if not statement:
                continue
            match = re.search(r'(?:CREATE TABLE|ON) public\.([a-z0-9_]+)', statement)
            if not match or match[1] not in verifier.WORKSPACE_TABLES:
                raise ReleaseError('DDL escaped the reviewed workspace schema')
            if match[1] in missing:
                statements.append(statement + ';')
        tables = ','.join('public.' + name for name in sorted(verifier.WORKSPACE_TABLES))
        statements += [f'GRANT SELECT,INSERT,UPDATE,DELETE ON {tables} TO "{api_role}";',
                       f'GRANT SELECT ON public.admin_ad_free_grants_v1 TO "{worker_role}";',
                       f'GRANT SELECT (reference) ON public.admin_order_archives_v1 TO "{worker_role}";']
        session.execute('\n'.join(statements))
        after.write_text(session.execute('\\set LECTURESIFT_ALLOW_LEGACY_WORKSPACE_TABLES off\n' + manifest))
        after.chmod(0o600)
        verifier.verify_transition(before, after, CONTRACT, PRESERVED)
        session.execute('COMMIT;')
        committed = True
        return len(missing)
    finally:
        session.close()
        if not committed:
            after.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-workspace-schema-v1', action='store_true', required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--api-role', required=True)
    parser.add_argument('--worker-role', required=True)
    args = parser.parse_args()
    try:
        count = migrate(args.evidence_dir, connection_environment(), api_role=args.api_role, worker_role=args.worker_role)
    except Exception:
        raise SystemExit('Workspace schema release was not confirmed. Check state before retrying.') from None
    print(f'workspace_tables_added={count}\nworkspace_schema_state=current')


if __name__ == '__main__':
    main()
