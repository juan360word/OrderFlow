"""HTTP middleware: correlation IDs, access logs, size limits, security headers.

Order matters. Starlette runs middleware in reverse registration order on the
way in, so the first one added is the outermost. RequestContextMiddleware must
be outermost: everything else — including error handlers — needs the request ID.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.status import HTTP_413_CONTENT_TOO_LARGE
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from orderflow.core.errors import problem_response
from orderflow.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_INBOUND_REQUEST_ID_LENGTH = 64


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a correlation ID and emit one structured access log per request.

    An inbound ``X-Request-ID`` is honoured so a trace survives across services,
    but it is sanitised first: it ends up in logs and in a response header, so
    an unvalidated value is a log-injection and header-injection vector.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        request.state.request_id = request_id

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.warning(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request_handled",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=_client_ip(request),
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        return response


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are buffered into memory.

    Written as raw ASGI rather than BaseHTTPMiddleware so we can inspect the
    body stream chunk by chunk. Checking ``Content-Length`` alone is not enough:
    a chunked upload has no such header, so we also count bytes as they arrive
    and abort as soon as the budget is exceeded.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_bytes:
            await self._reject(scope, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message: Message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, counting_receive, send)
        except _BodyTooLargeError:
            await self._reject(scope, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        logger.warning("request_body_too_large", path=scope.get("path"), limit=self.max_body_bytes)
        response = problem_response(
            status_code=HTTP_413_CONTENT_TOO_LARGE,
            code="payload_too_large",
            message=f"Request body exceeds the {self.max_body_bytes} byte limit.",
            request_id=None,
        )
        await response(scope, _empty_receive, send)


class _BodyTooLargeError(Exception):
    """Internal signal; never escapes BodySizeLimitMiddleware."""


async def _empty_receive() -> Message:
    return {"type": "http.disconnect"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the baseline browser hardening headers to every response.

    The API is consumed by a SPA, but responses can still be opened directly in
    a browser, so these are cheap insurance against clickjacking, MIME sniffing
    and referrer leakage.
    """

    def __init__(self, app: ASGIApp, *, enable_hsts: bool = False) -> None:
        super().__init__(app)
        self.enable_hsts = enable_hsts

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Cache-Control", "no-store")
        if self.enable_hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP.

    ``X-Forwarded-For`` is trusted only because we expect to sit behind our own
    load balancer; the header is client-controlled and must never be used for
    an authorization decision — only for logs.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else None


def _sanitize_request_id(raw: str | None) -> str:
    """Keep an inbound trace id only if it is short and alphanumeric."""
    if raw:
        candidate = raw.strip()
        if (
            0 < len(candidate) <= _MAX_INBOUND_REQUEST_ID_LENGTH
            and candidate.replace("-", "").replace("_", "").isalnum()
        ):
            return candidate
    return uuid.uuid4().hex
