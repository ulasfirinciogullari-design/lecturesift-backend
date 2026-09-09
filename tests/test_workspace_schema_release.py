"""Workspace upgrade and recovery proof on the allocated synthetic CI database."""
import pytest
from sqlalchemy import create_engine, inspect, text

from test_product_schema_release import database, client, managed_client, manifest
from deploy import product_schema_release, workspace_schema_release as release
from deploy import verify_schema_transition_v5 as verifier
from lecturesift import billing_service as billing, workspace_state as workspace, referrals


@pytest.fixture
def current(database, tmp_path):
    engine, name, restored = database
    before = tmp_path / 'product-before.txt'
    before.write_text(manifest(name, legacy=True))
    with engine.begin() as connection:
        product_schema_release.migrate(connection, before, tmp_path / 'product-after.txt')
    client(name, ['bash', '/probe/deploy/postgres-app-role.sh'], extra_env={
        'POSTGRES_DB': name, 'POSTGRES_USER': 'assistant_ci',
        'LECTURESIFT_PROVISION_PHASE': 'runtime',
        'LECTURESIFT_APP_DB_USER': name+'_api',
        'LECTURESIFT_APP_DB_PASSWORD': 'synthetic-api-password-longer-than-24',
        'LECTURESIFT_WORKER_DB_USER': name+'_worker',
        'LECTURESIFT_WORKER_DB_PASSWORD': 'synthetic-worker-password-longer-than-24',
    })
    return engine, name, restored


def migrate(name, directory):
    directory.mkdir(mode=0o700)
    env, command = managed_client(name)
    return release.migrate(directory, env, api_role=name+'_api', worker_role=name+'_worker', command=command)


def recovery(name):
    return client(name, ['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-f', '/probe/deploy/recovery_manifest_v4.sql']).decode()


def test_workspace_upgrade_restores_rows_and_worker_entitlements(current, tmp_path, monkeypatch):
    engine, name, restored = current
    assert migrate(name, tmp_path / 'workspace') == 4
    assert migrate(name, tmp_path / 'repeat') == 0
    with engine.connect() as connection:
        for table in workspace.METADATA.sorted_tables:
            assert referrals._table_shape_matches(inspect(connection), table)
            assert connection.execute(text("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')"),
                                      dict(role=name+'_api', table='public.'+table.name)).scalar_one()
            assert not connection.execute(text("SELECT has_table_privilege(:role,:table,'INSERT,UPDATE,DELETE')"),
                                          dict(role=name+'_worker', table='public.'+table.name)).scalar_one()
    monkeypatch.setattr(billing, 'ENGINE', engine)
    monkeypatch.setattr(billing, '_INITIALIZED', True)
    uid = billing.register_user('workspace@example.invalid', 'Synthetic-password-123', 'Synthetic', 'Test')['user']['id']
    order = billing.create_payment_order(uid, 'synthetic-provider', 'credit', 'one_time', 'TRY')
    workspace.archive_unpaid_order(order['reference'], 'synthetic-admin')
    workspace.set_ad_free(uid, True, 'Synthetic assignment', 'synthetic-admin')
    folder = workspace.save_folder(uid, 'Exam notes')
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO workspace_items_v1 VALUES ('synthetic-job',:uid,:folder)"), dict(uid=uid, folder=folder['id']))
        connection.execute(text("INSERT INTO assistant_credit_grants_v1 VALUES ('admin-grant',:uid,'ADMIN-synthetic','admin',500,300,now()+interval '30 days',now())"), dict(uid=uid))
    live = recovery(name)
    live_path = tmp_path / 'current.txt'
    live_path.write_text(live)
    verifier.verify_current(live_path, release.CONTRACT, release.PRESERVED)
    dump = client(name, ['pg_dump', '--format=custom', '--no-owner', '--no-acl'])
    client(restored, ['pg_restore', '--dbname', restored, '--no-owner', '--no-acl'], payload=dump)
    recovered = recovery(restored)
    for prefix in ('SCHEMA|','SCHEMA_OBJECT|','TABLE|','ANOMALY|','STATUS|'):
        assert sorted(x for x in live.splitlines() if x.startswith(prefix)) == sorted(x for x in recovered.splitlines() if x.startswith(prefix))
    worker = create_engine(engine.url.set(username=name+'_worker', password='synthetic-worker-password-longer-than-24'))
    try:
        monkeypatch.setattr(billing, 'ENGINE', worker)
        status = billing.account_status(uid)
        assert status['permanent_ad_free'] is True
        assert status['payment_orders'] == []
        with worker.connect() as connection:
            with pytest.raises(Exception):
                connection.execute(text('SELECT * FROM public.assistant_credit_grants_v1'))
    finally:
        worker.dispose()


def test_workspace_release_rolls_back_on_final_validation_failure(current, tmp_path, monkeypatch):
    engine, name, _ = current
    def reject(*args):
        raise verifier.ContractError('synthetic verification failure')
    monkeypatch.setattr(release.verifier, 'verify_transition', reject)
    with pytest.raises(verifier.ContractError, match='synthetic verification'):
        migrate(name, tmp_path / 'rejected')
    with engine.connect() as connection:
        assert not (set(inspect(connection).get_table_names()) & verifier.WORKSPACE_TABLES)
    assert not (tmp_path / 'rejected' / 'after-v5.txt').exists()
