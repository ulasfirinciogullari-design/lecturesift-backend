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
    task = asyncio.create_task(_loop(), name='assistant-retention')
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
