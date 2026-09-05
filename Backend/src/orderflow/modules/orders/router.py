"""HTTP layer for the orders module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from orderflow.core.dependencies import CurrentUser
from orderflow.modules.orders.dependencies import OrderServiceDep
from orderflow.modules.orders.schemas import (
    OrderCancel,
    OrderCreate,
    OrderFilters,
    OrderResponse,
    OrderSummaryResponse,
)
from orderflow.modules.orders.state_machine import OrderStatus
from orderflow.shared.pagination import Page, PageParams, page_params

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place an order",
    responses={
        404: {"description": "One or more products are unavailable"},
        409: {"description": "Insufficient stock for one of the lines"},
    },
)
async def create_order(
    payload: OrderCreate,
    user: CurrentUser,
    service: OrderServiceDep,
) -> OrderResponse:
    """Price the basket, reserve the stock and store the order in one transaction."""
    order = await service.create(payload, customer=user)
    return OrderResponse.model_validate(order)


@router.get(
    "",
    response_model=Page[OrderSummaryResponse],
    summary="List your orders",
)
async def list_orders(
    user: CurrentUser,
    service: OrderServiceDep,
    page: Annotated[PageParams, Depends(page_params)],
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
) -> Page[OrderSummaryResponse]:
    """Return the caller's own orders. Administrators see every order."""
    orders, total = await service.list_orders(OrderFilters(status=order_status), page, actor=user)
    return Page[OrderSummaryResponse](
        items=[OrderSummaryResponse.model_validate(order) for order in orders],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Fetch one order",
    responses={404: {"description": "Order not found, or not yours"}},
)
async def get_order(
    order_id: uuid.UUID,
    user: CurrentUser,
    service: OrderServiceDep,
) -> OrderResponse:
    """Return one order with its lines, if the caller owns it."""
    order = await service.get(order_id, actor=user)
    return OrderResponse.model_validate(order)


@router.post(
    "/{order_id}/confirm",
    response_model=OrderResponse,
    summary="Confirm an order (admin)",
    responses={409: {"description": "The order is not in a confirmable state"}},
)
async def confirm_order(
    order_id: uuid.UUID,
    user: CurrentUser,
    service: OrderServiceDep,
) -> OrderResponse:
    """Consume the held stock and mark the order confirmed."""
    order = await service.confirm(order_id, actor=user)
    return OrderResponse.model_validate(order)


@router.post(
    "/{order_id}/cancel",
    response_model=OrderResponse,
    summary="Cancel an order",
    responses={409: {"description": "The order can no longer be cancelled"}},
)
async def cancel_order(
    order_id: uuid.UUID,
    payload: OrderCancel,
    user: CurrentUser,
    service: OrderServiceDep,
) -> OrderResponse:
    """Cancel the order and return its stock to the sellable pool."""
    order = await service.cancel(order_id, actor=user, reason=payload.reason)
    return OrderResponse.model_validate(order)
