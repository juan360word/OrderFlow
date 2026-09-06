"""Consumer tests (phase 10): retries, deduplication and the dead letter path."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from orderflow.core.config import Settings
from orderflow.core.database import Database
from orderflow.core.messaging import ReceivedMessage
from orderflow.shared.events import DomainEvent, EventType
from orderflow.worker.consumer import EventConsumer
from orderflow.worker.handlers import NotificationHandler

pytestmark = pytest.mark.integration


class FakeQueue:
    """Stands in for SQS: records acknowledgements and re-releases."""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.released: list[tuple[str, int]] = []

    async def receive(self, *, max_messages: int = 10) -> list[ReceivedMessage]:
        return []

    async def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    async def release(self, receipt_handle: str, *, delay_seconds: int = 0) -> None:
        self.released.append((receipt_handle, delay_seconds))


def _message(event: DomainEvent, *, receive_count: int = 1) -> ReceivedMessage:
    return ReceivedMessage(
        receipt_handle=f"receipt-{event.event_id}",
        body=event.to_message(),
        receive_count=receive_count,
        message_id=str(uuid.uuid4()),
    )


def _event() -> DomainEvent:
    return DomainEvent(
        event_type=EventType.ORDER_CREATED,
        aggregate_type="order",
        aggregate_id=uuid.uuid4(),
        payload={"order_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
    )


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def handler() -> NotificationHandler:
    return NotificationHandler()


@pytest.fixture
def consumer(
    settings: Settings, database: Database, queue: FakeQueue, handler: NotificationHandler
) -> EventConsumer:
    return EventConsumer(settings, database, queue, handler)  # type: ignore[arg-type]


async def test_a_message_is_handled_and_acknowledged(
    consumer: EventConsumer, queue: FakeQueue, handler: NotificationHandler
) -> None:
    event = _event()

    assert await consumer.process(_message(event)) is True
    assert len(handler.handled) == 1
    assert queue.deleted == [f"receipt-{event.event_id}"]


async def test_a_redelivered_message_is_not_processed_twice(
    consumer: EventConsumer, queue: FakeQueue, handler: NotificationHandler
) -> None:
    """At-least-once delivery means this will happen; the consumer must absorb it."""
    event = _event()

    await consumer.process(_message(event))
    await consumer.process(_message(event, receive_count=2))

    assert len(handler.handled) == 1, "the work must run exactly once"
    assert len(queue.deleted) == 2, "but both copies must be acknowledged"


async def test_a_failed_handler_releases_the_message_for_retry(
    consumer: EventConsumer, queue: FakeQueue, handler: NotificationHandler
) -> None:
    handler.fail_next = 1
    event = _event()

    assert await consumer.process(_message(event)) is False
    assert queue.deleted == []
    assert queue.released[0][0] == f"receipt-{event.event_id}"


async def test_a_failure_rolls_back_the_dedup_claim(
    consumer: EventConsumer, handler: NotificationHandler
) -> None:
    """Otherwise a transient failure would permanently swallow the event."""
    handler.fail_next = 1
    event = _event()

    await consumer.process(_message(event))
    await consumer.process(_message(event, receive_count=2))

    assert len(handler.handled) == 1


async def test_the_retry_delay_grows_with_the_attempt(
    consumer: EventConsumer, queue: FakeQueue, handler: NotificationHandler
) -> None:
    handler.fail_next = 2
    event = _event()

    await consumer.process(_message(event, receive_count=1))
    await consumer.process(_message(event, receive_count=3))

    assert queue.released[1][1] > queue.released[0][1]


async def test_an_unparseable_message_is_dropped_not_retried(
    consumer: EventConsumer, queue: FakeQueue, handler: NotificationHandler
) -> None:
    """A poison pill that can never succeed must not block the queue forever."""
    poison = ReceivedMessage(
        receipt_handle="receipt-poison",
        body={"not": "an event"},
        receive_count=1,
        message_id="poison",
    )

    assert await consumer.process(poison) is True
    assert queue.deleted == ["receipt-poison"]
    assert handler.handled == []


async def test_an_exhausted_message_is_left_for_the_dead_letter_queue(
    consumer: EventConsumer,
    queue: FakeQueue,
    handler: NotificationHandler,
    settings: Settings,
) -> None:
    """SQS moves it to the DLQ once maxReceiveCount is reached; we must not delete it."""
    handler.fail_next = 1
    event = _event()

    acknowledged = await consumer.process(
        _message(event, receive_count=settings.sqs_max_receive_count)
    )

    assert acknowledged is False
    assert queue.deleted == [], "deleting it here would lose it instead of parking it"


async def test_every_event_type_has_a_notification(handler: NotificationHandler) -> None:
    for event_type in EventType:
        event = DomainEvent(
            event_type=event_type,
            aggregate_type="order",
            aggregate_id=uuid.uuid4(),
            payload={"order_id": str(uuid.uuid4())},
        )
        await handler.handle(event)

    assert len(handler.handled) == len(EventType)


async def test_the_handler_reads_only_the_payload(handler: NotificationHandler) -> None:
    """No database lookup: the event carries what the consumer needs."""
    payload: dict[str, Any] = {"order_id": "abc", "user_id": "def"}
    event = DomainEvent(
        event_type=EventType.ORDER_CREATED,
        aggregate_type="order",
        aggregate_id=uuid.uuid4(),
        payload=payload,
    )

    await handler.handle(event)

    assert handler.handled[0].payload == payload
