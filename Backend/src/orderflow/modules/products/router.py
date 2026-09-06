"""HTTP layer for the products module.

Authorization pattern used here: reads are public, writes are admin-only, and
the restriction is declared in the route signature (``AdminUser``) rather than
checked inside the handler. A reviewer can see the permission of every endpoint
without reading its body, and forgetting the check becomes visible in the diff.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from orderflow.core.dependencies import AdminUser
from orderflow.modules.products.dependencies import ProductServiceDep
from orderflow.modules.products.schemas import (
    ProductCreate,
    ProductFilters,
    ProductResponse,
    ProductUpdate,
)
from orderflow.shared.pagination import Page, PageParams, page_params

router = APIRouter(prefix="/products", tags=["products"])


@router.get(
    "",
    response_model=Page[ProductResponse],
    summary="List the catalogue",
)
async def list_products(
    service: ProductServiceDep,
    page: Annotated[PageParams, Depends(page_params)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
) -> Page[ProductResponse]:
    """Public, paginated catalogue. Inactive products are never listed."""
    filters = ProductFilters(
        search=search,
        min_price=min_price,
        max_price=max_price,
        include_inactive=False,
    )
    products, total = await service.list_products(filters, page)
    return Page[ProductResponse](
        items=[ProductResponse.model_validate(product) for product in products],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Fetch one product",
    responses={404: {"description": "Product not found"}},
)
async def get_product(product_id: uuid.UUID, service: ProductServiceDep) -> ProductResponse:
    """Public detail view, served through the Redis cache-aside path."""
    payload = await service.get_cached(product_id)
    return ProductResponse.model_validate(payload)


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product (admin)",
    responses={409: {"description": "SKU already exists"}},
)
async def create_product(
    payload: ProductCreate,
    admin: AdminUser,
    service: ProductServiceDep,
) -> ProductResponse:
    """Create a product and its inventory row in one transaction."""
    product = await service.create(payload, created_by=admin.id)
    return ProductResponse.model_validate(product)


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Update a product (admin)",
)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    admin: AdminUser,
    service: ProductServiceDep,
) -> ProductResponse:
    """Partial update. Only the fields present in the body are written."""
    product = await service.update(product_id, payload, updated_by=admin.id)
    return ProductResponse.model_validate(product)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a product from sale (admin)",
)
async def deactivate_product(
    product_id: uuid.UUID,
    admin: AdminUser,
    service: ProductServiceDep,
) -> None:
    """Soft delete: the row survives so order history stays intact."""
    await service.deactivate(product_id, deleted_by=admin.id)
