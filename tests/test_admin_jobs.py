"""Administrator inspection never widens ordinary users' lesson access."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.pool import StaticPool

from lecturesift import admin_jobs, billing_service as billing, config, jobs, rollout_routes, rollout_service
from main import app


@pytest.fixture
def state(monkeypatch, tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    rollout_service.METADATA.create_all(engine)
    monkeypatch.setattr(billing, 'ENGINE', engine)
    monkeypatch.setattr(rollout_service, 'ENGINE', engine)
    monkeypatch.setattr(billing, '_INITIALIZED', True)
    monkeypatch.setattr(rollout_service, 'init_rollout_database', lambda: None)
    monkeypatch.setattr(config, 'ADMIN_ADMIN', 'synthetic-admin')
    monkeypatch.setattr(config, 'WORK_DIR', tmp_path)
    monkeypatch.setattr(jobs, 'WORK_DIR', tmp_path)
    monkeypatch.setattr(jobs, 'REDIS_URL', '')
    store = jobs.JobStore()
    monkeypatch.setattr(admin_jobs, 'JOBS', store)
    monkeypatch.setattr(rollout_routes, 'JOBS', store)
    monkeypatch.setattr(rollout_routes, 'worker_health', lambda: {})
    user = billing.register_user(f'{uuid.uuid4()}@example.invalid', 'Synthetic-password1', 'Admin', 'Lesson')['user']
    path = tmp_path / 'lesson-1'
    (path / 'package').mkdir(parents=True)
    (path / 'package' / 'notes.txt').write_text('Private lesson output')
    (path / 'package' / 'unlisted.txt').write_text('Not in the output manifest')
    (path / 'result.json').write_text(json.dumps({
        'title':'Synthetic lesson', 'summary':'A private summary', 'transcript':'A private transcript',
        'sources':{'source_files':['lesson.pdf'], 'private_storage_key':'secret'},
        'diagnostics':{'provider_secret':'secret'}, 'options':{'billing_user_id':user['id']},
        'artifacts':[{'file':'notes.txt', 'label':'Notlar'}, {'file':'../secret', 'label':'invalid'}],
        'quiz':[{'question':'2+2?', 'options':['3','4'], 'answer_index':1}],
        'flashcards':[{'front':'Question?', 'back':'Answer'}],
    }))
    store.create('lesson-1', path, {'billing_user_id':user['id']}, title='Synthetic lesson', status='done', worker_state='done',
        technical_error='secret', source_keys={'audio':['secret']}, remote_result_key='secret')
    yield TestClient(app), store, user, path
    engine.dispose()


ADMIN = {'Authorization':'Bearer synthetic-admin'}


def test_list_is_metadata_only_and_filters_owner_before_limit(state, monkeypatch):
    client, store, user, path = state
    store.create('other-newer', path, {'billing_user_id':'other-user'}, created=9999999999)
    monkeypatch.setattr(store, 'ensure_local_file', lambda *a, **k: pytest.fail('list downloaded private files'))
    response = client.get(f"/billing/admin/jobs?limit=1&owner_id={user['id']}", headers=ADMIN)
    assert response.status_code == 200
    item, = response.json()['jobs']
    assert item['job_id'] == 'lesson-1' and item['owner_email'] == user['email']
    assert item['owner_name'] == 'Admin Lesson' and item['result_ready'] is True
    assert 'secret' not in response.text
    assert 'job_dir' not in item and 'billing_user_id' not in item['options']


def test_inspection_and_manifest_download_require_admin_and_are_audited(state):
    client, store, user, path = state
    for endpoint in ('lesson-1', 'lesson-1/artifacts/notes.txt'):
        for headers in ({}, {'Authorization':'Bearer ordinary-user-token'}):
            assert client.get('/billing/admin/jobs/' + endpoint, headers=headers).status_code == 401
    response = client.get('/billing/admin/jobs/lesson-1', headers=ADMIN)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    result = response.json()['result']
    assert result['summary'] == 'A private summary'
    assert result['source_files'] == ['lesson.pdf']
    assert result['artifacts'] == [{'file':'notes.txt', 'label':'Notlar'}]
    assert 'secret' not in response.text and 'diagnostics' not in result
    downloaded = client.get('/billing/admin/jobs/lesson-1/artifacts/notes.txt', headers=ADMIN)
    assert downloaded.status_code == 200 and downloaded.text == 'Private lesson output'
    assert downloaded.headers['cache-control'] == 'no-store'
    assert downloaded.headers['content-disposition'].startswith('attachment;')
    assert client.get('/billing/admin/jobs/lesson-1/artifacts/unlisted.txt', headers=ADMIN).status_code == 404
    assert client.get('/billing/admin/jobs/lesson-1/artifacts/..%5Csecret', headers=ADMIN).status_code == 404
    with billing.ENGINE.connect() as connection:
        actions = connection.execute(select(rollout_service.ADMIN_ACCOUNT_EVENTS.c.action)).scalars().all()
    assert actions == ['lesson_inspected', 'lesson_output_downloaded']


def test_worker_publication_and_expired_outputs_are_not_reported_as_available(state, monkeypatch):
    client, store, user, path = state
    store.update('lesson-1', queue_mode='celery', worker_state='publishing')
    response = client.get('/billing/admin/jobs/lesson-1', headers=ADMIN).json()
    assert response['job']['status'] == 'working' and response['result'] is None
    assert client.get('/billing/admin/jobs/lesson-1/artifacts/notes.txt', headers=ADMIN).status_code == 409
    store.update('lesson-1', worker_state='done')
    (path / 'result.json').unlink()
    response = client.get('/billing/admin/jobs/lesson-1', headers=ADMIN)
    assert response.status_code == 200 and response.json()['result'] is None
    assert 'saklama' in response.json()['result_message']
    assert client.get('/billing/admin/jobs/missing', headers=ADMIN).status_code == 404


def test_legacy_profile_and_hostile_paths_cannot_break_inspection_or_escape_job(state, tmp_path):
    client, store, user, path = state
    with billing.ENGINE.begin() as connection:
        connection.execute(delete(billing.USER_PROFILES).where(billing.USER_PROFILES.c.user_id == user['id']))
    assert client.get('/billing/admin/jobs', headers=ADMIN).json()['jobs'][0]['owner_email'] == user['email']
    outside = tmp_path / 'outside.txt'
    outside.write_text('unrelated secret')
    (path / 'package' / 'notes.txt').unlink()
    (path / 'package' / 'notes.txt').symlink_to(outside)
    assert client.get('/billing/admin/jobs/lesson-1/artifacts/notes.txt', headers=ADMIN).status_code == 404
    store.update('lesson-1', job_dir=str(tmp_path))
    response = client.get('/billing/admin/jobs/lesson-1', headers=ADMIN)
    assert response.json()['result'] is None
    assert 'unrelated secret' not in response.text


def test_long_transcript_is_bounded_and_clearly_marked(state):
    client, store, user, path = state
    result = json.loads((path / 'result.json').read_text())
    result['transcript'] = 'a' * 250001
    (path / 'result.json').write_text(json.dumps(result))
    body = client.get('/billing/admin/jobs/lesson-1', headers=ADMIN).json()['result']
    assert len(body['transcript']) == 250000 and body['transcript_truncated'] is True
