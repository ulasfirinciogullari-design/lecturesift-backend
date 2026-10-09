from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from lecturesift import billing_service as billing, config, job_notifications as notifications, jobs
from lecturesift.app import _public_job


@pytest.fixture
def notification_state(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, 'WORK_DIR', tmp_path)
    monkeypatch.setattr(jobs, 'REDIS_URL', '')
    monkeypatch.setattr(notifications.config, 'EMAIL_PROVIDER', 'resend')
    monkeypatch.setattr(notifications.config, 'current_maintenance_mode', lambda: 'off')
    monkeypatch.delenv('LECTURESIFT_WORKER', raising=False)
    store = jobs.JobStore()
    monkeypatch.setattr(notifications, 'JOBS', store)
    store.create('failed-job', tmp_path / 'failed-job', {'billing_user_id': 'synthetic-user'},
        status='error', error_code='LS-AI-08', error='Safe explanation', technical_error='private-secret')
    recipient = {'email': 'verified@example.invalid', 'language': 'de'}
    monkeypatch.setattr(notifications, '_recipient', lambda _user: recipient)
    sent = []
    monkeypatch.setattr(notifications, 'send_transactional_email', lambda *a, **kw: sent.append((a, kw)) or 'synthetic-mail')
    return store, sent, recipient


def test_terminal_notice_is_durable_deduplicated_localized_and_private(notification_state):
    store, sent, recipient = notification_state
    notifications.queue_failure_notification('failed-job')
    notifications.queue_failure_notification('failed-job')
    assert notifications.deliver_one('failed-job') == 'sent'
    assert notifications.deliver_one('failed-job') == 'skipped'
    assert len(sent) == 1
    args, kwargs = sent[0]
    assert args[0] == recipient['email']
    assert kwargs['idempotency_key'].startswith('job-failure/')
    assert 'private-secret' not in str(args)
    assert store.metadata('failed-job')['_failure_notification']['template_context']['language'] == 'de'
    assert store.metadata('failed-job')['_failure_notification']['provider_id'] == 'synthetic-mail'
    persisted = jobs.JobStore()
    assert persisted.queue_failure_notification('failed-job') is False
    assert persisted.claim_failure_notification('failed-job') is None
    for public in (store.public('failed-job'), _public_job(store.metadata('failed-job')),
                   store.admin_summary(store.metadata('failed-job'))):
        assert '_failure_notification' not in public
        assert recipient['email'] not in str(public)
        assert 'job-failure/' not in str(public)
        assert 'synthetic-mail' not in str(public)
    assert store.admin_summary(store.metadata('failed-job'))['failure_notification']['state'] == 'sent'


