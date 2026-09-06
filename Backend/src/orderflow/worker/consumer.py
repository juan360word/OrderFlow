"""Queue consumer.

Delivery is at least once, so the loop is built around that fact rather than
hoping it does not happen:

* Before doing any work, the consumer claims a slot in ``processed_events``.
  A claim that fails means this event was already handled and the message is
  simply acknowledged.
* The claim and the handler run in one transaction, so a handler that fails
  rolls the claim back and the event is genuinely retried.
* A message that keeps failing is left for SQS to redeliver until
  ``maxReceiveCount`` sends it to the dead letter queue, instead of being
  retried forever in-process.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from types import FrameType

from orderflow.core.config import Settings, get_settings
from orderflow.core.database import Database
from orderflow.core.logging import configure_logging, get_logger
from orderflow.core.messaging import ReceivedMessage, SqsConsumer, ensure_queues
from orderflow.modules.outbox.service import OutboxService
from orderflow.shared.events import DomainEvent
from orderflow.worker.handlers import NotificationHandler

logger = get_logger(__name__)


class EventConsumer:
    """Reads events off a queue and dispatches them to handlers."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        consumer: SqsConsumer,
        handler: NotificationHandler | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._consumer = consumer
        self._handler = handler or NotificationHandler()
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """Poll until asked to stop."""
        logger.info("consumer_started", queue=self._settings.orders_queue_name)
        while not self._stopping.is_set():
            try:
                messages = await self._consumer.receive()
            except Exception:
                logger.exception("consumer_receive_failed")
                await self._sleep(self._settings.outbox_poll_interval_seconds)
                continue

            for message in messages:
                if self._stopping.is_set():
                    break
                await self.process(message)
        logger.info("consumer_stopped")

    async def process(self, message: ReceivedMessage) -> bool:
        """Handle one message. Returns whether it was acknowledged."""
        try:
            event = DomainEvent.from_message(message.body)
        except ValueError as exc:
            logger.error(
                "event_unparseable_dropped",
                message_id=message.message_id,
                error=str(exc),
            )
            await self._consumer.delete(message.receipt_handle)
            return True

        try:
            async with self._database.session() as session:
                outbox = OutboxService(session, self._settings)
                claimed = await outbox.claim_processing_slot(
                    event_id=event.event_id,
                    consumer=self._handler.name,
                    event_type=event.event_type.value,
                )
                if not claimed:
                    logger.info("event_already_processed", event_id=str(event.event_id))
                    await self._consumer.delete(message.receipt_handle)
                    return True

                await self._handler.handle(event)
        except Exception as exc:
            logger.warning(
                "event_processing_failed",
                event_id=str(event.event_id),
                receive_count=message.receive_count,
                error_type=type(exc).__name__,
            )
            if message.receive_count >= self._settings.sqs_max_receive_count:
                logger.error(
                    "event_exhausted_retries_going_to_dlq",
                    event_id=str(event.event_id),
                    receive_count=message.receive_count,
                )
            with contextlib.suppress(Exception):
                await self._consumer.release(
                    message.receipt_handle,
                    delay_seconds=min(2**message.receive_count, 60),
                )
            return False

        await self._consumer.delete(message.receipt_handle)
        logger.info("event_processed", event_id=str(event.event_id))
        return True

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)


async def main() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    configure_logging(debug=settings.debug)
    await ensure_queues(settings)

    database = Database(settings)
    consumer = EventConsumer(settings, database, SqsConsumer(settings, settings.orders_queue_url))
    _install_signal_handlers(consumer)
    try:
        await consumer.run()
    finally:
        await database.dispose()


def _install_signal_handlers(consumer: EventConsumer) -> None:  # pragma: no cover
    """Finish the message in hand before exiting.

    A container being replaced gets SIGTERM. Dying immediately would leave that
    message to reappear after its visibility timeout and be processed twice.
    """
    loop = asyncio.get_running_loop()

    def handle(_signum: int, _frame: FrameType | None = None) -> None:
        logger.info("shutdown_signal_received")
        consumer.request_stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, handle, sig, None)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
