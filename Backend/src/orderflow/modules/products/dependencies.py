"""Dependency wiring for the products module."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from orderflow.core.dependencies import CacheDep, DbSession, SettingsDep
from orderflow.modules.inventory.dependencies import InventoryServiceDep
from orderflow.modules.products.service import ProductService


def get_product_service(
    session: DbSession,
    inventory: InventoryServiceDep,
    cache: CacheDep,
    settings: SettingsDep,
) -> ProductService:
    """All collaborators share the request's session, hence its transaction."""
    return ProductService(
        session,
        inventory,
        cache=cache,
        cache_ttl_seconds=settings.product_cache_ttl_seconds,
        image_max_bytes=settings.product_image_max_bytes,
    )


ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]
