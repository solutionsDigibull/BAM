"""Test helpers for E6.1: SQLite with the real `llm_calls` table, and a fake Bifrost over httpx."""

import json
import uuid
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from pipeline_db import pipeline_sessions
from pydantic import BaseModel, ConfigDict
from sbam_api.db.repository import TenantContext
from sbam_api.llm.models import LlmCall, LlmDerivative
from sbam_api.llm.repository import LlmCallRepository
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.settings import WorkerSettings
from sqlalchemy.orm import Session, sessionmaker

TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()


class SmokeAnswer(BaseModel):
    """The output type of the recorded mock answer (tests/llm-mock/sbam-sow/e6_1_wrapper.json)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    count: int
    label: str


SMOKE_CONTEXT = {"rfq": "Quote 12 cables"}


def llm_sessions() -> sessionmaker[Session]:
    sessions = pipeline_sessions()
    LlmCall.metadata.create_all(
        sessions.kw["bind"], tables=[LlmCall.__table__, LlmDerivative.__table__]
    )  # type: ignore[list-item]
    return sessions


def settings(**over: Any) -> WorkerSettings:
    base: dict[str, Any] = {
        "temporal_host": "temporal:7233",
        "database_url": "sqlite://",
        "llm_backoff_seconds": 0,
    }
    return WorkerSettings(_env_file=None, **{**base, **over})  # type: ignore[call-arg]


def scope(**over: Any) -> LlmScope:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "project_id": PROJECT,
        "correlation_id": "corr-1",
        "temporal_workflow_id": f"wf-{uuid.uuid4()}",
        "activity_id": "act-1",
        "attempt": 1,
    }
    return LlmScope(**{**values, **over})


def completion(content: str, model: str = "sbam-sow-primary", **usage: Any) -> dict[str, Any]:
    return {
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, **usage},
    }


class FakeBifrost:
    """An httpx transport that answers from a list (one entry per request, the last one repeats)."""

    def __init__(self, replies: Sequence[httpx.Response | Exception | dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            return httpx.Response(200, json=reply)
        return reply

    @property
    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def make_client(
    bifrost: FakeBifrost,
    registry: PromptRegistry | None = None,
    sessions: sessionmaker[Session] | None = None,
    tracer: Callable[[WorkerSettings], LangfuseTracer] | None = None,
    **over: Any,
) -> tuple[LlmClient, sessionmaker[Session]]:
    sessions = sessions or llm_sessions()
    conf = settings(**over)
    client = LlmClient(
        conf,
        registry or PromptRegistry(),
        SqlCallStore(sessions),
        tracer(conf) if tracer else LangfuseTracer(conf),
        bifrost.client,
    )
    return client, sessions


def rows(sessions: sessionmaker[Session], sc: LlmScope) -> list[LlmCall]:
    with sessions() as session:
        found = LlmCallRepository(session, TenantContext(sc.tenant_id, sc.project_id)).for_activity(
            sc.temporal_workflow_id, sc.activity_id
        )
        session.expunge_all()
        return found
