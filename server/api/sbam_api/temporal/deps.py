"""Request dependencies for the run endpoints. Each is built once per app, on first use.

Who may call is decided by the authorization chain (`sbam_api.core.auth`, #48), not here.
"""

import asyncio

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.core.settings import Settings
from sbam_api.db.session import API_TIMEOUTS, make_engine, make_sessionmaker
from sbam_api.temporal.client import OrchestratorUnavailable, connect

_lock = asyncio.Lock()


def session_factory(request: Request) -> sessionmaker[Session]:
    state = request.app.state
    if getattr(state, "sessions", None) is None:
        state.sessions = make_sessionmaker(
            make_engine(state.settings.database_url, timeouts=API_TIMEOUTS)
        )
    sessions: sessionmaker[Session] = state.sessions
    return sessions


async def temporal_client(request: Request) -> Client:
    """The app's Temporal client, connected on first use. Unreachable -> 503 (retried next call)."""
    state = request.app.state
    if getattr(state, "temporal", None) is None:
        async with _lock:
            if getattr(state, "temporal", None) is None:
                settings: Settings = state.settings
                try:
                    state.temporal = await connect(
                        settings.temporal_host, settings.temporal_namespace
                    )
                except RuntimeError:
                    raise OrchestratorUnavailable() from None
    client: Client = state.temporal
    return client
