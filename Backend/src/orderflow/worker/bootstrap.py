"""Create the SQS queues. Idempotent, safe to run on every boot."""

from __future__ import annotations

import asyncio

from orderflow.core.config import get_settings
from orderflow.core.logging import configure_logging, get_logger
from orderflow.core.messaging import ensure_queues

logger = get_logger(__name__)


async def main() -> None:  # pragma: no cover - operational entry point
    settings = get_settings()
    configure_logging(debug=settings.debug)
    urls = await ensure_queues(settings)
    logger.info("queues_bootstrapped", **urls)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
