"""Aggregate router for API v1.

Versioning in the URL prefix (``/api/v1``) rather than in a header: it is
visible in logs and browser history, trivially routable at the load balancer,
and it lets v1 and v2 run side by side during a migration.
"""

from fastapi import APIRouter

from orderflow.modules.auth.router import router as auth_router
from orderflow.modules.inventory.router import router as inventory_router
from orderflow.modules.orders.router import router as orders_router
from orderflow.modules.products.router import router as products_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(products_router)
api_router.include_router(inventory_router)
api_router.include_router(orders_router)

__all__ = ["api_router"]
