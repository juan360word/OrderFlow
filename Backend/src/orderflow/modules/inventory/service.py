"""Inventory business rules: reserve, confirm, release.

This service is the public door of the module. Orders (a later phase) will call
``reserve`` and then ``confirm`` or ``release``; nothing outside this package
touches the ``inventory`` table.

Why a two-step reserve → confirm protocol instead of decrementing at payment:
the reservation is what makes the checkout honest. Stock is set aside the moment
the customer commits, so a slow payment provider cannot let a second customer
buy the same unit, and an abandoned checkout returns the stock automatically
when the hold expires.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Settings
from orderflow.core.errors import ConflictError, NotFoundError, ValidationError
from orderflow.core.logging import get_logger
from orderflow.modules.inventory.models import Inventory, InventoryReservation, ReservationStatus
from orderflow.modules.inventory.repository import InventoryRepository

logger = get_logger(__name__)


class InsufficientStockError(ConflictError):
    """Not enough available stock to satisfy the request.

    A 409 and not a 422: the request is perfectly valid, it just lost the race
    (or arrived too late). The distinction matters to the client, which should
    retry a 409 with a smaller quantity rather than treat it as a bad payload.
    """

    code = "insufficient_stock"
    message = "Not enough stock available."


class InventoryService:
    """Stock lifecycle with real concurrency control."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = InventoryRepository(session)

    async def initialize_stock(self, product_id: uuid.UUID, *, quantity: int = 0) -> Inventory:
        """Create the inventory row for a newly created product.

        Called by ``ProductService`` inside the same transaction, so a product
        can never exist without an inventory row.
        """
        if quantity < 0:
            raise ValidationError("Initial stock cannot be negative.")

        inventory = Inventory(product_id=product_id, quantity_available=quantity)
        self._repo.add(inventory)
        await self._session.flush()
        logger.info("inventory_initialized", product_id=str(product_id), quantity=quantity)
        return inventory

    async def get_stock(self, product_id: uuid.UUID) -> Inventory:
        inventory = await self._repo.get(product_id)
        if inventory is None:
            raise NotFoundError("No inventory record for this product.")
        return inventory

    async def reserve(
        self, product_id: uuid.UUID, *, quantity: int, reference: str
    ) -> InventoryReservation:
        """Hold ``quantity`` units for ``reference``.

        The whole operation runs in the request's single transaction, so the
        counter update and the ledger row commit together or not at all.

        Idempotency: ``(reference, product_id)`` is UNIQUE. A retried request
        finds the existing hold and returns it instead of reserving twice —
        which is what makes this endpoint safe for a client that times out and
        retries.
        """
        if quantity <= 0:
            raise ValidationError("Reservation quantity must be positive.")

        existing = await self._repo.get_reservation(reference, product_id)
        if existing is not None:
            logger.info(
                "reservation_replayed",
                reference=reference,
                product_id=str(product_id),
                status=existing.status.value,
            )
            return existing

        updated = await self._decrement_available(product_id, quantity)
        if updated is None:
            if await self._repo.get(product_id) is None:
                raise NotFoundError("No inventory record for this product.")
            logger.info(
                "reservation_rejected_insufficient_stock",
                product_id=str(product_id),
                requested=quantity,
            )
            raise InsufficientStockError(
                "Not enough stock available for the requested quantity.",
                details={"product_id": str(product_id), "requested": quantity},
            )

        reservation = InventoryReservation(
            product_id=product_id,
            reference=reference,
            quantity=quantity,
            status=ReservationStatus.HELD,
            expires_at=datetime.now(UTC)
            + timedelta(minutes=self._settings.inventory_reservation_ttl_minutes),
        )
        self._repo.add_reservation(reservation)

        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            logger.warning("reservation_race_on_reference", reference=reference)
            raise ConflictError(
                "A reservation for this reference is already being processed.",
                details={"reference": reference},
            ) from exc

        logger.info(
            "stock_reserved",
            product_id=str(product_id),
            quantity=quantity,
            reference=reference,
            strategy=self._settings.inventory_locking_strategy,
            remaining_available=updated.quantity_available,
        )
        return reservation

    async def _decrement_available(self, product_id: uuid.UUID, quantity: int) -> Inventory | None:
        """Dispatch to the configured concurrency strategy.

        Both are correct. The choice is a performance/expressiveness trade-off,
        documented on each repository method, and it is a configuration knob so
        that the behaviour can be compared under load without a code change.
        """
        if self._settings.inventory_locking_strategy == "pessimistic":
            return await self._repo.reserve_pessimistic(product_id, quantity)
        return await self._repo.reserve_atomic(product_id, quantity)

    async def confirm(self, reference: str, product_id: uuid.UUID) -> InventoryReservation:
        """Turn a hold into a sale. The units leave the warehouse."""
        reservation = await self._load_held_reservation(reference, product_id)

        if not await self._repo.confirm(product_id, reservation.quantity):
            raise ConflictError("Reserved quantity is no longer consistent.")

        await self._repo.resolve_reservation(reservation, ReservationStatus.CONFIRMED)
        logger.info(
            "reservation_confirmed",
            reference=reference,
            product_id=str(product_id),
            quantity=reservation.quantity,
        )
        return reservation

    async def release(self, reference: str, product_id: uuid.UUID) -> InventoryReservation:
        """Cancel a hold and put the stock back on sale."""
        reservation = await self._load_held_reservation(reference, product_id)

        if not await self._repo.release(product_id, reservation.quantity):
            raise ConflictError("Reserved quantity is no longer consistent.")

        await self._repo.resolve_reservation(reservation, ReservationStatus.RELEASED)
        logger.info(
            "reservation_released",
            reference=reference,
            product_id=str(product_id),
            quantity=reservation.quantity,
        )
        return reservation

    async def _load_held_reservation(
        self, reference: str, product_id: uuid.UUID
    ) -> InventoryReservation:
        """Load a reservation with a row lock and assert it is still held.

        The lock is what makes confirm and release mutually exclusive: without
        it, a cancellation racing a confirmation could apply both, returning
        the stock *and* shipping it.
        """
        reservation = await self._repo.get_reservation_for_update(reference, product_id)
        if reservation is None:
            raise NotFoundError("Reservation not found.")
        if reservation.status is not ReservationStatus.HELD:
            raise ConflictError(
                f"Reservation is already {reservation.status.value}.",
                details={"status": reservation.status.value},
            )
        return reservation

    async def adjust(
        self, product_id: uuid.UUID, *, delta: int, reason: str, actor_id: uuid.UUID
    ) -> Inventory:
        """Restock or correct stock. Admin-only at the router layer."""
        if delta == 0:
            return await self.get_stock(product_id)

        updated = await self._repo.adjust_available(product_id, delta)
        if updated is None:
            if await self._repo.get(product_id) is None:
                raise NotFoundError("No inventory record for this product.")
            raise InsufficientStockError(
                "Adjustment would drive available stock below zero.",
                details={"product_id": str(product_id), "delta": delta},
            )

        logger.info(
            "stock_adjusted",
            product_id=str(product_id),
            delta=delta,
            reason=reason,
            actor_id=str(actor_id),
            new_available=updated.quantity_available,
        )
        return updated

    async def release_expired_holds(self, *, limit: int = 100) -> int:
        """Return abandoned holds to the sellable pool. Run by a scheduler."""
        expired = await self._repo.list_expired_holds(limit=limit)
        released = 0
        for reservation in expired:
            if await self._repo.release(reservation.product_id, reservation.quantity):
                await self._repo.resolve_reservation(reservation, ReservationStatus.RELEASED)
                released += 1
        if released:
            logger.info("expired_holds_released", count=released)
        return released
