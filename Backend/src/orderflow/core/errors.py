"""Domain error hierarchy and the HTTP translation layer.

Two rules drive this design:

1. **Business code must not import HTTP.** A service raises
   ``InsufficientStockError``; it does not know that this becomes a 409. That
   keeps the domain reusable from a worker or a CLI, not just a web request.
2. **The client never sees internals.** Every unexpected exception becomes a
   generic 500 carrying only a correlation ID. Stack traces, SQL fragments and
   table names are attacker reconnaissance; they go to the logs, not the wire.

Responses follow RFC 9457 (Problem Details for HTTP APIs) so clients get one
predictable error shape across the whole API.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from orderflow.core.logging import get_logger

logger = get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"


class AppError(Exception):
    """Base class for every expected, business-meaningful failure.

    ``message`` is safe to show to the client. ``details`` carries structured,
    also-safe context (e.g. which field conflicted). Anything unsafe belongs in
    the log, never in the exception.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    """The requested resource does not exist (or the caller may not see it)."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found."


class ConflictError(AppError):
    """The request collides with the current state of the resource."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class ValidationError(AppError):
    """Input is syntactically valid but violates a business rule."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "The request payload is invalid."


class AuthenticationError(AppError):
    """Caller could not be identified: missing, malformed or expired token."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    message = "Authentication credentials are missing or invalid."


class AuthorizationError(AppError):
    """Caller is known but not allowed to perform this action."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to perform this action."


class RateLimitError(AppError):
    """Caller exceeded an allowed request budget."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Please slow down."


class ServiceUnavailableError(AppError):
    """A dependency (database, cache, queue) is not answering."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "The service is temporarily unavailable."


def problem_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build one RFC 9457-shaped error body."""
    body: dict[str, Any] = {
        "type": f"https://orderflow.dev/errors/{code}",
        "title": code,
        "status": status_code,
        "detail": message,
    }
    if details:
        body["errors"] = details
    if request_id:
        body["request_id"] = request_id

    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the three handlers that together cover every failure path."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app_error",
            error_code=exc.code,
            status_code=exc.status_code,
            path=request.url.path,
            detail=exc.message,
        )
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
        return problem_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            request_id=_request_id(request),
            details=exc.details or None,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"][1:]) or "body"
            field_errors.setdefault(location, []).append(error["msg"])

        logger.info("request_validation_failed", path=request.url.path, fields=list(field_errors))
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation_error",
            message="The request payload is invalid.",
            request_id=_request_id(request),
            details=field_errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem_response(
            status_code=exc.status_code,
            code=_http_code_slug(exc.status_code),
            message=str(exc.detail),
            request_id=_request_id(request),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            error_type=type(exc).__name__,
        )
        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="An unexpected error occurred.",
            request_id=_request_id(request),
        )


def _http_code_slug(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "payload_too_large",
        429: "rate_limited",
    }.get(status_code, "http_error")
