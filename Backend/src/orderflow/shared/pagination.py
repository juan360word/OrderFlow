"""Pagination primitives shared by every module.

Offset/limit is used deliberately: it is what an admin table with page numbers
needs. Its weakness is well known — a large OFFSET makes PostgreSQL scan and
discard every skipped row, and a concurrent insert shifts items between pages.
Keyset ("cursor") pagination fixes both but cannot jump to page 47. The hard
cap on ``limit`` is the security-relevant part: without it, ``?limit=1000000``
is a one-request denial of service.
"""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")

MAX_PAGE_SIZE = 100


class PageParams(BaseModel):
    """Validated ``?limit=&offset=`` query parameters."""

    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = 20
    offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Items per page")] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000, description="Items to skip")] = 0,
) -> PageParams:
    """FastAPI dependency producing validated pagination parameters."""
    return PageParams(limit=limit, offset=offset)


class Page(BaseModel, Generic[T]):
    """Envelope returned by every list endpoint.

    Returning ``{"items": [...], "total": n}`` rather than a bare JSON array is
    both a usability and a security choice: clients get the metadata they need
    to paginate, and a top-level array is the shape exploited by the classic
    JSON hijacking attack.
    """

    items: list[T]
    total: int = Field(description="Total rows matching the filter, ignoring pagination")
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total
