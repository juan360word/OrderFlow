"""Order lifecycle: the set of states and the transitions allowed between them."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from orderflow.core.errors import ConflictError


class OrderStatus(StrEnum):
    """States an order can be in."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


ALLOWED_TRANSITIONS: Final[Mapping[OrderStatus, frozenset[OrderStatus]]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.CANCELLED}),
    OrderStatus.CANCELLED: frozenset(),
}

TERMINAL_STATUSES: Final[frozenset[OrderStatus]] = frozenset(
    status for status, targets in ALLOWED_TRANSITIONS.items() if not targets
)


class InvalidTransitionError(ConflictError):
    """The requested state change is not part of the order lifecycle."""

    code = "invalid_order_transition"
    message = "The order cannot move to the requested state."


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    """Whether ``current -> target`` is a legal move."""
    return target in ALLOWED_TRANSITIONS[current]


def assert_can_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Raise :class:`InvalidTransitionError` unless the move is legal."""
    if not can_transition(current, target):
        raise InvalidTransitionError(
            f"An order in state '{current.value}' cannot become '{target.value}'.",
            details={
                "current_status": current.value,
                "requested_status": target.value,
                "allowed": sorted(status.value for status in ALLOWED_TRANSITIONS[current]),
            },
        )


def is_terminal(status: OrderStatus) -> bool:
    """Whether the order can never change state again."""
    return status in TERMINAL_STATUSES
