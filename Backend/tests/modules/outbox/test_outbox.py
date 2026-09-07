"""Outbox and event delivery tests (phases 10 and 11)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Settings
from orderflow.core.database import Database
from orderflow.core.messaging import InMemoryMessagePublisher, MessagePublishError
from orderflow.modules.auth.models import User
from orderflow.modules.inventory.service import InventoryService
from orderflow.modules.orders.schemas import OrderCreate, OrderItemRequest
from orderflow.modules.orders.service import OrderService
from orderflow.modules.outbox.models import OutboxStatus
from orderflow.modules.outbox.repository import OutboxRepository
from orderflow.modules.outbox.service import (
    DirectEventDispatcher,
    OutboxService,
)
from orderflow.modules.products.service import ProductService
from orderflow.shared.events import DomainEvent, EventType
from orderflow.worker.relay import OutboxRelay
from tests.conftest import SHIPPING_ADDRESS, SHIPPING_INPUT

pytestmark = pytest.mark.integration

ORDERS = "/api/v1/orders"


def _event(event_type: EventType = EventType.ORDER_CREATED) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        aggregate_type="order",
        aggregate_id=uuid.uuid4(),
        payload={"order_id": str(uuid.uuid4()), "total_amount": "10.00"},
    )


class TestDomainEvent:
    def test_round_trips_through_the_wire_format(self) -> None:
        original = _event()

        restored = DomainEvent.from_message(original.to_message())

        assert restored.event_id == original.event_id
        assert restored.event_type == original.event_type
        assert restored.payload == original.payload

    def test_every_event_gets_its_own_id(self) -> None:
        """The id is what lets a consumer recognise a redelivery."""
        assert _event().event_id != _event().event_id

    def test_the_message_is_versioned(self) -> None:
        assert _event().to_message()["schema_version"] == 1

    @pytest.mark.parametrize(
        "message",
        [
            {},
            {"event_id": "not-a-uuid"},
            {"event_id": str(uuid.uuid4()), "event_type": "nonsense"},
        ],
    )
    def test_malformed_messages_are_rejected(self, message: dict) -> None:
        with pytest.raises(ValueError, match="Malformed event"):
            DomainEvent.from_message(message)


class TestOutboxStaging:
    async def test_creating_an_order_stages_an_event(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        product = await make_product(sku="OUT-001", stock=10)

        response = await client.post(
            ORDERS,
            headers=auth_headers,
            json={
                "shipping_address": SHIPPING_ADDRESS,
                "items": [{"product_id": str(product.id), "quantity": 1}],
            },
        )

        assert response.status_code == 201
        rows = await OutboxRepository(session).list_for_aggregate(
            "order", uuid.UUID(response.json()["id"])
        )
        assert [row.event_type for row in rows] == [EventType.ORDER_CREATED.value]
        assert rows[0].status is OutboxStatus.PENDING

    async def test_the_event_shares_the_order_transaction(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        """A failed order must leave no event behind, and vice versa.

        This is the property the outbox exists for: one commit covers both.
        """
        product = await make_product(sku="OUT-002", stock=1)

        failed = await client.post(
            ORDERS,
            headers=auth_headers,
            json={
                "shipping_address": SHIPPING_ADDRESS,
                "items": [{"product_id": str(product.id), "quantity": 5}],
            },
        )

        assert failed.status_code == 409
        events = (await session.execute(text("SELECT count(*) FROM outbox_events"))).scalar_one()
        orders = (await session.execute(text("SELECT count(*) FROM orders"))).scalar_one()
        assert (events, orders) == (0, 0)

    async def test_the_lifecycle_stages_one_event_per_transition(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        product = await make_product(sku="OUT-003", stock=10)
        order = (
            await client.post(
                ORDERS,
                headers=auth_headers,
                json={
                    "shipping_address": SHIPPING_ADDRESS,
                    "items": [{"product_id": str(product.id), "quantity": 1}],
                },
            )
        ).json()
        await client.post(f"{ORDERS}/{order['id']}/confirm", headers=admin_headers)
        await client.post(f"{ORDERS}/{order['id']}/cancel", headers=admin_headers, json={})

        rows = await OutboxRepository(session).list_for_aggregate("order", uuid.UUID(order["id"]))

        assert [row.event_type for row in rows] == [
            EventType.ORDER_CREATED.value,
            EventType.ORDER_CONFIRMED.value,
            EventType.ORDER_CANCELLED.value,
        ]

    async def test_the_payload_is_self_contained(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        """A consumer must not have to read the database to act on the event."""
        product = await make_product(sku="OUT-004", stock=10, price="12.50")
        order = (
            await client.post(
                ORDERS,
                headers=auth_headers,
                json={
                    "shipping_address": SHIPPING_ADDRESS,
                    "items": [{"product_id": str(product.id), "quantity": 2}],
                },
            )
        ).json()

        rows = await OutboxRepository(session).list_for_aggregate("order", uuid.UUID(order["id"]))
        payload = rows[0].payload["payload"]

        assert payload["total_amount"] == "25.00"
        assert payload["items"][0]["product_sku"] == "OUT-004"
        assert payload["items"][0]["unit_price"] == "12.50"


class TestDualWriteProblem:
    """Why the outbox exists, demonstrated rather than asserted."""

    async def test_direct_dispatch_loses_the_event_when_the_broker_is_down(
        self,
        database: Database,
        settings: Settings,
        make_user: Callable[..., object],
        make_product: Callable[..., object],
    ) -> None:
        """The order commits, the publish fails, and nothing records the loss.

        Nothing in the system can later discover that this event was never
        emitted. That is the failure mode the outbox removes.
        """
        buyer: User = await make_user(email="dual@example.com")
        product = await make_product(sku="DUAL-001", stock=10)
        broken = InMemoryMessagePublisher()
        broken.fail_next = 1

        async with database.session() as session:
            inventory = InventoryService(session, settings)
            products = ProductService(session, inventory)
            orders = OrderService(session, products, inventory, DirectEventDispatcher(broken))
            customer = await session.get(User, buyer.id)
            assert customer is not None
            order = await orders.create(
                OrderCreate(
                    shipping_address=SHIPPING_INPUT,
                    items=[OrderItemRequest(product_id=product.id, quantity=1)],
                ),
                customer=customer,
            )

        async with database.session() as session:
            stored = (
                await session.execute(
                    text("SELECT count(*) FROM orders WHERE id = :oid"), {"oid": order.id}
                )
            ).scalar_one()
            staged = (
                await session.execute(text("SELECT count(*) FROM outbox_events"))
            ).scalar_one()

        assert stored == 1, "the order survived"
        assert broken.published == [], "the event never reached the broker"
        assert staged == 0, "and there is no record that it should have"

    async def test_the_outbox_keeps_the_event_when_the_broker_is_down(
        self,
        database: Database,
        settings: Settings,
        make_user: Callable[..., object],
        make_product: Callable[..., object],
    ) -> None:
        """Same outage, opposite outcome: the event waits and is delivered later."""
        buyer: User = await make_user(email="dual2@example.com")
        product = await make_product(sku="DUAL-002", stock=10)
        broken = InMemoryMessagePublisher()
        broken.fail_next = 5

        async with database.session() as session:
            inventory = InventoryService(session, settings)
            products = ProductService(session, inventory)
            orders = OrderService(session, products, inventory, OutboxService(session, settings))
            customer = await session.get(User, buyer.id)
            assert customer is not None
            await orders.create(
                OrderCreate(
                    shipping_address=SHIPPING_INPUT,
                    items=[OrderItemRequest(product_id=product.id, quantity=1)],
                ),
                customer=customer,
            )

        relay = OutboxRelay(settings, database, broken)
        published, failed = await relay.run_once()
        assert (published, failed) == (0, 1)

        async with database.session() as session:
            pending = await OutboxRepository(session).count_by_status(OutboxStatus.PENDING)
        assert pending == 1, "the event is still there, waiting"

        broken.fail_next = 0
        async with database.session() as session:
            await session.execute(text("UPDATE outbox_events SET available_at = now()"))

        published, failed = await relay.run_once()
        assert (published, failed) == (1, 0)
        assert len(broken.published) == 1


class TestOutboxRelay:
    async def test_it_publishes_pending_events(
        self, database: Database, settings: Settings
    ) -> None:
        publisher = InMemoryMessagePublisher()
        async with database.session() as session:
            await OutboxService(session, settings).dispatch(_event())

        published, failed = await OutboxRelay(settings, database, publisher).run_once()

        assert (published, failed) == (1, 0)
        assert len(publisher.published) == 1

    async def test_an_empty_outbox_is_a_no_op(self, database: Database, settings: Settings) -> None:
        assert await OutboxRelay(settings, database, InMemoryMessagePublisher()).run_once() == (
            0,
            0,
        )

    async def test_a_published_event_is_not_published_again(
        self, database: Database, settings: Settings
    ) -> None:
        publisher = InMemoryMessagePublisher()
        async with database.session() as session:
            await OutboxService(session, settings).dispatch(_event())
        relay = OutboxRelay(settings, database, publisher)

        await relay.run_once()
        await relay.run_once()

        assert len(publisher.published) == 1

    async def test_failures_back_off_instead_of_retrying_instantly(
        self, database: Database, settings: Settings
    ) -> None:
        """An immediate retry loop would burn the attempt budget in a second."""
        publisher = InMemoryMessagePublisher()
        publisher.fail_next = 1
        async with database.session() as session:
            await OutboxService(session, settings).dispatch(_event())
        relay = OutboxRelay(settings, database, publisher)

        await relay.run_once()

        assert await relay.run_once() == (0, 0)
        async with database.session() as session:
            row = (
                await session.execute(
                    text("SELECT attempts, available_at > now() FROM outbox_events")
                )
            ).one()
        assert row[0] == 1
        assert row[1] is True

    async def test_an_event_is_given_up_on_after_the_attempt_budget(
        self, database: Database, settings: Settings
    ) -> None:
        """A permanently unpublishable event must stop consuming the relay."""
        tuned = settings.model_copy(update={"outbox_max_attempts": 2})
        publisher = InMemoryMessagePublisher()
        publisher.fail_next = 10
        async with database.session() as session:
            await OutboxService(session, tuned).dispatch(_event())
        relay = OutboxRelay(tuned, database, publisher)

        await relay.run_once()
        async with database.session() as session:
            await session.execute(text("UPDATE outbox_events SET available_at = now()"))
        await relay.run_once()

        async with database.session() as session:
            repo = OutboxRepository(session)
            assert await repo.count_by_status(OutboxStatus.FAILED) == 1
            assert await repo.count_by_status(OutboxStatus.PENDING) == 0

    async def test_publish_errors_do_not_stop_the_batch(
        self, database: Database, settings: Settings
    ) -> None:
        publisher = InMemoryMessagePublisher()
        publisher.fail_next = 1
        async with database.session() as session:
            service = OutboxService(session, settings)
            await service.dispatch(_event())
            await service.dispatch(_event())

        published, failed = await OutboxRelay(settings, database, publisher).run_once()

        assert (published, failed) == (1, 1)


class TestConsumerDeduplication:
    async def test_a_slot_can_only_be_claimed_once(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        service = OutboxService(session, settings)
        event_id = uuid.uuid4()

        first = await service.claim_processing_slot(
            event_id=event_id, consumer="notification", event_type="order.created"
        )
        second = await service.claim_processing_slot(
            event_id=event_id, consumer="notification", event_type="order.created"
        )

        assert first is True
        assert second is False, "a redelivery must be recognised and skipped"

    async def test_different_consumers_each_get_a_turn(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        service = OutboxService(session, settings)
        event_id = uuid.uuid4()

        assert await service.claim_processing_slot(
            event_id=event_id, consumer="notification", event_type="order.created"
        )
        assert await service.claim_processing_slot(
            event_id=event_id, consumer="analytics", event_type="order.created"
        )


class TestPublisherDouble:
    async def test_it_records_what_was_published(self) -> None:
        publisher = InMemoryMessagePublisher()

        await publisher.publish(_event())

        assert len(publisher.published) == 1

    async def test_it_can_simulate_an_outage(self) -> None:
        publisher = InMemoryMessagePublisher()
        publisher.fail_next = 1

        with pytest.raises(MessagePublishError):
            await publisher.publish(_event())
