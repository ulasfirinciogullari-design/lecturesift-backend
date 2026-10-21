"""Short-lived assistant data maintenance in each API process, without DDL."""
import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from . import assistant_catalog, assistant_wallet

LOGGER = logging.getLogger(__name__)


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


async def _loop():
    while True:
        await run_once()
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    from .durable_runtime import stuck_job_loop
    from .job_notifications import delivery_loop
    from .payments import iyzico_reconciliation_loop
    from .renewal_reminders import reminder_loop
    tasks = [
        asyncio.create_task(_loop(), name='assistant-retention'),
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
