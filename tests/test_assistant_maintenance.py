import asyncio

from lecturesift import assistant_catalog, assistant_maintenance as maintenance


def test_disabled_schema_does_not_schedule_database_work(monkeypatch):
    monkeypatch.setattr(assistant_catalog, 'SCHEMA_RECOVERY_RELEASE_READY', False)
    monkeypatch.setattr(maintenance.assistant_wallet, 'prune_private_cache', lambda: (_ for _ in ()).throw(AssertionError('must not query')))
    asyncio.run(maintenance.run_once())


def test_retention_runs_with_chat_disabled_and_logs_no_private_error(monkeypatch, caplog):
    monkeypatch.setattr(assistant_catalog, 'SCHEMA_RECOVERY_RELEASE_READY', True)
    monkeypatch.setenv('ASSISTANT_ENABLED', 'false')
    called = []
    def fail():
        called.append(True)
        raise RuntimeError('private-customer-answer')
    monkeypatch.setattr(maintenance.assistant_wallet, 'prune_private_cache', fail)
    asyncio.run(maintenance.run_once())
    assert called == [True]
    assert 'Assistant retention maintenance failed' in caplog.text
    assert 'private-customer-answer' not in caplog.text


def test_api_shutdown_cancels_its_maintenance_task(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def loop():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        monkeypatch.setattr(maintenance, '_loop', loop)
        async with maintenance.lifespan(None):
            await started.wait()
            assert not stopped.is_set()
        assert stopped.is_set()
    asyncio.run(scenario())


def test_expired_jobs_are_pruned_by_the_api_maintenance_loop(monkeypatch, caplog):
    import logging
    import threading

    from lecturesift import config, jobs

    caplog.set_level(logging.INFO, logger=maintenance.__name__)
    loop_thread = threading.current_thread()
    calls = []

    def cleanup():
        calls.append(threading.current_thread())
        return 2

    monkeypatch.setattr(config, 'current_maintenance_mode', lambda: 'off')
    monkeypatch.setattr(jobs.JOBS, 'cleanup_expired', cleanup)
    asyncio.run(maintenance.run_job_retention_once())
    assert len(calls) == 1 and calls[0] is not loop_thread
    assert 'Expired job retention removed 2 jobs' in caplog.text


def test_job_pruning_waits_while_a_drain_or_freeze_is_active(monkeypatch):
    from lecturesift import config, jobs

    monkeypatch.setattr(jobs.JOBS, 'cleanup_expired', lambda: (_ for _ in ()).throw(AssertionError('must not prune')))
    for mode in ('drain', 'freeze'):
        monkeypatch.setattr(config, 'current_maintenance_mode', lambda mode=mode: mode)
        asyncio.run(maintenance.run_job_retention_once())


def test_job_pruning_failure_logs_no_private_detail(monkeypatch, caplog):
    from lecturesift import config, jobs

    def fail():
        raise RuntimeError('/var/lib/lecturesift/private-job-id')

    monkeypatch.setattr(config, 'current_maintenance_mode', lambda: 'off')
    monkeypatch.setattr(jobs.JOBS, 'cleanup_expired', fail)
    asyncio.run(maintenance.run_job_retention_once())
    assert 'Expired job retention failed' in caplog.text
    assert 'private-job-id' not in caplog.text


def test_job_pruning_runs_at_startup_and_then_on_its_interval(monkeypatch):
    runs = []
    sleeps = []

    async def run():
        runs.append(len(sleeps))

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(maintenance, 'run_job_retention_once', run)
    monkeypatch.setattr(maintenance.asyncio, 'sleep', sleep)
    try:
        asyncio.run(maintenance._job_retention_loop())
    except asyncio.CancelledError:
        pass
    assert runs == [0, 1]
    assert sleeps == [maintenance.JOB_RETENTION_INTERVAL_SECONDS] * 2
    assert maintenance.JOB_RETENTION_INTERVAL_SECONDS <= 60 * 60