def test_retry_freezes_message_and_language_without_changing_job(notification_state, monkeypatch):
    store, sent, recipient = notification_state
    attempts = []
    def fail_once(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise RuntimeError('private provider response and recipient')
        return 'synthetic-mail'
    monkeypatch.setattr(notifications, 'send_transactional_email', fail_once)
    notifications.queue_failure_notification('failed-job')
    assert notifications.deliver_one('failed-job') == 'retry'
    recipient['language'] = 'ja'
    notice = store.metadata('failed-job')['_failure_notification']
    store.update_failure_notification('failed-job', notice['claim'], next_attempt_at=0)
    assert notifications.deliver_one('failed-job') == 'sent'
    assert attempts[0] == attempts[1]
    data = store.metadata('failed-job')
    assert data['status'] == 'error' and data['error'] == 'Safe explanation'
    assert data['_failure_notification']['attempts'] == 2


def test_recovered_job_is_cancelled_and_historical_errors_are_not_backfilled(notification_state):
    store, sent, recipient = notification_state
    notifications.deliver_pending()
    assert sent == []
    notifications.queue_failure_notification('failed-job')
    store.update('failed-job', status='done')
    assert notifications.deliver_one('failed-job') == 'skipped'
    assert store.metadata('failed-job')['_failure_notification']['state'] == 'cancelled'
    assert sent == []


def test_delivery_lease_prevents_duplicate_senders_and_recovers_after_timeout(notification_state, monkeypatch):
    store, sent, recipient = notification_state
    notifications.queue_failure_notification('failed-job')
    claimed = store.claim_failure_notification('failed-job')
    assert claimed is not None and store.claim_failure_notification('failed-job') is None
    data, claim = claimed
    store.update_failure_notification('failed-job', claim, lease_until=0)
    assert notifications.deliver_one('failed-job') == 'sent'
    assert len(sent) == 1
    assert store.update_failure_notification('failed-job', claim, state='retry') is False


@pytest.mark.parametrize('provider', ['resend', 'smtp'])
def test_failed_delivery_is_bounded_and_smtp_ambiguity_is_not_retried(notification_state, monkeypatch, provider):
    store, sent, recipient = notification_state
    monkeypatch.setattr(config, 'EMAIL_PROVIDER', provider)
    monkeypatch.setattr(notifications, 'send_transactional_email', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('private-secret')))
    notifications.queue_failure_notification('failed-job')
    for attempt in range(6 if provider == 'resend' else 1):
        notifications.deliver_one('failed-job')
        notice = store.metadata('failed-job')['_failure_notification']
        store.update_failure_notification('failed-job', notice['claim'], next_attempt_at=0)
    assert notifications.deliver_one('failed-job') == 'skipped'
    assert notice['state'] == ('exhausted' if provider == 'resend' else 'delivery_unknown')
    assert 'private-secret' not in str(notice)


def test_changed_recipient_does_not_replay_an_ambiguous_email(notification_state, monkeypatch):
    store, sent, recipient = notification_state
    monkeypatch.setattr(notifications, 'send_transactional_email', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('timeout')))
    notifications.queue_failure_notification('failed-job')
    assert notifications.deliver_one('failed-job') == 'retry'
    notice = store.metadata('failed-job')['_failure_notification']
    store.update_failure_notification('failed-job', notice['claim'], next_attempt_at=0)
    recipient['email'] = 'changed@example.invalid'
    assert notifications.deliver_one('failed-job') == 'recipient_changed'


