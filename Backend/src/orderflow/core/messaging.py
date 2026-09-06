"""Message publishing and consuming over SQS.

Why a queue at all: sending a confirmation email inside ``POST /orders`` makes
the customer wait for something irrelevant to their purchase, and it couples
the order to the email provider being up. Publishing an event instead lets the
API answer as soon as the order is durable, and the notification happens on its
own schedule.

Two delivery facts a consumer must be built around:

* **At least once.** SQS may deliver the same message twice - after a
  visibility timeout expires, after a network partition, or because the delete
  call was lost. Consumers therefore have to be idempotent; that is why every
  event carries an ``event_id``.
* **Visibility timeout.** A received message is hidden, not removed. If the
  worker does not delete it within the timeout, it reappears for another
  attempt. Work that takes longer than the timeout will be processed twice.

Retries and the dead letter queue are configured on the queue itself, not in
this code: after ``maxReceiveCount`` failed attempts SQS moves the message to
the DLQ. Without that, one message the worker can never process - a "poison
pill" - would be redelivered forever and block progress.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import aioboto3
from botocore.exceptions import BotoCoreError, ClientError

from orderflow.core.config import Settings
from orderflow.core.logging import get_logger
from orderflow.shared.events import DomainEvent

logger = get_logger(__name__)


class MessagePublishError(RuntimeError):
    """The broker refused or could not be reached."""


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    """One message pulled off a queue."""

    receipt_handle: str
    body: dict[str, Any]
    receive_count: int
    message_id: str


class MessagePublisher(Protocol):
    """The contract the rest of the system depends on.

    A protocol, not a concrete class, so the order module never imports boto3
    and tests can substitute an in-memory double without a broker running.
    """

    async def publish(self, event: DomainEvent) -> str: ...

    async def publish_many(self, events: Sequence[DomainEvent]) -> list[str]: ...


class InMemoryMessagePublisher:
    """Records events instead of sending them. For tests and local runs."""

    def __init__(self) -> None:
        self.published: list[DomainEvent] = []
        self.fail_next = 0

    async def publish(self, event: DomainEvent) -> str:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise MessagePublishError("Simulated publish failure.")
        self.published.append(event)
        return str(event.event_id)

    async def publish_many(self, events: Sequence[DomainEvent]) -> list[str]:
        return [await self.publish(event) for event in events]

    def clear(self) -> None:
        self.published.clear()
        self.fail_next = 0


class SqsMessagePublisher:
    """Publishes events to an SQS queue."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=(
                settings.aws_secret_access_key.get_secret_value()
                if settings.aws_secret_access_key
                else None
            ),
            region_name=settings.aws_region,
        )

    def _client(self) -> Any:
        return self._session.client("sqs", endpoint_url=self._settings.aws_endpoint_url)

    async def publish(self, event: DomainEvent) -> str:
        async with self._client() as sqs:
            try:
                response = await sqs.send_message(
                    QueueUrl=self._settings.orders_queue_url,
                    MessageBody=json.dumps(event.to_message()),
                    MessageAttributes={
                        "event_type": {
                            "DataType": "String",
                            "StringValue": event.event_type.value,
                        }
                    },
                )
            except (ClientError, BotoCoreError, OSError) as exc:
                logger.error("event_publish_failed", event_id=str(event.event_id))
                raise MessagePublishError(str(exc)) from exc

        logger.info(
            "event_published",
            event_id=str(event.event_id),
            event_type=event.event_type.value,
        )
        return str(response["MessageId"])

    async def publish_many(self, events: Sequence[DomainEvent]) -> list[str]:
        return [await self.publish(event) for event in events]


class SqsConsumer:
    """Long-polling reader for one queue."""

    def __init__(self, settings: Settings, queue_url: str) -> None:
        self._settings = settings
        self._queue_url = queue_url
        self._session = aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=(
                settings.aws_secret_access_key.get_secret_value()
                if settings.aws_secret_access_key
                else None
            ),
            region_name=settings.aws_region,
        )

    def _client(self) -> Any:
        return self._session.client("sqs", endpoint_url=self._settings.aws_endpoint_url)

    async def receive(self, *, max_messages: int = 10) -> list[ReceivedMessage]:
        """Fetch a batch, waiting for work rather than spinning.

        Long polling (``WaitTimeSeconds``) holds the request open until a
        message arrives. Short polling would return empty immediately and the
        worker would burn CPU and API calls asking again.
        """
        async with self._client() as sqs:
            response = await sqs.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=min(max_messages, 10),
                WaitTimeSeconds=self._settings.sqs_wait_time_seconds,
                VisibilityTimeout=self._settings.sqs_visibility_timeout_seconds,
                MessageAttributeNames=["All"],
                AttributeNames=["ApproximateReceiveCount"],
            )

        messages: list[ReceivedMessage] = []
        for raw in response.get("Messages", []):
            try:
                body = json.loads(raw["Body"])
            except json.JSONDecodeError:
                logger.error("message_body_not_json", message_id=raw.get("MessageId"))
                continue
            messages.append(
                ReceivedMessage(
                    receipt_handle=raw["ReceiptHandle"],
                    body=body,
                    receive_count=int(raw.get("Attributes", {}).get("ApproximateReceiveCount", 1)),
                    message_id=raw["MessageId"],
                )
            )
        return messages

    async def delete(self, receipt_handle: str) -> None:
        """Acknowledge a message. Until this runs, SQS will redeliver it."""
        async with self._client() as sqs:
            await sqs.delete_message(QueueUrl=self._queue_url, ReceiptHandle=receipt_handle)

    async def release(self, receipt_handle: str, *, delay_seconds: int = 0) -> None:
        """Make a message visible again, optionally after a backoff.

        Used when processing failed but is worth retrying: rather than waiting
        out the full visibility timeout, we hand it back immediately, or after
        a delay that grows with the attempt count.
        """
        async with self._client() as sqs:
            await sqs.change_message_visibility(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=delay_seconds,
            )


async def ensure_queues(settings: Settings) -> dict[str, str]:
    """Create the work queue and its dead letter queue if they are missing.

    Idempotent, so it is safe to run on every boot. The redrive policy is what
    wires them together: after ``maxReceiveCount`` failed attempts SQS moves
    the message to the DLQ instead of redelivering it forever.
    """
    session = aioboto3.Session(
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=(
            settings.aws_secret_access_key.get_secret_value()
            if settings.aws_secret_access_key
            else None
        ),
        region_name=settings.aws_region,
    )
    async with session.client("sqs", endpoint_url=settings.aws_endpoint_url) as sqs:
        dlq = await sqs.create_queue(QueueName=settings.orders_dlq_name)
        dlq_attributes = await sqs.get_queue_attributes(
            QueueUrl=dlq["QueueUrl"], AttributeNames=["QueueArn"]
        )
        dlq_arn = dlq_attributes["Attributes"]["QueueArn"]

        main = await sqs.create_queue(
            QueueName=settings.orders_queue_name,
            Attributes={
                "VisibilityTimeout": str(settings.sqs_visibility_timeout_seconds),
                "MessageRetentionPeriod": "1209600",
                "RedrivePolicy": json.dumps(
                    {
                        "deadLetterTargetArn": dlq_arn,
                        "maxReceiveCount": settings.sqs_max_receive_count,
                    }
                ),
            },
        )

    logger.info("queues_ready", queue=settings.orders_queue_name, dlq=settings.orders_dlq_name)
    return {"queue_url": main["QueueUrl"], "dlq_url": dlq["QueueUrl"]}
