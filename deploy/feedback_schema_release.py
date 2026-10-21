"""Explicit additive study-pack feedback release, preserving all v5 schema and rows.

Run only after the workspace (v5) release, CI upgrade/restore evidence and a
fresh managed backup. The runtime never imports this driver and never creates
the table itself. Owner credentials stay in the environment: either a private
DATABASE_URL, or the OVH compose PostgreSQL container's own owner login.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re

from deploy.psql_product_release import Session, ReleaseError, connection_environment, CONTRACT, PRESERVED
from deploy import verify_schema_transition_v6 as verifier

ROOT = Path(__file__).resolve().parent
_IDENTIFIER = re.compile(r'[a-z_][a-z0-9_]{0,62}')
_CONTAINER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')


def migrate(evidence_dir, environment, *, api_role, worker_role, command=('psql',)):
    directory = Path(evidence_dir)
    if (directory.is_symlink() or not directory.is_dir()
            or directory.stat().st_mode & 0o077 or any(directory.iterdir())):
        raise ReleaseError('Use a fresh private evidence directory')
    if (not all(_IDENTIFIER.fullmatch(role or '') for role in (api_role, worker_role))
            or api_role == worker_role):
        raise ReleaseError('Two distinct existing runtime roles are required')
    session = Session(command, environment)
    after = directory / 'after-v6.txt'
    committed = False
    try:
        session.execute(r"""BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='45s';
SET LOCAL idle_in_transaction_session_timeout='60s';
SELECT pg_advisory_xact_lock(hashtextextended('lecturesift.feedback-schema-v1',0));
SELECT format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE',table_name)
FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'
ORDER BY table_name
\gexec
""")
        manifest = (ROOT / 'rehearsal_manifest_v6.sql').read_text()
        before = directory / 'before-v6.txt'
        before.write_text(session.execute('\\set LECTURESIFT_ALLOW_LEGACY_FEEDBACK_TABLES on\n' + manifest))
        before.chmod(0o600)
        verifier.verify_legacy(before, CONTRACT, PRESERVED)
        names = {line.split('|')[1] for line in before.read_text().splitlines() if line.startswith('TABLE|')}
        missing = verifier.EXPECTED_TABLES - names
        if names - verifier.EXPECTED_TABLES or missing - verifier.FEEDBACK_TABLES:
            raise ReleaseError('Every v5 table must already be current')
        ddl = re.sub(r'^--.*$', '', (ROOT / 'feedback_tables_v1.sql').read_text(), flags=re.MULTILINE)
        statements = []
        for statement in ddl.split(';'):
            statement = statement.strip()
            if not statement:
                continue
            match = re.search(r'(?:CREATE TABLE|ON) public\.([a-z0-9_]+)', statement)
            if not match or match[1] not in verifier.FEEDBACK_TABLES:
                raise ReleaseError('DDL escaped the reviewed feedback schema')
            if match[1] in missing:
                statements.append(statement + ';')
        tables = ','.join('public.' + name for name in sorted(verifier.FEEDBACK_TABLES))
        # The API owns feedback end to end; the worker never reads or writes it.
        statements += [f'GRANT SELECT,INSERT,UPDATE,DELETE ON {tables} TO "{api_role}";',
                       f'REVOKE ALL ON {tables} FROM "{worker_role}";']
        session.execute('\n'.join(statements))
        after.write_text(session.execute('\\set LECTURESIFT_ALLOW_LEGACY_FEEDBACK_TABLES off\n' + manifest))
        after.chmod(0o600)
        verifier.verify_transition(before, after, CONTRACT, PRESERVED)
        session.execute('COMMIT;')
        committed = True
        return len(missing)
    finally:
        session.close()
        if not committed:
            after.unlink(missing_ok=True)


def container_command(container, environment):
    """psql inside the compose PostgreSQL container as its owner login.

    This is how every OVH tool reaches the private database (see
    run_exact_rehearsal.sh); POSTGRES_USER and POSTGRES_DB come from the
    root-only /etc/lecturesift/postgres.env, never from argv.
    """
    user = environment.get('POSTGRES_USER', '')
    database = environment.get('POSTGRES_DB', '')
    if not _CONTAINER.fullmatch(container or ''):
        raise ReleaseError('The PostgreSQL container name is invalid')
    if not all(_IDENTIFIER.fullmatch(value) for value in (user, database)):
        raise ReleaseError('POSTGRES_USER and POSTGRES_DB must name the owner login and database')
    return ('docker', 'exec', '-i', container, 'psql', '--username', user, '--dbname', database)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-feedback-schema-v1', action='store_true', required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--api-role', required=True)
    parser.add_argument('--worker-role', required=True)
    parser.add_argument('--postgres-container',
                        help='run psql inside this compose PostgreSQL container as its POSTGRES_USER '
                             'instead of connecting with a private DATABASE_URL')
    args = parser.parse_args()
    try:
        if args.postgres_container:
            environment = os.environ.copy()
            command = container_command(args.postgres_container, environment)
        else:
            environment, command = connection_environment(), ('psql',)
        count = migrate(args.evidence_dir, environment, api_role=args.api_role,
                        worker_role=args.worker_role, command=command)
    except Exception:
        raise SystemExit('Feedback schema release was not confirmed. Check state before retrying.') from None
    print(f'feedback_tables_added={count}\nfeedback_schema_state=current')


if __name__ == '__main__':
    main()
