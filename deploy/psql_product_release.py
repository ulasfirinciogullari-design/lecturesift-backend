"""Add the frozen product schema through an existing PostgreSQL client.

The managed Render database has no root shell or application release checkout.
This driver holds one database transaction while the same v4 verifier checks
the real before/after manifests. No production rows leave the database.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import time
from urllib.parse import unquote, urlsplit

from deploy import verify_schema_transition_v4 as verifier

ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT / 'schema_contract_payment_provider_sessions_v1.txt'
PRESERVED = ROOT / 'schema_contract_billing_email_verifications_v1.txt'
TERMS_DDL = '''CREATE TABLE public.billing_purchase_terms (
 reference character varying(64) NOT NULL PRIMARY KEY,
 plan_json text NOT NULL,
 version character varying(32) NOT NULL,
 created_at timestamp with time zone NOT NULL
);'''


class ReleaseError(RuntimeError):
    pass


class Session:
    def __init__(self, command, environment):
        self.process = subprocess.Popen(
            [*command, '--no-psqlrc', '--no-password', '--quiet', '--no-align',
             '--tuples-only', '--set=ON_ERROR_STOP=1'], env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def execute(self, sql, *, timeout=60):
        marker = ('release_stage_' + secrets.token_hex(16)).encode()
        self.process.stdin.write(sql.encode() + b'\n\\echo ' + marker + b'\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        output = b''
        while time.monotonic() < deadline:
            if not self.selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise ReleaseError('Database command failed; transaction was not approved')
            output += chunk
            if len(output) > 4_000_000:
                raise ReleaseError('Release evidence exceeded its bound')
            if output.endswith(marker + b'\n'):
                return output[:-len(marker)-1].decode().strip() + '\n'
        raise ReleaseError('Database operation exceeded its release deadline')

    def close(self):
        # Closing the connection rolls back an uncommitted transaction even
        # after a parser error, timeout, interruption or failed verification.
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()


def migrate(evidence_dir, environment, *, allow_purchase_terms=False, command=('psql',)):
    evidence_dir = Path(evidence_dir)
    if (evidence_dir.is_symlink() or not evidence_dir.is_dir()
            or evidence_dir.stat().st_mode & 0o077 or any(evidence_dir.iterdir())):
        raise ReleaseError('Use a fresh private evidence directory')
    if any(environment.get(key, '').lower() == 'true'
           for key in ('ASSISTANT_ENABLED', 'LECTURESIFT_REFERRALS_ENABLED')):
        raise ReleaseError('Keep both product features disabled during migration')
    before, after = (evidence_dir / name for name in ('before-v4.txt', 'after-v4.txt'))
    session = Session(command, environment)
    committed = False
    try:
        session.execute(r'''BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '45s';
SET LOCAL idle_in_transaction_session_timeout = '60s';
SELECT pg_advisory_xact_lock(hashtextextended('lecturesift.product-schema-v1', 0));
SELECT format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', table_name)
FROM information_schema.tables
WHERE table_schema='public' AND table_type='BASE TABLE'
ORDER BY table_name
\gexec
''')
        manifest = (ROOT / 'rehearsal_manifest_v4.sql').read_text()
        flags = '\\set LECTURESIFT_ALLOW_LEGACY_PRODUCT_TABLES on\n'
        flags += '\\set LECTURESIFT_ALLOW_LEGACY_PURCHASE_TERMS ' + ('on' if allow_purchase_terms else 'off') + '\n'
        before.write_text(session.execute(flags + manifest))
        before.chmod(0o600)
        verifier.verify_legacy(before, CONTRACT, PRESERVED)
        names = {line.split('|')[1] for line in before.read_text().splitlines() if line.startswith('TABLE|')}
        allowed_missing = verifier.PRODUCT_TABLES | ({'billing_purchase_terms'} if allow_purchase_terms else set())
        if names - verifier.EXPECTED_TABLES or (verifier.EXPECTED_TABLES - names) - allowed_missing:
            raise ReleaseError('Unexpected core schema inventory')
        ddl = []
        if 'billing_purchase_terms' not in names:
            ddl.append(TERMS_DDL)
        product_ddl = re.sub(r'^--.*$', '', (ROOT / 'product_tables_v1.sql').read_text(), flags=re.MULTILINE)
        for statement in product_ddl.split(';'):
            statement = statement.strip()
            if not statement:
                continue
            match = re.search(r'(?:CREATE TABLE|ON) public\.([a-z0-9_]+)', statement)
            if not match or match[1] not in verifier.PRODUCT_TABLES:
                raise ReleaseError('DDL escaped the reviewed product schema')
            if match[1] not in names:
                ddl.append(statement + ';')
        session.execute('\n'.join(ddl))
        flags = '\\set LECTURESIFT_ALLOW_LEGACY_PRODUCT_TABLES off\n\\set LECTURESIFT_ALLOW_LEGACY_PURCHASE_TERMS off\n'
        after.write_text(session.execute(flags + manifest))
        after.chmod(0o600)
        verifier.verify_transition(before, after, CONTRACT, PRESERVED)
        session.execute('COMMIT;')
        committed = True
        return len(verifier.EXPECTED_TABLES - names)
    finally:
        session.close()
        if not committed:
            after.unlink(missing_ok=True)


def connection_environment():
    url = urlsplit(os.environ.get('DATABASE_URL', ''))
    if url.scheme not in ('postgres', 'postgresql') or not url.hostname or not url.username or not url.password or url.query or url.fragment:
        raise ReleaseError('A private PostgreSQL owner connection is required')
    env = os.environ.copy()
    env.update(PGHOST=url.hostname, PGPORT=str(url.port or 5432),
               PGDATABASE=unquote(url.path.lstrip('/')), PGUSER=unquote(url.username),
               PGPASSWORD=unquote(url.password), PGSSLMODE='verify-full',
               PGSSLROOTCERT='/etc/ssl/certs/ca-certificates.crt', PGCONNECT_TIMEOUT='8',
               PGAPPNAME='lecturesift-product-schema-release', PGOPTIONS='')
    env.pop('DATABASE_URL', None)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-product-schema-v1', action='store_true', required=True)
    parser.add_argument('--add-legacy-purchase-terms', action='store_true')
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        count = migrate(args.evidence_dir, connection_environment(), allow_purchase_terms=args.add_legacy_purchase_terms)
    except Exception:
        raise SystemExit('Product schema release failed. Check the database state before retrying; no feature was activated.') from None
    print(f'tables_added={count}\nproduct_schema_state=current')


if __name__ == '__main__':
    main()
