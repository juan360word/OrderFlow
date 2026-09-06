"""Unit tests for the order state machine (phase 7)."""

from __future__ import annotations

import pytest

from orderflow.modules.orders.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    OrderStatus,
    assert_can_transition,
    can_transition,
    is_terminal,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.PENDING, OrderStatus.CONFIRMED),
        (OrderStatus.PENDING, OrderStatus.CANCELLED),
        (OrderStatus.CONFIRMED, OrderStatus.CANCELLED),
    ],
)
def test_legal_transitions(current: OrderStatus, target: OrderStatus) -> None:
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.CONFIRMED, OrderStatus.PENDING),
        (OrderStatus.CANCELLED, OrderStatus.PENDING),
        (OrderStatus.CANCELLED, OrderStatus.CONFIRMED),
        (OrderStatus.CANCELLED, OrderStatus.CANCELLED),
        (OrderStatus.PENDING, OrderStatus.PENDING),
    ],
)
def test_illegal_transitions(current: OrderStatus, target: OrderStatus) -> None:
    assert not can_transition(current, target)

    with pytest.raises(InvalidTransitionError):
        assert_can_transition(current, target)


def test_the_error_lists_what_was_allowed() -> None:
    with pytest.raises(InvalidTransitionError) as exc_info:
        assert_can_transition(OrderStatus.CANCELLED, OrderStatus.CONFIRMED)

    details = exc_info.value.details
    assert details["current_status"] == "cancelled"
    assert details["requested_status"] == "confirmed"
    assert details["allowed"] == []


def test_cancelled_is_the_only_terminal_state() -> None:
    assert is_terminal(OrderStatus.CANCELLED)
    assert not is_terminal(OrderStatus.PENDING)
    assert not is_terminal(OrderStatus.CONFIRMED)


def test_every_status_is_declared_in_the_table() -> None:
    """A new status must be given transitions, not silently default to none."""
    assert set(ALLOWED_TRANSITIONS) == set(OrderStatus)
