"""Pure-ASGI middleware: correlation id + access log + 500 safety net, and request body limit."""

import logging
import re
import time
import traceback
from collections.abc import Mapping
from pathlib import Path

from opentelemetry import trace
from sbam_contracts.api import ErrorCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sbam_api.core.conventions import ApiError
from sbam_api.core.errors import INTERNAL_ERROR, send_problem
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var

CORRELATION_HEADER = b"x-correlation-id"
_VALID_CORRELATION_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
log = logging.getLogger("sbam_api.request")


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
        cid = raw if _VALID_CORRELATION_ID.fullmatch(raw) else str(uuid7())
        token = correlation_id_var.set(cid)
        trace.get_current_span().set_attribute("sbam.correlation_id", cid)  # no-op without a span
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
    """413 problem+json when Content-Length, or the streamed body, exceeds `max_bytes` (or the
    path's own limit in `per_path`, e.g. the 15 MB masters workbook import)."""

    def __init__(
        self, app: ASGIApp, max_bytes: int, per_path: Mapping[str, int] | None = None
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.per_path = dict(per_path or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        max_bytes = self.per_path.get(scope["path"], self.max_bytes)
        too_large = ApiError(
            413,
            ErrorCode.PAYLOAD_TOO_LARGE,
            "Request body too large",
            f"The limit is {max_bytes} bytes.",
        )
        length = _header(scope, b"content-length")
        if length.isdigit() and int(length) > max_bytes:
            await send_problem(send, too_large)
            return

        received, started, exceeded, answered = 0, False, False, False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    exceeded = True
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started, answered
            if exceeded and not started:
                # The handler's own body parser catches the error above and answers 400 (A-19):
                # the client is told 413, whatever the app made of the cut-off body.
                if not answered:
                    answered = True
                    await send_problem(send, too_large)
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            if started:
                raise
            if not answered:
                await send_problem(send, too_large)
        else:
            if exceeded and not started and not answered:  # the app swallowed it and sent nothing
                await send_problem(send, too_large)
