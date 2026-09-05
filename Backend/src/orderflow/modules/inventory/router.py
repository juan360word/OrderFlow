"""HTTP layer for the inventory module.

Reservations are an internal, trusted operation: in the finished system they
are issued by the orders module, not by a browser. They are exposed here so the
concurrency behaviour is demonstrable end to end, and they require an
authenticated caller.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from orderflow.core.dependencies import AdminUser, CurrentUser
from orderflow.modules.inventory.dependencies import InventoryServiceDep
from orderflow.modules.inventory.schemas import (
    ReservationRequest,
    ReservationResponse,
    StockAdjustment,
    StockResponse,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get(
    "/{product_id}",
    response_model=StockResponse,
    summary="Current stock for a product",
    responses={404: {"description": "No inventory record for this product"}},
)
async def get_stock(product_id: uuid.UUID, service: InventoryServiceDep) -> StockResponse:
    """Read the counters. Public: availability is catalogue information."""
    inventory = await service.get_stock(product_id)
    return StockResponse.model_validate(inventory)


@router.post(
    "/reservations",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reserve stock",
    responses={
        409: {"description": "Insufficient stock, or the reference is in flight"},
        404: {"description": "No inventory record for this product"},
    },
)
async def reserve_stock(
    payload: ReservationRequest,
    user: CurrentUser,
    service: InventoryServiceDep,
) -> ReservationResponse:
    """Hold stock for a reference.

    Idempotent: repeating the call with the same ``reference`` returns the
    original reservation rather than holding stock twice. Returns 409 when the
    request loses the race for the last units.
    """
    reservation = await service.reserve(
        payload.product_id, quantity=payload.quantity, reference=payload.reference
    )
    return ReservationResponse.model_validate(reservation)


@router.post(
    "/reservations/{reference}/confirm",
    response_model=ReservationResponse,
    summary="Confirm a reservation (the goods ship)",
)
async def confirm_reservation(
    reference: str,
    product_id: uuid.UUID,
    user: CurrentUser,
    service: InventoryServiceDep,
) -> ReservationResponse:
    """Consume the hold. The reserved units leave the warehouse for good."""
    reservation = await service.confirm(reference, product_id)
    return ReservationResponse.model_validate(reservation)


@router.post(
    "/reservations/{reference}/release",
    response_model=ReservationResponse,
    summary="Release a reservation (the stock goes back on sale)",
)
async def release_reservation(
    reference: str,
    product_id: uuid.UUID,
    user: CurrentUser,
    service: InventoryServiceDep,
) -> ReservationResponse:
    """Cancel the hold and return the units to the sellable pool."""
    reservation = await service.release(reference, product_id)
    return ReservationResponse.model_validate(reservation)


@router.post(
    "/{product_id}/adjust",
    response_model=StockResponse,
    summary="Restock or correct stock (admin)",
)
async def adjust_stock(
    product_id: uuid.UUID,
    payload: StockAdjustment,
    admin: AdminUser,
    service: InventoryServiceDep,
) -> StockResponse:
    """Apply a signed delta to available stock, with an audited reason."""
    inventory = await service.adjust(
        product_id, delta=payload.delta, reason=payload.reason, actor_id=admin.id
    )
    return StockResponse.model_validate(inventory)
