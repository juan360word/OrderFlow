"""Event handlers: the actual work done off the request path."""

from __future__ import annotations

from orderflow.core.logging import get_logger
from orderflow.shared.events import DomainEvent, EventType

logger = get_logger(__name__)


class NotificationHandler:
    """Stands in for an email or push provider.

    Kept as a simulation on purpose: the point of this phase is the delivery
    machinery around the handler - at-least-once delivery, retries, the dead
    letter queue, deduplication - not the provider integration. Swapping in a
    real client changes only this class.
    """

    name = "notification"

    def __init__(self) -> None:
        self.handled: list[DomainEvent] = []
        self.fail_next = 0

    async def handle(self, event: DomainEvent) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("Simulated notification provider failure.")

        message = {
            EventType.ORDER_CREATED: "Your order has been received.",
            EventType.ORDER_CONFIRMED: "Your order is confirmed and on its way.",
            EventType.ORDER_CANCELLED: "Your order has been cancelled.",
        }[event.event_type]

        self.handled.append(event)
        logger.info(
            "notification_sent",
            event_id=str(event.event_id),
            event_type=event.event_type.value,
            order_id=event.payload.get("order_id"),
            recipient_user_id=event.payload.get("user_id"),
            message=message,
        )
