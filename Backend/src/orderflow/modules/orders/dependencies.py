"""Dependency wiring for the orders module."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from orderflow.core.dependencies import DbSession
from orderflow.modules.inventory.dependencies import InventoryServiceDep
from orderflow.modules.orders.service import OrderService
from orderflow.modules.products.dependencies import ProductServiceDep


def get_order_service(
    session: DbSession,
    products: ProductServiceDep,
    inventory: InventoryServiceDep,
) -> OrderService:
    """All three services share the request's session, hence its transaction."""
    return OrderService(session, products, inventory)


OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]
