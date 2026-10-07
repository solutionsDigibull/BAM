"""Pure-ASGI middleware: correlation id + access log + 500 safety net, and request body limit."""

import logging
import re
import secrets
import time
import traceback
import uuid
from pathlib import Path

from sbam_contracts.api import ErrorCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sbam_api.core.conventions import ApiError
from sbam_api.core.errors import INTERNAL_ERROR, send_problem
from sbam_api.core.logging import correlation_id_var

CORRELATION_HEADER = b"x-correlation-id"
_VALID_CORRELATION_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
log = logging.getLogger("sbam_api.request")


def uuid7() -> str:
    """RFC 9562 UUIDv7 (time-ordered). Python 3.12 has no uuid.uuid7."""
    ms = time.time_ns() // 1_000_000
    rand = secrets.randbits(74)
    value = (
        (ms << 80) | (0x7 << 76) | ((rand >> 62) << 64) | (0b10 << 62) | (rand & ((1 << 62) - 1))
    )
    return str(uuid.UUID(int=value))


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return bytes(value).decode("latin-1")
    return ""


def _where(exc: BaseException) -> str:
    """File:line of the raising frame. No message, no source text: those can hold secrets."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    last = frames[-1]
    return f"{Path(last.filename).name}:{last.lineno}"


class CorrelationIdMiddleware:
    """Correlation id, access log and 500 safety net.

    Accepts a safe X-Correlation-ID or mints a UUIDv7 and echoes it, logs one access line per
    request, and turns any unhandled exception into a generic 500 problem+json.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = _header(scope, CORRELATION_HEADER)
        cid = raw if _VALID_CORRELATION_ID.fullmatch(raw) else uuid7()
        token = correlation_id_var.set(cid)
        started, status = False, 500
        begin = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started, status = True, message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (CORRELATION_HEADER, cid.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            log.error(
                "unhandled exception",
                extra={"exc_type": type(exc).__name__, "exc_at": _where(exc)},
            )
            if started:
                raise
            await send_problem(send_with_id, INTERNAL_ERROR)
        finally:
            route = scope.get("route")
            log.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "route": getattr(route, "path", scope.get("path")),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - begin) * 1000, 1),
                },
            )
            correlation_id_var.reset(token)


class _BodyTooLargeError(Exception):
    pass


class BodyLimitMiddleware:
    """413 problem+json when Content-Length, or the streamed body, exceeds `max_bytes`."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        too_large = ApiError(
            413,
            ErrorCode.PAYLOAD_TOO_LARGE,
            "Request body too large",
            f"The limit is {self.max_bytes} bytes.",
        )
        length = _header(scope, b"content-length")
        if length.isdigit() and int(length) > self.max_bytes:
            await send_problem(send, too_large)
            return

        received, started = 0, False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            if started:
                raise
            await send_problem(send, too_large)
