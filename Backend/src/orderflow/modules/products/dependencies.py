"""Dependency wiring for the products module."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from orderflow.core.dependencies import DbSession
from orderflow.modules.inventory.dependencies import InventoryServiceDep
from orderflow.modules.products.service import ProductService


def get_product_service(session: DbSession, inventory: InventoryServiceDep) -> ProductService:
    """Both services share the request's session — hence the transaction."""
    return ProductService(session, inventory)


ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]
