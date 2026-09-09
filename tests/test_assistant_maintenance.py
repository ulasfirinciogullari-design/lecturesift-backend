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
