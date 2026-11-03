"""One database session per request (audit A-12).

A request used to open three sessions before its handler did anything: the membership lookup, the
users-mirror sync and the handler's own. `RequestSessionMiddleware` gives each HTTP request a
holder; the first code that needs a session creates it from the app's sessionmaker, and everything
else in the request gets the same one. The middleware closes it when the request ends (after the
response and any cleanup), so a handler that raises still gives its connection back.

Outside a request (workers, scripts, tests that call a function directly) there is no holder, and
`request_scoped` opens and closes a session of its own, exactly as before.

A session is not thread-safe, but a request uses it one step at a time: FastAPI resolves sync
dependencies in order, each in a worker thread that sees the same holder (anyio copies the context).
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import anyio.to_thread
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestSession:
    """The session of one request, created on first use."""

    def __init__(self) -> None:
        self._session: Session | None = None
        self._lock = threading.Lock()

    def get(self, factory: sessionmaker[Session]) -> Session:
        with self._lock:
            if self._session is None:
                self._session = factory()
            return self._session

    def close(self) -> None:
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            session.close()


_current: ContextVar[RequestSession | None] = ContextVar("request_session", default=None)


@contextmanager
def request_scoped(factory: sessionmaker[Session], *, settle: bool = True) -> Iterator[Session]:
    """The request's session (or a private one outside a request).

    `settle=True` (the auth path) ends any transaction the block left open when it exits, so the
    next user of the shared session starts clean: a handler that uses `with session.begin()` must
    not find a read transaction from the membership lookup still open. The handler's own use
    (`settle=False`) leaves the transaction to the handler and to the end of the request.
    """
    holder = _current.get()
    if holder is None:
        with factory() as session:
            yield session
        return
    session = holder.get(factory)
    try:
        yield session
    finally:
        if settle and session.in_transaction():
            session.rollback()


class RequestSessionMiddleware:
    """Pure ASGI: opens the request's holder and closes its session when the request is over."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        holder = RequestSession()
        token = _current.set(holder)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
            await anyio.to_thread.run_sync(holder.close)
