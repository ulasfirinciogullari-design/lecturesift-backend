"""Real PostgreSQL 18 upgrade/restore proof in the allocated CI service only."""
import os
from pathlib import Path
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

from deploy import product_schema_release as release
from deploy import verify_schema_transition_v4 as verifier
from lecturesift import billing_service as billing, rollout_service, costs, referrals, assistant_wallet

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'postgres:18-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af'


@pytest.fixture
def database():
    url = os.getenv('TEST_ASSISTANT_POSTGRES_URL', '')
    if not url:
        pytest.skip('Requires the allocated synthetic CI PostgreSQL service')
    assert url == 'postgresql+psycopg://assistant_ci:synthetic-ci-only@127.0.0.1:5432/assistant_ci'
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    name = 'product_ci_' + uuid.uuid4().hex[:12]
    restored = name + '_restore'
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE {name} TEMPLATE template0'))
        connection.execute(text(f'CREATE DATABASE {restored} TEMPLATE template0'))
    engine = create_engine(url.rsplit('/', 1)[0] + '/' + name)
    try:
        billing.METADATA.create_all(engine)
        costs._METADATA.create_all(engine)
        yield engine, name, restored
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))
            connection.execute(text(f'DROP DATABASE {restored} WITH (FORCE)'))
            connection.execute(text(f'DROP ROLE IF EXISTS {name}_api'))
            connection.execute(text(f'DROP ROLE IF EXISTS {name}_worker'))
        admin.dispose()


