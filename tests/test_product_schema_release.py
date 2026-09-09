"""Real PostgreSQL 18 upgrade/restore proof in the allocated CI service only."""
import os
from pathlib import Path
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select, text, update

from deploy import product_schema_release as release
from deploy import psql_product_release
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


def managed_client(name):
    env = os.environ.copy()
    env.update(PGHOST='127.0.0.1', PGPORT='5432', PGUSER='assistant_ci',
               PGPASSWORD='synthetic-ci-only', PGDATABASE=name,
               ASSISTANT_ENABLED='false', LECTURESIFT_REFERRALS_ENABLED='false')
    command = ['docker', 'run', '--rm', '--network', 'host', '-i',
               '-e', 'PGHOST', '-e', 'PGPORT', '-e', 'PGUSER', '-e', 'PGPASSWORD',
               '-e', 'PGDATABASE', IMAGE, 'psql']
    return env, command


def test_managed_release_adds_nine_tables_and_preserves_existing_rows(database, tmp_path):
    engine, name, _ = database
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE billing_purchase_terms'))
        connection.execute(text("INSERT INTO billing_users (id,email,password_salt,password_hash,credit_minutes,created_at) VALUES ('retained','retained@example.invalid','x','y',17,now())"))
    evidence = tmp_path / 'managed-release'
    evidence.mkdir(mode=0o700)
    env, command = managed_client(name)
    assert psql_product_release.migrate(evidence, env, allow_purchase_terms=True, command=command) == 9
    current = tmp_path / 'managed-current.txt'
    current.write_text(manifest(name, recovery=True))
    verifier.verify_transition(evidence / 'before-v4.txt', current, release.CONTRACT, release.PRESERVED)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT credit_minutes FROM billing_users WHERE id='retained'")).scalar_one() == 17
        inspector = __import__('sqlalchemy').inspect(connection)
        for table in (billing.PURCHASE_TERMS, *referrals.METADATA.sorted_tables, *assistant_wallet.METADATA.sorted_tables):
            assert referrals._table_shape_matches(inspector, table)


def test_managed_release_rolls_back_every_addition_if_verification_fails(database, tmp_path, monkeypatch):
    engine, name, _ = database
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE billing_purchase_terms'))
    evidence = tmp_path / 'rejected-managed-release'
    evidence.mkdir(mode=0o700)
    env, command = managed_client(name)
    def reject(*_args):
        raise verifier.ContractError('synthetic final-verification failure')
    monkeypatch.setattr(psql_product_release.verifier, 'verify_transition', reject)
    with pytest.raises(verifier.ContractError, match='final-verification'):
        psql_product_release.migrate(evidence, env, allow_purchase_terms=True, command=command)
    with engine.connect() as connection:
        assert not (release.table_names(connection) & (verifier.PRODUCT_TABLES | {'billing_purchase_terms'}))
    assert not (evidence / 'after-v4.txt').exists()


def test_managed_release_requires_explicit_legacy_core_option(database, tmp_path):
    engine, name, _ = database
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE billing_purchase_terms'))
    evidence = tmp_path / 'unapproved-core-release'
    evidence.mkdir(mode=0o700)
    env, command = managed_client(name)
    with pytest.raises(verifier.ContractError):
        psql_product_release.migrate(evidence, env, command=command)
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


