"""problem+json rendering for every error path (docs/api-conventions.md)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sbam_contracts.api import PROBLEM_JSON, ErrorCode, FieldError, ProblemDetail
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Send

from sbam_api.core.conventions import ApiError
from sbam_api.core.logging import correlation_id_var

_HTTP_CODES = {
    401: (ErrorCode.UNAUTHENTICATED, "Sign in required"),
    403: (ErrorCode.FORBIDDEN, "Forbidden"),
    404: (ErrorCode.NOT_FOUND, "Not found"),
    405: (ErrorCode.VALIDATION_ERROR, "Method not allowed"),
    413: (ErrorCode.PAYLOAD_TOO_LARGE, "Request body too large"),
    415: (ErrorCode.UNSUPPORTED_MEDIA_TYPE, "Unsupported media type"),
    429: (ErrorCode.RATE_LIMITED, "Too many requests"),
    503: (ErrorCode.SERVICE_UNAVAILABLE, "Service unavailable"),
}
_LOC_PREFIXES = {"body", "query", "path", "header", "cookie"}

INTERNAL_ERROR = ApiError(
    500,
    ErrorCode.INTERNAL_ERROR,
    "Internal server error",
    "Something went wrong on our side. Quote the correlation id if you contact support.",
)


def current_correlation_id() -> str:
    return correlation_id_var.get() or "unknown"


def problem_response(error: ApiError) -> JSONResponse:
    problem = error.to_problem(current_correlation_id())
    return JSONResponse(
        problem.model_dump(mode="json"),
        status_code=problem.status,
        media_type=PROBLEM_JSON,
        headers=error.headers or None,
    )


async def send_problem(send: Send, error: ApiError) -> None:
    """Write a problem+json response straight to ASGI `send` (for middleware)."""
    problem: ProblemDetail = error.to_problem(current_correlation_id())
    body = problem.model_dump_json().encode()
    await send(
        {
            "type": "http.response.start",
            "status": problem.status,
            "headers": [
                (b"content-type", PROBLEM_JSON.encode()),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _field_path(loc: tuple[Any, ...]) -> str:
    parts = list(loc[1:] if loc and loc[0] in _LOC_PREFIXES else loc)
    return ".".join(str(p) for p in parts) or str(loc[0] if loc else "body")


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApiError):
        raise exc
    return problem_response(exc)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    errors = [
        FieldError(path=_field_path(tuple(e.get("loc", ()))), message=str(e.get("msg", "invalid")))
        for e in exc.errors()
    ]
    return problem_response(
        ApiError(422, ErrorCode.VALIDATION_ERROR, "Invalid request", errors=errors)
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    code, title = _HTTP_CODES.get(exc.status_code, (ErrorCode.VALIDATION_ERROR, "Bad request"))
    return problem_response(ApiError(exc.status_code, code, title))


# What the server-side timeouts of `db.session.Timeouts` raise (A-30), by SQLSTATE. Anything else is
# a real fault and stays a 500.
_DB_TIMEOUTS = {
    "55P03": ApiError(  # lock_timeout: another request holds the row
        409,
        ErrorCode.CONFLICT,
        "Busy",
        "Another change to this resource is in progress. Try again in a moment.",
        headers={"Retry-After": "1"},
    ),
    "57014": ApiError(  # statement_timeout
        503,
        ErrorCode.SERVICE_UNAVAILABLE,
        "The database is slow",
        "The request took too long and was cancelled; nothing was changed. Try again shortly.",
        headers={"Retry-After": "5"},
    ),
    "25P03": ApiError(  # idle_in_transaction_session_timeout: the lock was released by the server
        503,
        ErrorCode.SERVICE_UNAVAILABLE,
        "The request took too long",
        "The request was cancelled and nothing was changed. Try again shortly.",
        headers={"Retry-After": "5"},
    ),
}


async def _db_error(_: Request, exc: Exception) -> JSONResponse:
    state = getattr(getattr(exc, "orig", None), "sqlstate", None)
    mapped = _DB_TIMEOUTS.get(state or "")
    if mapped is None:
        raise exc  # an unexpected database error: the middleware logs it and answers 500
    return problem_response(mapped)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DBAPIError, _db_error)
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    # Unhandled exceptions are turned into a 500 problem by CorrelationIdMiddleware, so the
    # response still carries the correlation id header.
