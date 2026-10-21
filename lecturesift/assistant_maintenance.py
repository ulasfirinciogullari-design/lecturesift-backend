"""Short-lived assistant data maintenance in each API process, without DDL."""
import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from . import assistant_catalog, assistant_wallet

LOGGER = logging.getLogger(__name__)
# Expired lesson results used to be removed during each upload request. Run
# the removal here instead so it never delays an upload and still happens on
# days without uploads.
JOB_RETENTION_INTERVAL_SECONDS = 15 * 60
TEMP_CLEANUP_INTERVAL_SECONDS = 60 * 60


async def run_once():
    if not assistant_catalog.SCHEMA_RECOVERY_RELEASE_READY:
        return
    try:
        result = await asyncio.to_thread(assistant_wallet.prune_private_cache)
        if max(result['responses_cleared'], result['trial_keys_removed']) >= 1000:
            LOGGER.warning('Assistant retention batch reached its limit; check maintenance backlog')
    except Exception:
        # Driver exceptions can contain SQL parameters or cached text. Log only
        # an operational signal; never include exception bodies or identifiers.
        LOGGER.error('Assistant retention maintenance failed')


async def run_job_retention_once():
    from . import config
    from .jobs import JOBS

    # Uploads (and so this cleanup) were always fenced off during a drain or
    # freeze; keep backups and migrations free of concurrent deletions.
    if config.current_maintenance_mode() != "off":
        return
    try:
        removed = await asyncio.to_thread(JOBS.cleanup_expired)
    except Exception:
        LOGGER.error('Expired job retention failed')
        return
    if removed:
        LOGGER.info('Expired job retention removed %d jobs', removed)


async def run_temp_cleanup_once():
    from .api_temp import clean_stale_temp_entries

    try:
        await asyncio.to_thread(clean_stale_temp_entries)
    except Exception:
        LOGGER.error('API temporary file cleanup failed')


async def _loop():
    while True:
        await run_once()
        await asyncio.sleep(60)


async def _job_retention_loop():
    while True:
        await run_job_retention_once()
        await asyncio.sleep(JOB_RETENTION_INTERVAL_SECONDS)


async def _temp_cleanup_loop():
    while True:
        await asyncio.sleep(TEMP_CLEANUP_INTERVAL_SECONDS)
        await run_temp_cleanup_once()


@asynccontextmanager
async def lifespan(app):
    from .api_temp import prepare_api_temp_dir
    from .billing_service import require_production_session_secret
    from .durable_runtime import stuck_job_loop
    from .job_notifications import delivery_loop
    from .payments import iyzico_reconciliation_loop
    from .renewal_reminders import reminder_loop

    # Both checks raise before the API accepts a request: a production API
    # without a real session secret, or without a private disk-backed temp
    # directory for upload spooling, must not start.
    require_production_session_secret()
    prepare_api_temp_dir()
    tasks = [
        asyncio.create_task(_loop(), name='assistant-retention'),
        asyncio.create_task(_job_retention_loop(), name='job-retention'),
        asyncio.create_task(_temp_cleanup_loop(), name='api-temp-cleanup'),
        asyncio.create_task(delivery_loop(), name='job-failure-notifications'),
        asyncio.create_task(reminder_loop(), name='renewal-reminders'),
        asyncio.create_task(stuck_job_loop(), name='stuck-job-sweep'),
        asyncio.create_task(iyzico_reconciliation_loop(), name='iyzico-reconciliation'),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
