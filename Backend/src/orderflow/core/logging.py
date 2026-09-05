"""Structured logging.

Why structlog and not the stdlib logger alone: in production, logs are parsed
by machines (CloudWatch, Loki, Datadog). A line like
``"order 5f3 failed for user bob"`` is unqueryable; ``{"event": "order_failed",
"order_id": "5f3", "user_id": "..."}`` is. structlog also gives us
``contextvars``, which is how a request ID reaches every log line emitted deep
inside a service without threading it through every function signature.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# Keys that must never reach a log sink. Logs are copied to third parties and
# retained for months; a password or token in one is a breach.
_REDACTED_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "secret",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "set-cookie",
        "cookie",
    }
)
_REDACTED_PLACEHOLDER = "[redacted]"


def _redact_sensitive(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """Scrub known-sensitive keys before rendering.

    Defence in depth: call sites should not log secrets in the first place,
    but a single careless ``logger.info("login", **payload)`` should not be
    able to leak a credential.
    """
    for key in list(event_dict):
        if key.lower() in _REDACTED_KEYS:
            event_dict[key] = _REDACTED_PLACEHOLDER
    return event_dict


def configure_logging(*, debug: bool = False, level: int = logging.INFO) -> None:
    """Install the logging pipeline. Call once, at application startup.

    ``debug=True`` renders coloured, human-readable lines for a terminal;
    otherwise we emit one JSON object per line, which is what log shippers
    expect.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # pulls in request_id, user_id...
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _redact_sensitive,
    ]

    renderer: Processor
    if debug:
        renderer = structlog.dev.ConsoleRenderer(colors=True)
        shared_processors.append(structlog.dev.set_exc_info)
    else:
        renderer = structlog.processors.JSONRenderer()
        # format_exc_info turns exc_info into a string field; the console
        # renderer prints tracebacks itself, so it is only needed for JSON.
        shared_processors.append(structlog.processors.format_exc_info)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, alembic) through the same
    # pipeline so the whole process emits one consistent format.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Module-level logger accessor, typed for mypy --strict."""
    return structlog.stdlib.get_logger(name)
