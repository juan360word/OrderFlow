"""Order business rules.

Placing an order touches three modules and several tables at once. Every write
here shares the request's single transaction, so the order, its lines and the
stock reservations either all commit or none of them do.

Module boundaries: this service calls ``ProductService`` and
``InventoryService``. It never reaches into their repositories or tables, and
neither of them knows that orders exist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from orderflow.core.logging import get_logger
from orderflow.modules.auth.models import ROLE_ADMIN, User
from orderflow.modules.inventory.service import InventoryService
from orderflow.modules.orders.models import Order, OrderItem
from orderflow.modules.orders.repository import OrderRepository
from orderflow.modules.orders.schemas import OrderCreate, OrderFilters
from orderflow.modules.orders.state_machine import OrderStatus, assert_can_transition
from orderflow.modules.products.models import Product
from orderflow.modules.products.service import ProductService
from orderflow.shared.pagination import PageParams

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PricedLine:
    """A requested line resolved against the live catalogue."""

    product: Product
    quantity: int

    @property
    def subtotal(self) -> Decimal:
        return self.product.price * self.quantity


class OrderService:
    """Create, read and transition orders."""

    def __init__(
        self,
        session: AsyncSession,
        products: ProductService,
        inventory: InventoryService,
    ) -> None:
        self._session = session
        self._repo = OrderRepository(session)
        self._products = products
        self._inventory = inventory

    async def create(self, payload: OrderCreate, *, customer: User) -> Order:
        """Place an order: price it, reserve the stock, persist it.

        All of it inside one transaction. If the last line has no stock, the
        reservations already taken by the earlier lines are rolled back too —
        there is no state in which a customer holds stock for an order that was
        never created.
        """
        lines = await self._resolve_lines(payload)
        currency = self._single_currency(lines)
        total = sum((line.subtotal for line in lines), start=Decimal("0.00"))

        order = Order(
            user_id=customer.id,
            status=OrderStatus.PENDING,
            total_amount=total,
            currency=currency,
        )
        self._repo.add(order)
        await self._session.flush()

        await self._reserve_stock(order, lines)

        for line in lines:
            self._session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=line.product.id,
                    product_sku=line.product.sku,
                    product_name=line.product.name,
                    quantity=line.quantity,
                    unit_price=line.product.price,
                )
            )

        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("The order could not be stored.") from exc

        await self._session.refresh(order, attribute_names=["items"])
        logger.info(
            "order_created",
            order_id=str(order.id),
            user_id=str(customer.id),
            item_count=len(lines),
            total_amount=str(total),
            currency=currency,
        )
        return order

    async def _resolve_lines(self, payload: OrderCreate) -> list[PricedLine]:
        """Turn requested product ids into priced lines, or fail loudly."""
        requested_ids = [item.product_id for item in payload.items]
        catalogue = await self._products.get_many_active(requested_ids)

        missing = [str(pid) for pid in requested_ids if pid not in catalogue]
        if missing:
            raise NotFoundError(
                "One or more products are unavailable.",
                details={"unavailable_product_ids": missing},
            )

        return [
            PricedLine(product=catalogue[item.product_id], quantity=item.quantity)
            for item in payload.items
        ]

    @staticmethod
    def _single_currency(lines: list[PricedLine]) -> str:
        """Reject a basket that mixes currencies: its total would be meaningless."""
        currencies = {line.product.currency for line in lines}
        if len(currencies) > 1:
            raise ValidationError(
                "An order cannot mix currencies.",
                details={"currencies": sorted(currencies)},
            )
        return currencies.pop()

    async def _reserve_stock(self, order: Order, lines: list[PricedLine]) -> None:
        """Hold stock for every line, always in the same global order.

        Sorting by product id is what prevents deadlock. Two concurrent orders
        for the same two products in opposite sequence would each hold the row
        the other needs; taking the locks in one agreed order makes that cycle
        impossible.
        """
        for line in sorted(lines, key=lambda entry: entry.product.id.bytes):
            await self._inventory.reserve(
                line.product.id,
                quantity=line.quantity,
                reference=order.reservation_reference,
            )

    async def get(self, order_id: uuid.UUID, *, actor: User) -> Order:
        """Fetch one order the caller is allowed to see.

        Ownership is a WHERE clause, not a check after the fact, and a
        non-owner gets 404 rather than 403: a 403 would confirm that the order
        exists, which is the leak an IDOR probe is looking for.
        """
        order = await self._repo.get_by_id(order_id, owner_id=self._visibility_scope(actor))
        if order is None:
            raise NotFoundError("Order not found.")
        return order

    async def list_orders(
        self, filters: OrderFilters, page: PageParams, *, actor: User
    ) -> tuple[list[Order], int]:
        """List the caller's orders. Admins see every order."""
        return await self._repo.list_orders(filters, page, owner_id=self._visibility_scope(actor))

    async def confirm(self, order_id: uuid.UUID, *, actor: User) -> Order:
        """Move a pending order to confirmed and consume its held stock.

        Admin-only because in a real system this is the fulfilment step, driven
        by a payment provider callback rather than by the customer.
        """
        order = await self._load_for_transition(order_id, actor=actor, admin_only=True)
        assert_can_transition(order.status, OrderStatus.CONFIRMED)

        for item in self._locking_order(order):
            await self._inventory.confirm(order.reservation_reference, item.product_id)

        order.status = OrderStatus.CONFIRMED
        order.confirmed_at = datetime.now(UTC)
        await self._session.flush()

        logger.info("order_confirmed", order_id=str(order.id), actor_id=str(actor.id))
        return order

    async def cancel(self, order_id: uuid.UUID, *, actor: User, reason: str | None = None) -> Order:
        """Cancel an order and return its stock to the sellable pool.

        How the stock comes back depends on where the order was:

        * ``pending``   — the hold is released; the units never left.
        * ``confirmed`` — the hold was already consumed, so the units are put
          back with a compensating restock. That is a different business event,
          closer to a return than to a cancellation, which is why only an admin
          may do it.
        """
        order = await self._load_for_transition(order_id, actor=actor, admin_only=False)
        previous_status = order.status
        assert_can_transition(previous_status, OrderStatus.CANCELLED)

        if previous_status is OrderStatus.CONFIRMED and not self._is_admin(actor):
            raise AuthorizationError("A confirmed order can only be cancelled by an administrator.")

        for item in self._locking_order(order):
            if previous_status is OrderStatus.PENDING:
                await self._inventory.release(order.reservation_reference, item.product_id)
            else:
                await self._inventory.adjust(
                    item.product_id,
                    delta=item.quantity,
                    reason=f"restock after cancelling order {order.id}",
                    actor_id=actor.id,
                )

        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.now(UTC)
        order.cancellation_reason = reason
        await self._session.flush()

        logger.info(
            "order_cancelled",
            order_id=str(order.id),
            previous_status=previous_status.value,
            actor_id=str(actor.id),
        )
        return order

    async def _load_for_transition(
        self, order_id: uuid.UUID, *, actor: User, admin_only: bool
    ) -> Order:
        """Load an order with its row locked, so two transitions cannot both win.

        Without ``FOR UPDATE`` a cancel racing a confirm would both read
        ``pending``, both pass the state check, and both apply — releasing the
        stock and shipping it.
        """
        if admin_only and not self._is_admin(actor):
            raise AuthorizationError()

        order = await self._repo.get_for_update(order_id, owner_id=self._visibility_scope(actor))
        if order is None:
            raise NotFoundError("Order not found.")
        return order

    @staticmethod
    def _locking_order(order: Order) -> list[OrderItem]:
        return sorted(order.items, key=lambda item: item.product_id.bytes)

    @staticmethod
    def _is_admin(actor: User) -> bool:
        return actor.role_name == ROLE_ADMIN

    def _visibility_scope(self, actor: User) -> uuid.UUID | None:
        """``None`` means "every order"; otherwise the query is pinned to the owner."""
        return None if self._is_admin(actor) else actor.id