def client(database, command, *, payload=None, extra_env=None):
    env = os.environ.copy()
    env.update(PGHOST='127.0.0.1', PGPORT='5432', PGUSER='assistant_ci', PGPASSWORD='synthetic-ci-only', PGDATABASE=database)
    env.update(extra_env or {})
    forwarded = [item for key in (extra_env or {}) for item in ('-e', key)]
    result = subprocess.run([
        'docker', 'run', '--rm', '--network', 'host', '-i',
        '-e', 'PGHOST', '-e', 'PGPORT', '-e', 'PGUSER', '-e', 'PGPASSWORD', '-e', 'PGDATABASE',
        *forwarded, '-v', str(ROOT) + ':/probe:ro', IMAGE, *command,
    ], input=payload, capture_output=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr.decode()[-1000:]
    return result.stdout


def manifest(database, *, legacy=False, recovery=False):
    command = ['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1']
    if legacy:
        command += ['-v', 'LECTURESIFT_ALLOW_LEGACY_PRODUCT_TABLES=on']
    command += ['-f', '/probe/deploy/' + ('recovery_manifest_v3.sql' if recovery else 'rehearsal_manifest_v4.sql')]
    return client(database, command).decode()


def seed_legacy(engine):
    for table in (referrals.CODES, referrals.REWARDS, referrals.COUPONS):
        table.create(engine)
    with engine.begin() as connection:
        for owner in ('inviter', 'invitee'):
            connection.execute(text("INSERT INTO billing_users (id,email,password_salt,password_hash,credit_minutes,created_at) VALUES (:id,:email,:salt,:hash,0,now())"),
                               dict(id=owner, email=owner+'@example.invalid', salt='0'*64, hash='1'*64))
        connection.execute(text("INSERT INTO billing_referral_codes VALUES ('inviter','LSR-synthetic',now())"))
        connection.execute(text("""INSERT INTO billing_referral_rewards
          (id,invitee_user_id,inviter_user_id,status,policy_version,inviter_minutes,invitee_minutes,created_at)
          VALUES ('00000000-0000-4000-8000-000000000001','invitee','inviter','registered','2026-09-08-v1',60,30,now())"""))
        connection.execute(text("""INSERT INTO billing_referral_coupons
          (code,user_id,reward_id,status,percent,max_discount_minor,currency,created_at,expires_at)
          VALUES ('LSC-synthetic','inviter','00000000-0000-4000-8000-000000000001','available',10,5000,'TRY',now(),now()+interval '90 days')"""))


def test_upgrade_preserves_legacy_rows_and_restores_all_product_ledgers(database, tmp_path):
    engine, name, restored = database
    seed_legacy(engine)
    before = tmp_path / 'before.txt'
    before.write_text(manifest(name, legacy=True))
    after = tmp_path / 'after.txt'
    with engine.begin() as connection:
        assert release.migrate(connection, before, after) == 5
    current = tmp_path / 'current.txt'
    current.write_text(manifest(name))
    verifier.verify_current(current, release.CONTRACT, release.PRESERVED)
    verifier.verify_transition(before, current, release.CONTRACT, release.PRESERVED)
    # Independently compare migration-created objects with SQLAlchemy's actual
    # metadata: a hand-authored SQL/contract pair cannot hide runtime drift.
    with engine.connect() as connection:
        inspector = __import__('sqlalchemy').inspect(connection)
        for table in (*referrals.METADATA.sorted_tables, *assistant_wallet.METADATA.sorted_tables):
            assert referrals._table_shape_matches(inspector, table)
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO assistant_credit_grants_v1
          VALUES ('grant','inviter','welcome','welcome',50,43,now()+interval '365 days',now())"""))
        connection.execute(text("""INSERT INTO assistant_credit_requests_v1
          VALUES ('request','inviter','payload-digest','complete','[[\"grant\",7]]',7,7,1000,1000,'{\"answer\":\"synthetic\"}','2026-09-08',now())"""))
        connection.execute(text("INSERT INTO assistant_daily_budget_v1 VALUES ('2026-09-08',7)"))
    live = manifest(name, recovery=True)
    dump = client(name, ['pg_dump', '--format=custom', '--no-owner', '--no-acl'])
    client(restored, ['pg_restore', '--dbname', restored, '--no-owner', '--no-acl'], payload=dump)
    recovered = manifest(restored, recovery=True)
    # Database name and physical size can differ; all schema and row identities
    # must agree, including cached answers, reservations and old coupons.
    for prefix in ('SCHEMA|', 'SCHEMA_OBJECT|', 'TABLE|', 'ANOMALY|', 'STATUS|'):
        assert sorted(line for line in live.splitlines() if line.startswith(prefix)) == sorted(line for line in recovered.splitlines() if line.startswith(prefix))
    restored_path = tmp_path / 'restored.txt'
    restored_path.write_text(recovered)
    verifier.verify_current(restored_path, release.CONTRACT, release.PRESERVED)


def test_migration_rejects_stale_evidence_without_creating_tables(database, tmp_path):
    engine, name, _ = database
    before = tmp_path / 'before.txt'
    before.write_text(manifest(name, legacy=True))
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO billing_users (id,email,password_salt,password_hash,credit_minutes,created_at) VALUES ('late','late@example.invalid','x','y',0,now())"))
    with pytest.raises(RuntimeError, match='Rows changed'):
        with engine.begin() as connection:
            release.migrate(connection, before, tmp_path / 'after.txt')
    with engine.connect() as connection:
        assert not (release.table_names(connection) & verifier.PRODUCT_TABLES)


def test_product_shape_contract_rejects_an_altered_existing_column(database, tmp_path):
    engine, name, _ = database
    seed_legacy(engine)
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE billing_referral_codes ALTER COLUMN code TYPE varchar(90)'))
    before = tmp_path / 'before.txt'
    before.write_text(manifest(name, legacy=True))
    with pytest.raises(verifier.ContractError):
        with engine.begin() as connection:
            release.migrate(connection, before, tmp_path / 'after.txt')


def test_runtime_roles_cannot_expose_product_ledgers_to_workers(database, tmp_path):
    engine, name, _ = database
    before = tmp_path / 'before.txt'
    before.write_text(manifest(name, legacy=True))
    with engine.begin() as connection:
        release.migrate(connection, before, tmp_path / 'after.txt')
    client(name, ['bash', '/probe/deploy/postgres-app-role.sh'], extra_env={
        'POSTGRES_DB': name, 'POSTGRES_USER': 'assistant_ci',
        'LECTURESIFT_PROVISION_PHASE': 'runtime',
        'LECTURESIFT_APP_DB_USER': name+'_api',
        'LECTURESIFT_APP_DB_PASSWORD': 'synthetic-api-password-longer-than-24',
        'LECTURESIFT_WORKER_DB_USER': name+'_worker',
        'LECTURESIFT_WORKER_DB_PASSWORD': 'synthetic-worker-password-longer-than-24',
    })
    with engine.connect() as connection:
        for table in verifier.PRODUCT_TABLES:
            for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                assert connection.execute(text('SELECT has_table_privilege(:role,:table,:privilege)'),
                                          dict(role=name+'_api',table='public.'+table,privilege=privilege)).scalar_one()
            assert not connection.execute(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"),
                                          dict(role=name+'_worker',table='public.'+table)).scalar_one()