@pytest.fixture
def product_state(database, tmp_path, monkeypatch):
    engine, name, _ = database
    before = tmp_path / 'before.txt'
    before.write_text(manifest(name, legacy=True))
    with engine.begin() as connection:
        release.migrate(connection, before, tmp_path / 'after.txt')
    monkeypatch.setattr(billing, 'ENGINE', engine)
    monkeypatch.setattr(rollout_service, 'ENGINE', engine)
    monkeypatch.setattr(billing, '_INITIALIZED', True)
    monkeypatch.setattr(referrals, 'SCHEMA_RECOVERY_RELEASE_READY', True)
    monkeypatch.setenv('LECTURESIFT_REFERRALS_ENABLED', 'true')
    monkeypatch.setenv('LECTURESIFT_REFERRAL_CAMPAIGN_START_AT', '2026-09-08T00:00:00Z')
    monkeypatch.setattr(referrals, 'redemption_currencies', lambda: ['TRY'])
    # Reconciliation below runs in competing real transactions and raises on
    # errors; the best-effort payment wrapper must not swallow a race failure.
    monkeypatch.setattr(referrals, 'after_order_change', lambda _reference: None)
    clock = {'now': datetime(2026, 9, 8, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(billing, 'utcnow', lambda: clock['now'])
    monkeypatch.setattr(rollout_service, 'utcnow', lambda: clock['now'])
    return clock


def product_user(code=''):
    email = 'product-' + uuid.uuid4().hex + '@example.invalid'
    result = billing.register_user(email, 'Synthetic-password-123', 'Synthetic', 'Test', referral_code=code)
    owner = result['user']['id']
    with billing.ENGINE.begin() as connection:
        connection.execute(update(billing.USER_PROFILES).where(
            billing.USER_PROFILES.c.user_id == owner).values(email_verified_at=billing.utcnow()))
    return owner


def product_payment(owner):
    order = billing.create_payment_order(owner, 'synthetic-provider', 'lite', 'monthly', 'TRY')
    billing.complete_payment_order(order['reference'], succeeded=True, provider_amount_minor=order['amount_minor'])
    return order


def race(operation, values):
    barrier = Barrier(len(values), timeout=20)
    def attempt(value):
        barrier.wait()
        return operation(value)
    with ThreadPoolExecutor(max_workers=len(values)) as pool:
        return list(pool.map(attempt, values, timeout=60))


def test_postgres_first_and_renewal_purchases_share_one_atomic_cap(product_state):
    inviter = product_user()
    code = referrals.create_code(inviter)['referral_code']
    existing = [product_user(code) for _ in range(3)]
    for owner in existing:
        referrals._qualify(product_payment(owner)['reference'])
    product_state['now'] += timedelta(days=31)
    new = [product_user(code) for _ in range(4)]
    references = [product_payment(owner)['reference'] for owner in existing + new]
    race(referrals._qualify, references)
    race(referrals._qualify, references)
    with billing.ENGINE.connect() as connection:
        rows = [row for table in (referrals.REWARDS, referrals.RENEWAL_REWARDS)
                for row in connection.execute(select(table).where(table.c.reservation_month == '2026-10'))]
    assert len(rows) == 7
    assert sum(row.status == 'pending' for row in rows) == 5
    assert sum(row.status == 'cap_reached' for row in rows) == 2
    assert len({row.order_reference for row in rows}) == 7


def test_postgres_renewal_month_slot_and_release_are_exactly_once(product_state):
    inviter = product_user()
    invitee = product_user(referrals.create_code(inviter)['referral_code'])
    referrals._qualify(product_payment(invitee)['reference'])
    product_state['now'] += timedelta(days=31)
    references = [product_payment(invitee)['reference'] for _ in range(4)]
    race(referrals._qualify, references)
    with billing.ENGINE.connect() as connection:
        rows = connection.execute(select(referrals.RENEWAL_REWARDS)).all()
    assert len(rows) == 4
    assert sum(row.status == 'monthly_limit' for row in rows) == 3
    selected = next(row for row in rows if row.status == 'pending')
    referrals.choose_reward(inviter, selected.id, 'minutes')
    product_state['now'] += timedelta(days=14)
    results = race(lambda _: referrals.release_reward(selected.id, provider_reconciled=True,
                                                      evidence_reference='synthetic-review-123'), list(range(8)))
    assert all(result['status'] == 'released' for result in results)
    with billing.ENGINE.connect() as connection:
        balances = dict(connection.execute(select(billing.USERS.c.id, billing.USERS.c.credit_minutes)).all())
    assert balances[inviter] == 30
    assert balances[invitee] == 0


def test_postgres_coupon_race_failure_reuse_and_refund_block(product_state):
    inviter = product_user()
    invitee = product_user(referrals.create_code(inviter)['referral_code'])
    source = product_payment(invitee)
    referrals._qualify(source['reference'])
    with billing.ENGINE.connect() as connection:
        reward = connection.execute(select(referrals.REWARDS)).one()
    referrals.choose_reward(inviter, reward.id, 'coupon', 'TRY')
    product_state['now'] += timedelta(days=14)
    referrals.release_reward(reward.id, provider_reconciled=True, evidence_reference='synthetic-review-123')
    with billing.ENGINE.connect() as connection:
        coupon = connection.execute(select(referrals.COUPONS.c.code)).scalar_one()
    def checkout(_):
        try:
            return billing.create_payment_order(inviter, 'synthetic-provider', 'lite', 'monthly', 'TRY', coupon_code=coupon)
        except referrals.ReferralError:
            return None
    orders = [order for order in race(checkout, list(range(8))) if order]
    assert len(orders) == 1
    billing.complete_payment_order(orders[0]['reference'], succeeded=False, provider_amount_minor=0)
    referrals._coupon_order_changed(orders[0]['reference'])
    with billing.ENGINE.connect() as connection:
        assert connection.execute(select(referrals.COUPONS.c.status)).scalar_one() == 'ready'
    rollout_service.create_refund_request(invitee, source['reference'], 'Synthetic source purchase refund')
    assert checkout(None) is None
    with billing.ENGINE.connect() as connection:
        assert connection.execute(select(referrals.COUPONS.c.order_reference)).scalar_one() is None


def test_postgres_account_erasure_removes_private_assistant_rows_with_feature_off(product_state, monkeypatch):
    from lecturesift import assistant_catalog
    monkeypatch.setattr(assistant_catalog, 'SCHEMA_RECOVERY_RELEASE_READY', True)
    monkeypatch.setenv('ASSISTANT_ENABLED', 'true')
    owner = product_user()
    peer = product_user(referrals.create_code(owner)['referral_code'])
    key, _ = assistant_wallet.reserve(owner, 'synthetic-request', 'synthetic-hash', 20)
    assistant_wallet.settle(owner, key, input_tokens=1000, response={'answer': 'synthetic-private-answer'})
    assert assistant_wallet.status(peer)['balance'] == 50
    exported = rollout_service.export_account_data(owner)
    assert 'synthetic-private-answer' not in str(exported)
    with billing.ENGINE.connect() as connection:
        email = connection.execute(select(billing.USERS.c.email).where(billing.USERS.c.id == owner)).scalar_one()
    monkeypatch.setenv('ASSISTANT_ENABLED', 'false')
    monkeypatch.setenv('LECTURESIFT_REFERRALS_ENABLED', 'false')
    rollout_service.close_user_account(owner, 'Synthetic-password-123', email)
    with billing.ENGINE.connect() as connection:
        for table in (assistant_wallet.GRANTS, assistant_wallet.REQUESTS):
            assert not connection.execute(select(table).where(table.c.user_id == owner)).first()
        assert connection.execute(select(assistant_wallet.GRANTS).where(assistant_wallet.GRANTS.c.user_id == peer)).first()
    # Financial attribution remains as a blocked audit row; its public code
    # and private assistant answers/balances are removed on actual closure.
    exported = referrals.export_data(owner)
    assert all(row['status'] == 'blocked' for row in exported['rewards'])
    assert 'synthetic-private-answer' not in str(exported) and peer not in str(exported)
    with billing.ENGINE.connect() as connection:
        assert not connection.execute(select(referrals.CODES).where(referrals.CODES.c.user_id == owner)).first()
    # Only the separately proof-bound rehearsal cleanup may remove both sides.
    with billing.ENGINE.begin() as connection:
        referrals.purge_rehearsal(connection, [owner, peer])
    assert referrals.export_data(owner) == {'rewards': [], 'coupons': []}