@pytest.mark.parametrize(('email', 'verified', 'has_profile', 'eligible'), [
    ('registered@example.invalid', True, True, True),
    ('registered@example.invalid', False, True, False),
    ('registered@example.invalid', True, False, False),
    ('guest+123@users.invalid', True, True, False),
    ('guest-123@guest.lecturesift.invalid', True, True, False),
    ('deleted+123@users.invalid', True, True, False),
])
def test_recipient_requires_a_verified_registered_account(monkeypatch, email, verified, has_profile, eligible):
    engine = create_engine('sqlite://')
    billing.METADATA.create_all(engine, tables=[billing.USERS, billing.USER_PROFILES, billing.USER_PREFERENCES])
    monkeypatch.setattr(billing, 'ENGINE', engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(billing.USERS.insert().values(id='user', email=email,
            password_salt='', password_hash='', credit_minutes=0, created_at=now))
        if has_profile:
            connection.execute(billing.USER_PROFILES.insert().values(user_id='user', first_name='Synthetic',
                last_name='User', email_verified_at=now if verified else None, created_at=now, updated_at=now))
        connection.execute(billing.USER_PREFERENCES.insert().values(user_id='user', preferred_language='fr', updated_at=now))
    result = notifications._recipient('user')
    assert bool(result) is eligible
    if eligible:
        assert result == {'email': email, 'language': 'fr'}
    engine.dispose()


def test_worker_terminal_hooks_wait_for_retry_exhaustion(monkeypatch):
    from lecturesift import tasks
    queued = []
    monkeypatch.setattr(tasks.JOBS, 'update', lambda job_id, **kw:
        queued.append(job_id) if kw.get('notify_failure') else None)
    monkeypatch.setattr(tasks, '_cleanup_terminal_sources', lambda *_a, **_kw: None)
    class RetryScheduled(Exception):
        pass
    task = SimpleNamespace(max_retries=6, request=SimpleNamespace(retries=0),
        retry=lambda **kw: (_ for _ in ()).throw(RetryScheduled()))
    with pytest.raises(RetryScheduled):
        tasks._retry_or_fail(task, 'job', RuntimeError('temporary system failure'))
    assert queued == []
    task.request.retries = 6
    with pytest.raises(RuntimeError):
        tasks._retry_or_fail(task, 'job', RuntimeError('temporary system failure'))
    assert queued == ['job']
    tasks._quota_error('quota-job', billing.BillingError('safe quota explanation'))
    assert queued == ['job', 'quota-job']


def test_terminal_status_and_rejection_snapshot_are_persisted_atomically(notification_state, monkeypatch):
    store, sent, recipient = notification_state
    store.update('failed-job', status='working')
    snapshots = []
    original_flush = store._flush_locked
    def capture():
        snapshots.append(store.metadata('failed-job'))
        original_flush()
    monkeypatch.setattr(store, '_flush_locked', capture)
    store.update('failed-job', status='error', error_code='LS-BILL-10', notify_failure=True,
        notification_context={'required_minutes': 561, 'remaining_minutes': 60, 'max_minutes_per_job': 30})
    assert len(snapshots) == 1
    assert snapshots[0]['status'] == 'error'
    assert snapshots[0]['_failure_notification']['state'] == 'pending'
    monkeypatch.setattr(billing, 'account_status', lambda *a: pytest.fail('must not read a changed balance'))
    assert notifications.deliver_one('failed-job') == 'sent'
    context = store.metadata('failed-job')['_failure_notification']['template_context']
    assert context['required_minutes'] == 561 and context['remaining_minutes'] == 60


@pytest.mark.parametrize(('setting', 'value'), [('EMAIL_FROM', 'changed@example.invalid'), ('EMAIL_PROVIDER', 'smtp')])
def test_changed_envelope_stops_ambiguous_retry(notification_state, monkeypatch, setting, value):
    store, sent, recipient = notification_state
    monkeypatch.setattr(notifications, 'send_transactional_email', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('timeout')))
    notifications.queue_failure_notification('failed-job')
    assert notifications.deliver_one('failed-job') == 'retry'
    notice = store.metadata('failed-job')['_failure_notification']
    store.update_failure_notification('failed-job', notice['claim'], next_attempt_at=0)
    monkeypatch.setattr(config, setting, value)
    assert notifications.deliver_one('failed-job') == 'envelope_changed'


@pytest.mark.parametrize(('code', 'managed', 'queued'), [
    ('LS-AI-02', True, False), ('LS-SYSTEM-01', True, False),
    ('LS-AI-08', True, True), ('LS-AI-01', True, True), ('LS-SYSTEM-01', False, True),
])
def test_pipeline_only_enqueues_terminal_failures(notification_state, monkeypatch, tmp_path, code, managed, queued):
    from lecturesift import pipeline
    from lecturesift.errors import LectureSiftError
    store, sent, recipient = notification_state
    monkeypatch.setattr(pipeline, 'JOBS', store)
    monkeypatch.setattr(pipeline.AI_PROVIDER_CIRCUIT, 'trip_error', lambda *_a: None)
    monkeypatch.setattr(pipeline, 'extract_documents', lambda *a, **kw:
        (_ for _ in ()).throw(LectureSiftError(code, 'Synthetic processing failure')))
    pipeline._process_job('failed-job', [tmp_path / 'source.pdf'],
        {'billing_user_id': 'synthetic-user', '_worker_managed': managed, 'document_mode': True})
    data = store.metadata('failed-job')
    assert data['status'] == 'error'
    assert bool(data.get('_failure_notification')) is queued
