"""Ownership, durable grants and cleanup regression checks; no provider calls."""
from contextlib import contextmanager
from datetime import timedelta
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.pool import StaticPool

from lecturesift import billing_service as billing, rollout_service as rollout, workspace_state as workspace
from lecturesift import assistant_catalog, assistant_wallet as wallet, jobs, config
from main import app


@pytest.fixture
def state(monkeypatch, tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    wallet.METADATA.create_all(engine)
    workspace.METADATA.create_all(engine)
    monkeypatch.setattr(billing, 'ENGINE', engine)
    monkeypatch.setattr(rollout, 'ENGINE', engine)
    monkeypatch.setattr(billing, '_INITIALIZED', True)
    monkeypatch.setattr(config, 'ADMIN_ADMIN', 'synthetic-admin')
    monkeypatch.setattr(config, 'REQUIRE_DURABLE_PROCESSING', False)
    monkeypatch.setenv('ASSISTANT_ENABLED', 'true')
    monkeypatch.setattr(assistant_catalog, 'SCHEMA_RECOVERY_RELEASE_READY', True)
    monkeypatch.setattr(jobs, 'WORK_DIR', tmp_path)
    monkeypatch.setattr(jobs, 'REDIS_URL', '')
    store = jobs.JobStore()
    monkeypatch.setattr(jobs, 'JOBS', store)
    monkeypatch.setattr(jobs.STORAGE, 'delete_job', lambda job_id: 1)
    yield store, tmp_path
    engine.dispose()


def account():
    created = billing.register_user(f'library-{uuid.uuid4().hex}@example.com', 'Synthetic-password1', 'Library', 'Test')
    uid = created['user']['id']
    with billing.ENGINE.begin() as connection:
        connection.execute(update(billing.USER_PROFILES).where(billing.USER_PROFILES.c.user_id == uid).values(email_verified_at=billing.utcnow()))
    return uid


def lesson(state, owner, job_id='lesson', **extra):
    store, root = state
    path = root / job_id
    path.mkdir()
    (path / 'summary.txt').write_text('Synthetic lesson')
    store.create(job_id, path, {'billing_user_id': owner}, title='A lesson', status='done', worker_state='done', stored_bytes=16, **extra)
    return path


def paid(uid, code='plus'):
    order = billing.create_payment_order(uid, 'test-provider', code, 'monthly' if code == 'plus' else 'one_time', 'TRY')
    billing.complete_payment_order(order['reference'], succeeded=True, provider_amount_minor=order['amount_minor'])
    return order


def test_folders_survive_reload_and_delete_moves_lessons_to_root(state):
    uid = account()
    lesson(state, uid)
    folder = workspace.save_folder(uid, '  Biology   101 ')
    workspace.move_lesson(uid, 'lesson', folder['id'])
    workspace.save_folder(uid, 'Exam revision', folder['id'])
    assert workspace.library(uid)['jobs'][0]['folder_id'] == folder['id']
    assert workspace.library(uid)['folders'][0]['name'] == 'Exam revision'
    with pytest.raises(billing.BillingError):
        workspace.save_folder(uid, 'exam REVISION')
    workspace.remove_folder(uid, folder['id'])
    assert workspace.library(uid)['jobs'][0]['folder_id'] is None
    assert state[0].metadata('lesson') is not None


def test_cross_account_folder_move_and_delete_fail_without_touching_files(state):
    owner, outsider = account(), account()
    path = lesson(state, owner)
    folder = workspace.save_folder(owner, 'Private')
    for action in (lambda: workspace.move_lesson(outsider, 'lesson', None),
                   lambda: workspace.remove_folder(outsider, folder['id']),
                   lambda: workspace.save_folder(outsider, 'Renamed', folder['id']),
                   lambda: workspace.delete_lesson(outsider, 'lesson')):
        with pytest.raises(billing.BillingError):
            action()
    assert path.exists()
    assert not workspace.library(outsider)['jobs']


def test_lesson_delete_waits_for_worker_and_keeps_retryable_metadata_on_storage_failure(state, monkeypatch):
    uid = account()
    path = lesson(state, uid)
    store = state[0]
    store.update('lesson', worker_state='publishing')
    with pytest.raises(billing.BillingError):
        workspace.delete_lesson(uid, 'lesson')
    store.update('lesson', worker_state='done')
    @contextmanager
    def busy(_):
        yield False
    with monkeypatch.context() as scoped:
        scoped.setattr(store, 'processing_lock', busy)
        with pytest.raises(billing.BillingError):
            workspace.delete_lesson(uid, 'lesson')
    with monkeypatch.context() as scoped:
        scoped.setattr(jobs.STORAGE, 'delete_job', lambda _: (_ for _ in ()).throw(RuntimeError('synthetic storage failure')))
        with pytest.raises(billing.BillingError):
            workspace.delete_lesson(uid, 'lesson')
    assert path.exists() and store.metadata('lesson')
    workspace.delete_lesson(uid, 'lesson')
    assert not path.exists() and store.metadata('lesson') is None


def test_terminal_queue_failures_and_completed_fallbacks_are_deletable(state):
    uid = account()
    store = state[0]
    cases = (
        ('unavailable', 'error', 'unavailable'),
        ('fallback-done', 'done', 'local_fallback'),
        ('fallback-error', 'error', 'local_fallback'),
    )
    for job_id, status, worker_state in cases:
        path = lesson(state, uid, job_id=job_id)
        store.update(job_id, status=status, worker_state=worker_state)
        item = next(row for row in workspace.library(uid)['jobs'] if row['job_id'] == job_id)
        assert jobs.is_job_deletable(store.metadata(job_id)) is True
        assert item['can_delete'] is True
        workspace.delete_lesson(uid, job_id)
        assert not path.exists() and store.metadata(job_id) is None


def test_active_and_retryable_jobs_remain_locked_and_terminal_states_are_not_recovered(state):
    uid = account()
    store = state[0]
    cases = (
        ('fallback-active', 'working', 'local_fallback'),
        ('queued', 'queued', 'queued'),
        ('publishing', 'done', 'publishing'),
        ('retrying', 'error', 'retrying'),
        ('invalid-unavailable', 'done', 'unavailable'),
    )
    paths = {}
    for job_id, status, worker_state in cases:
        paths[job_id] = lesson(state, uid, job_id=job_id)
        store.update(job_id, status=status, worker_state=worker_state)
        item = next(row for row in workspace.library(uid)['jobs'] if row['job_id'] == job_id)
        assert jobs.is_job_deletable(store.metadata(job_id)) is False
        assert item['can_delete'] is False
        with pytest.raises(billing.BillingError):
            workspace.delete_lesson(uid, job_id)
        assert paths[job_id].exists() and store.metadata(job_id) is not None

    terminal_unavailable = lesson(state, uid, job_id='terminal-unavailable')
    store.update('terminal-unavailable', status='error', worker_state='unavailable')
    terminal_fallback = lesson(state, uid, job_id='terminal-fallback')
    store.update('terminal-fallback', status='done', worker_state='local_fallback')
    assert {row['job_id'] for row in store.recoverable()} == {
        'fallback-active',
        'queued',
    }
    assert terminal_unavailable.exists() and terminal_fallback.exists()


def test_credit_grant_replay_spending_and_expiry_preserve_minutes(state, monkeypatch):
    uid = account()
    before = billing.account_status(uid)['remaining_minutes']
    request_id = str(uuid.uuid4())
    assert not workspace.grant_credits(uid, 500, 30, request_id, 'Service gesture', 'admin')['replayed']
    assert workspace.grant_credits(uid, 500, 30, request_id, 'Service gesture', 'admin')['replayed']
    with pytest.raises(billing.BillingError):
        workspace.grant_credits(uid, 501, 30, request_id, 'Service gesture', 'admin')
    assert workspace.admin_entitlements(uid)['assistant_credits'] == 550
    key, _ = wallet.reserve(uid, 'image-request', 'synthetic-payload', 200)
    wallet.settle(uid, key, fixed_charge=200, response={'kind': 'image'})
    assert workspace.admin_entitlements(uid)['assistant_credits'] == 350
    assert billing.account_status(uid)['remaining_minutes'] == before
    now = billing.utcnow()
    monkeypatch.setattr(billing, 'utcnow', lambda: now + timedelta(days=31))
    assert workspace.admin_entitlements(uid)['assistant_credits'] == 50


def test_manual_ad_free_does_not_replace_subscription_or_revoke_paid_ad_free(state):
    uid = account()
    paid(uid)
    before = billing.account_status(uid)
    workspace.set_ad_free(uid, True, 'Owner assignment', 'admin')
    after = billing.account_status(uid)
    assert after['plan']['code'] == 'plus'
    assert after['subscription'] == before['subscription']
    assert after['plan']['entitlements']['ad_mode'] == 'none'
    assert after['remaining_minutes'] == before['remaining_minutes']
    workspace.set_ad_free(uid, False, 'Remove assignment', 'admin')
    assert billing.account_status(uid)['plan']['entitlements']['ad_mode'] == 'limited'
    paid(uid, 'ad_free')
    workspace.set_ad_free(uid, True, 'Owner assignment', 'admin')
    workspace.set_ad_free(uid, False, 'Remove assignment', 'admin')
    assert billing.account_status(uid)['permanent_ad_free'] is True


def test_archive_preserves_successes_rights_and_provider_reconciliation(state):
    uid = account()
    success = paid(uid)
    pending = billing.create_payment_order(uid, 'test-provider', 'credit', 'one_time', 'TRY')
    before = billing.account_status(uid)['remaining_minutes']
    with pytest.raises(billing.BillingError):
        workspace.archive_unpaid_order(success['reference'], 'admin')
    workspace.archive_unpaid_order(pending['reference'], 'admin')
    assert [o['reference'] for o in billing.account_status(uid)['payment_orders']] == [success['reference']]
    assert rollout.list_admin_orders_page()['pagination']['total'] == 1
    assert billing.admin_billing_overview()['counts']['pending_orders'] == 0
    assert billing.account_status(uid)['remaining_minutes'] == before
    billing.complete_payment_order(pending['reference'], succeeded=True, provider_amount_minor=pending['amount_minor'])
    assert rollout.list_admin_orders_page()['pagination']['total'] == 2
    assert billing.account_status(uid)['credit_minutes'] == 180


def test_privileged_routes_require_admin_and_reject_non_integer_credits(state):
    uid = account()
    client = TestClient(app)
    url = f'/billing/admin/users/{uid}/assistant-credits'
    data = {'credits': 500, 'days': 30, 'request_id': str(uuid.uuid4()), 'reason': 'Synthetic grant'}
    assert client.post(url, json=data).status_code == 401
    assert client.post(url, json=data, headers={'Authorization': 'Bearer synthetic-admin'}).status_code == 200
    assert client.post(url, json={**data, 'credits': True}, headers={'Authorization': 'Bearer synthetic-admin'}).status_code == 422
    assert client.get('/library').status_code == 401


def test_support_deletion_removes_thread_and_keeps_payment_context_private(state):
    uid = account()
    order = paid(uid)
    mid = str(uuid.uuid4())
    now = billing.utcnow()
    with billing.ENGINE.begin() as connection:
        connection.execute(rollout.CONTACT_MESSAGES.insert().values(id=mid, name='Synthetic', email='synthetic@example.invalid', topic='Payment', message='Help please', order_reference=order['reference'], status='new', email_notified=0, created_at=now, updated_at=now))
        connection.execute(rollout.CONTACT_REPLIES.insert().values(id=str(uuid.uuid4()), contact_message_id=mid, direction='inbound', body='More detail', sender='synthetic@example.invalid', delivery_status='received', created_at=now))
    public = rollout.get_contact_conversation(mid, public_token=rollout._support_reply_token(mid))
    assert 'payment' not in public['message']
    assert rollout.get_contact_conversation(mid, include_payment=True)['message']['payment']['status'] == 'paid'
    client = TestClient(app)
    assert client.delete(f'/billing/admin/contact-messages/{mid}').status_code == 401
    assert client.delete(f'/billing/admin/contact-messages/{mid}', headers={'Authorization':'Bearer synthetic-admin'}).status_code == 200
    with pytest.raises(billing.BillingError):
        rollout.get_contact_conversation(mid, public_token=rollout._support_reply_token(mid))
    assert billing.account_status(uid)['plan']['code'] == 'plus'
    with billing.ENGINE.connect() as connection:
        assert not connection.execute(select(rollout.CONTACT_REPLIES)).first()


def test_purchase_snapshot_without_advertising_field_preserves_old_terms(state):
    import json
    uid = account()
    order = billing.create_payment_order(uid, 'test-provider', 'plus', 'monthly', 'TRY')
    with billing.ENGINE.begin() as connection:
        terms = connection.execute(select(billing.PURCHASE_TERMS).where(billing.PURCHASE_TERMS.c.reference == order['reference'])).one()
        payload = json.loads(terms.plan_json)
        payload['plan'].pop('advertising')
        connection.execute(update(billing.PURCHASE_TERMS).where(billing.PURCHASE_TERMS.c.reference == order['reference']).values(plan_json=json.dumps(payload)))
    billing.complete_payment_order(order['reference'], succeeded=True, provider_amount_minor=order['amount_minor'])
    assert billing.account_status(uid)['plan']['entitlements']['ad_free'] is True
