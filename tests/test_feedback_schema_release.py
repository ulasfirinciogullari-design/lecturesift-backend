"""Feedback (schema contract v6) upgrade and recovery proof on the allocated synthetic CI database."""
import pytest
from sqlalchemy import inspect, text

from test_product_schema_release import database, client, managed_client  # noqa: F401
from test_workspace_schema_release import current, migrate as migrate_workspace  # noqa: F401
from deploy import feedback_schema_release as release
from deploy import verify_schema_transition_v6 as verifier
from lecturesift import billing_service as billing, job_feedback, referrals


@pytest.fixture
def workspace_current(current, tmp_path):
    """A v5 database: product (v4) plus the explicit workspace release."""
    engine, name, restored = current
    assert migrate_workspace(name, tmp_path / 'workspace-first') == 4
    return engine, name, restored


def migrate(name, directory):
    directory.mkdir(mode=0o700)
    env, command = managed_client(name)
    return release.migrate(directory, env, api_role=name+'_api', worker_role=name+'_worker', command=command)


def recovery(name):
    return client(name, ['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-f', '/probe/deploy/recovery_manifest_v5.sql']).decode()


def test_feedback_release_requires_the_workspace_family_first(current, tmp_path):
    engine, name, _ = current
    # A product (v4) database is missing every workspace table: the v6 legacy
    # manifest reports them as TABLE_DIFF rows, so nothing is created.
    with pytest.raises(verifier.ContractError):
        migrate(name, tmp_path / 'premature')
    with engine.connect() as connection:
        assert not (set(inspect(connection).get_table_names()) & verifier.FEEDBACK_TABLES)
    assert not (tmp_path / 'premature' / 'after-v6.txt').exists()


def test_feedback_upgrade_restores_rows_and_keeps_the_worker_out(workspace_current, tmp_path, monkeypatch):
    engine, name, restored = workspace_current
    assert migrate(name, tmp_path / 'feedback') == 1
    assert migrate(name, tmp_path / 'repeat') == 0
    with engine.connect() as connection:
        assert referrals._table_shape_matches(inspect(connection), job_feedback.JOB_FEEDBACK)
        assert connection.execute(text("SELECT has_table_privilege(:role,'public.lecturesift_job_feedback','SELECT,INSERT,UPDATE,DELETE')"),
                                  dict(role=name+'_api')).scalar_one()
        assert not connection.execute(text("SELECT has_table_privilege(:role,'public.lecturesift_job_feedback','SELECT')"),
                                      dict(role=name+'_worker')).scalar_one()
    monkeypatch.setattr(billing, 'ENGINE', engine)
    monkeypatch.setattr(billing, '_INITIALIZED', True)
    uid = billing.register_user('feedback@example.invalid', 'Synthetic-password-123', 'Synthetic', 'Test')['user']['id']
    member = {'id': uid, 'email': 'feedback@example.invalid'}
    saved = job_feedback.save_job_feedback('00000000-0000-4000-8000-0000000000fb', member,
                                           rating='up', comment='Quiz çok işime yaradı', consent=True, locale='tr')
    assert saved['updated'] is False and saved['comment'] == 'Quiz çok işime yaradı'
    guest = {'id': 'guest', 'email': 'guest@guest.lecturesift.invalid'}
    assert job_feedback.save_job_feedback('00000000-0000-4000-8000-0000000000fc', guest, rating='down', locale='en')['updated'] is False
    live = recovery(name)
    assert 'ANOMALY|invalid_lecturesift_job_feedback|0' in live.splitlines()
    live_path = tmp_path / 'current.txt'
    live_path.write_text(live)
    verifier.verify_current(live_path, release.CONTRACT, release.PRESERVED)
    dump = client(name, ['pg_dump', '--format=custom', '--no-owner', '--no-acl'])
    client(restored, ['pg_restore', '--dbname', restored, '--no-owner', '--no-acl'], payload=dump)
    recovered = recovery(restored)
    for prefix in ('SCHEMA|', 'SCHEMA_OBJECT|', 'TABLE|', 'ANOMALY|', 'STATUS|'):
        assert sorted(x for x in live.splitlines() if x.startswith(prefix)) == sorted(x for x in recovered.splitlines() if x.startswith(prefix))
    # The newest row is listed first; guests keep no account reference.
    listing = job_feedback.list_job_feedback()
    assert [(item['plan'], item['user_id']) for item in listing] == [('guest', None), ('free', uid)]
    with engine.begin() as connection:
        assert [row['job_id'] for row in job_feedback.export_rows(connection, uid)] == ['00000000-0000-4000-8000-0000000000fb']
        assert job_feedback.erase_rows(connection, uid) == 1
    assert [item['job_id'] for item in job_feedback.list_job_feedback()] == ['00000000-0000-4000-8000-0000000000fc']


def test_feedback_release_rolls_back_on_final_validation_failure(workspace_current, tmp_path, monkeypatch):
    engine, name, _ = workspace_current
    def reject(*args):
        raise verifier.ContractError('synthetic verification failure')
    monkeypatch.setattr(release.verifier, 'verify_transition', reject)
    with pytest.raises(verifier.ContractError, match='synthetic verification'):
        migrate(name, tmp_path / 'rejected')
    with engine.connect() as connection:
        assert not (set(inspect(connection).get_table_names()) & verifier.FEEDBACK_TABLES)
    assert not (tmp_path / 'rejected' / 'after-v6.txt').exists()


def test_release_driver_reaches_the_compose_database_only_through_its_owner_login(monkeypatch):
    environment = {'POSTGRES_USER': 'lecturesift_owner', 'POSTGRES_DB': 'lecturesift'}
    assert release.container_command('lecturesift-postgres-1', environment) == (
        'docker', 'exec', '-i', 'lecturesift-postgres-1', 'psql',
        '--username', 'lecturesift_owner', '--dbname', 'lecturesift',
    )
    for container, env in (
        ('lecturesift-postgres-1; rm -rf /', environment),
        ('', environment),
        ('lecturesift-postgres-1', {'POSTGRES_USER': 'owner', 'POSTGRES_DB': ''}),
        ('lecturesift-postgres-1', {'POSTGRES_USER': 'Owner Role', 'POSTGRES_DB': 'lecturesift'}),
    ):
        with pytest.raises(release.ReleaseError):
            release.container_command(container, env)
