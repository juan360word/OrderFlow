"""Outbox relay: moves staged events from PostgreSQL to the broker.

The relay is the second half of the transactional outbox. The API writes rows;
this process publishes them and marks them done. It is deliberately dumb - it
knows nothing about orders - so it can never be the reason an event is wrong,
only late.

Running several instances is safe: ``claim_batch`` locks the rows it takes with
``FOR UPDATE SKIP LOCKED``, so workers divide the backlog instead of colliding.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from types import FrameType

from orderflow.core.config import Settings, get_settings
from orderflow.core.database import Database
from orderflow.core.logging import configure_logging, get_logger
from orderflow.core.messaging import MessagePublisher, SqsMessagePublisher, ensure_queues
from orderflow.modules.outbox.service import OutboxService

logger = get_logger(__name__)


class OutboxRelay:
    """Polls the outbox and publishes whatever is ready."""

    def __init__(self, settings: Settings, database: Database, publisher: MessagePublisher) -> None:
        self._settings = settings
        self._database = database
        self._publisher = publisher
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        self._stopping.set()

    async def run_once(self) -> tuple[int, int]:
        """Relay a single batch.

        The whole batch shares one transaction, so a crash mid-batch leaves
        every row in it still pending. Re-publishing an event the broker
        already accepted is safe; losing one is not. That asymmetry is why the
        outbox errs toward duplicates.
        """
        async with self._database.session() as session:
            service = OutboxService(session, self._settings)
            return await service.relay_batch(self._publisher)

    async def run(self) -> None:
        """Poll until asked to stop."""
        logger.info("outbox_relay_started", interval=self._settings.outbox_poll_interval_seconds)
        while not self._stopping.is_set():
            try:
                published, failed = await self.run_once()
            except Exception:
                logger.exception("outbox_relay_iteration_failed")
                published = failed = 0

            if published == 0 and failed == 0:
                await self._sleep(self._settings.outbox_poll_interval_seconds)
        logger.info("outbox_relay_stopped")

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)


async def main() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    configure_logging(debug=settings.debug)
    await ensure_queues(settings)

    database = Database(settings)
    relay = OutboxRelay(settings, database, SqsMessagePublisher(settings))
    _install_signal_handlers(relay)
    try:
        await relay.run()
    finally:
        await database.dispose()


def _install_signal_handlers(relay: OutboxRelay) -> None:  # pragma: no cover
    loop = asyncio.get_running_loop()

    def handle(_signum: int, _frame: FrameType | None = None) -> None:
        logger.info("shutdown_signal_received")
        relay.request_stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, handle, sig, None)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
