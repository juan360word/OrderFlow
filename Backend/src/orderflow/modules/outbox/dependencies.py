"""Dependency wiring for event dispatch."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from orderflow.core.dependencies import DbSession, SettingsDep
from orderflow.core.messaging import MessagePublisher
from orderflow.modules.outbox.service import (
    DirectEventDispatcher,
    EventDispatcher,
    NullEventDispatcher,
    OutboxService,
)


def get_publisher(request: Request) -> MessagePublisher | None:
    return getattr(request.app.state, "publisher", None)


def get_outbox_service(session: DbSession, settings: SettingsDep) -> OutboxService:
    return OutboxService(session, settings)


def get_event_dispatcher(
    session: DbSession,
    settings: SettingsDep,
    publisher: Annotated[MessagePublisher | None, Depends(get_publisher)],
) -> EventDispatcher:
    """Pick the delivery strategy configured for this environment."""
    if not settings.messaging_enabled or publisher is None:
        return NullEventDispatcher()
    if settings.event_delivery_mode == "direct":
        return DirectEventDispatcher(publisher)
    return OutboxService(session, settings)


EventDispatcherDep = Annotated[EventDispatcher, Depends(get_event_dispatcher)]
OutboxServiceDep = Annotated[OutboxService, Depends(get_outbox_service)]
__all__ = ["EventDispatcherDep", "OutboxServiceDep"]
