"""Dependency wiring for the inventory module."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from orderflow.core.dependencies import DbSession, SettingsDep
from orderflow.modules.inventory.service import InventoryService


def get_inventory_service(session: DbSession, settings: SettingsDep) -> InventoryService:
    return InventoryService(session, settings)


InventoryServiceDep = Annotated[InventoryService, Depends(get_inventory_service)]
