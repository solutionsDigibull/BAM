"""#420: Cognee's model calls (LiteLLM inside worker-knowledge) are counted by the LLM budgets.

The meter is exercised through real LiteLLM (`mock_response` makes no network call but still runs
the success callbacks), so the callback signature, the token fields and the context-variable
hand-over are the ones Cognee would meet.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Coroutine, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import litellm
import pytest
from cognee_fake import FakeCognee
from llm_db import PROJECT, TENANT, llm_sessions, scope, settings
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.llm.models import LlmCall
from sbam_api.llm.repository import LlmCallRepository
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.knowledge import ItemSource, KnowledgeItem, ProjectScope
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter
from sbam_workers.llm import meter
from sbam_workers.llm.budget import BudgetExceeded
from sbam_workers.llm.store import SqlCallStore
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_cognee_adapter import SECRET, FakeGuard

OTHER_PROJECT = uuid.uuid4()
CHAT = {"model": "openai/sbam-knowledge", "api_base": "http://bifrost/v1", "api_key": "k"}
EMBED = {"model": "openai/sbam-embed", "api_base": "http://bifrost/v1", "api_key": "k"}


class World:
    def __init__(self, **over: Any) -> None:
        self.sessions: sessionmaker[Session] = llm_sessions()
        self.store = SqlCallStore(self.sessions)
        self.settings = settings(**over)
        self.budget = meter.install(self.store, self.settings)

    def rows(self) -> list[LlmCall]:
        with self.sessions() as session:
            rows = list(session.scalars(select(LlmCall).order_by(LlmCall.created_at)))
            session.expunge_all()
            return rows

    async def settled(self, count: int, timeout: float = 10.0) -> list[LlmCall]:
        """LiteLLM runs success callbacks as background tasks of the same loop (the worker's loop
        lives on; `asyncio.run` would end it first): wait, in the loop, until `count` rows exist."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self.rows()
            if len(rows) >= count:
                return rows
            await asyncio.sleep(0.05)
        return self.rows()


@pytest.fixture
def world() -> Iterator[Callable[..., World]]:
    created: list[World] = []

    def make(**over: Any) -> World:
        litellm.logging_callback_manager._reset_all_callbacks()  # type: ignore[no-untyped-call]  # a clean process
        meter._installed = False
        created.append(World(**over))
        return created[-1]

    yield make
    litellm.logging_callback_manager._reset_all_callbacks()  # type: ignore[no-untyped-call]
    meter._installed = False


async def with_rows(w: World, count: int, call: Coroutine[Any, Any, None]) -> list[LlmCall]:
    await call
    return await w.settled(count)


async def chat(
    tenant: uuid.UUID = TENANT, project: uuid.UUID = PROJECT, what: str = "cognify"
) -> None:
    with meter.metered(tenant, project, what):
        await litellm.acompletion(
            messages=[{"role": "user", "content": "hi"}], mock_response="ok", **CHAT
        )


async def embed(tenant: uuid.UUID = TENANT, project: uuid.UUID = PROJECT) -> None:
    with meter.metered(tenant, project, "search"):
        await litellm.aembedding(input=["alpha beta"], mock_response=[0.1, 0.2], **EMBED)


def test_420_t11_a_cognee_chat_call_is_recorded_for_the_project_that_asked(
    world: Callable[..., World],
) -> None:
    w = world()
    [row] = asyncio.run(with_rows(w, 1, chat()))
    assert (row.alias, row.prompt_id, row.status) == (
        "sbam-knowledge",
        "cognee.acompletion",
        "SUCCEEDED",
    )
    assert (row.tenant_id, row.project_id) == (TENANT, PROJECT)
    assert row.tokens_in > 0 and row.tokens_out > 0 and row.output is None
    assert row.context_manifest == {"cognee": {"call_type": "acompletion"}}


def test_420_t11_an_embedding_call_is_recorded_too(world: Callable[..., World]) -> None:
    w = world()
    [row] = asyncio.run(with_rows(w, 1, embed()))
    assert (row.alias, row.prompt_id) == ("sbam-embed", "cognee.aembedding")
    assert row.tokens_in > 0 and row.tokens_out == 0


def test_420_t12_concurrent_projects_are_not_mixed_up(world: Callable[..., World]) -> None:
    w = world()

    async def both() -> None:
        await asyncio.gather(*(chat(TENANT, p) for p in (PROJECT, OTHER_PROJECT) * 3))

    rows = asyncio.run(with_rows(w, 6, both()))
    assert sorted(str(r.project_id) for r in rows) == sorted(
        [str(PROJECT)] * 3 + [str(OTHER_PROJECT)] * 3
    )


def test_420_t13_a_call_outside_a_metered_block_is_not_attributed_to_anyone(
    world: Callable[..., World], caplog: pytest.LogCaptureFixture
) -> None:
    w = world()

    async def stray() -> None:
        await litellm.acompletion(
            messages=[{"role": "user", "content": "hi"}], mock_response="ok", **CHAT
        )
        await asyncio.sleep(0.5)

    with caplog.at_level(logging.WARNING, logger="sbam_workers.llm.meter"):
        asyncio.run(stray())
    assert w.rows() == [] and "outside a metered block" in caplog.text


def test_420_t14_a_metered_block_ends_with_its_scope() -> None:
    assert meter._scope.get() is None
    with meter.metered(TENANT, PROJECT, "x"):
        got = meter._scope.get()
        assert got is not None and (got.tenant_id, got.project_id) == (TENANT, PROJECT)
    assert meter._scope.get() is None


def test_420_t15_cognee_spend_counts_toward_the_budget_once_a_price_is_set(
    world: Callable[..., World],
) -> None:
    # the mock call is 10 prompt + 20 completion tokens; 1 USD per token makes 30 USD per call
    prices = {"sbam-knowledge": {"in": 1_000_000, "out": 1_000_000}}
    w = world(llm_project_budget_usd="50", llm_price_usd_per_mtok=prices)
    [row] = asyncio.run(with_rows(w, 1, chat()))
    assert row.cost_usd == Decimal("30")
    ctx = TenantContext(TENANT, PROJECT)
    with w.sessions() as session:
        assert LlmCallRepository(session, ctx).spent() == Decimal("30")
    asyncio.run(with_rows(w, 2, chat()))
    with pytest.raises(BudgetExceeded, match="project has spent"):
        asyncio.run(w.budget.check(scope(), "sbam-sow"))  # the next wrapper call is the one refused


def test_420_t16_a_budget_with_no_price_for_cognee_says_so_instead_of_counting_it_free(
    world: Callable[..., World], caplog: pytest.LogCaptureFixture
) -> None:
    w = world(llm_project_budget_usd="50")
    with caplog.at_level(logging.ERROR, logger="sbam_workers.llm"):
        asyncio.run(with_rows(w, 1, chat()))
    assert "no price is configured" in caplog.text and "sbam-knowledge" in w.budget.blind
    assert w.rows()[0].cost_usd == 0  # recorded, honestly: the cost is unknown, not invented


def test_420_t17_a_failing_database_never_breaks_the_cognee_call(
    world: Callable[..., World], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    w = world()

    def boom(*args: Any, **kwargs: Any) -> uuid.UUID:
        raise RuntimeError("db down")

    monkeypatch.setattr(w.store, "add", boom)

    async def scenario() -> None:
        await chat()  # returns normally
        deadline = time.monotonic() + 5
        while "could not record" not in caplog.text and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    with caplog.at_level(logging.ERROR, logger="sbam_workers.llm.meter"):
        asyncio.run(scenario())
    assert "could not record a Cognee model call" in caplog.text


# --- the adapter opens the metered block around each Cognee call -------------------------


class Spy(FakeCognee):
    def __init__(self) -> None:
        super().__init__()
        self.seen: dict[str, tuple[uuid.UUID, uuid.UUID] | None] = {}

    def _watch(self, name: str) -> None:
        s = meter._scope.get()
        self.seen[name] = None if s is None else (s.tenant_id, s.project_id)

    async def cognify(self, who: Any, dataset: str) -> None:
        self._watch("cognify")
        await super().cognify(who, dataset)

    async def search(self, who: Any, dataset: str, query: str, top_k: int) -> Any:
        self._watch("search")
        return await super().search(who, dataset, query, top_k)

    async def add(self, who: Any, dataset: str, name: str, data: bytes) -> None:
        self._watch("add")
        await super().add(who, dataset, name, data)


def test_420_t18_cognify_search_and_add_run_inside_the_projects_metered_block(
    tmp_path: Path,
) -> None:
    tenant, project = uuid7(), uuid7()
    storage = LocalDiskBackend(tmp_path)
    spy = Spy()
    adapter = CogneeAdapter(spy, FakeGuard({project: tenant}, set()), storage, SECRET)
    sc = ProjectScope(tenant_id=tenant, project_id=project)
    obj = storage.put(b"text", StorageScope(tenant, project), Kind.DERIVED, "x")
    item = KnowledgeItem(
        source=ItemSource.MASTERS,
        source_id=uuid.uuid4(),
        sha256=obj.sha256,
        storage_key=obj.storage_key,
    )

    async def go() -> None:
        await adapter.add(sc, [item])
        await adapter.cognify(sc)
        await adapter.search(sc, "q", 3)

    asyncio.run(go())
    assert spy.seen == {
        "add": (tenant, project),
        "cognify": (tenant, project),
        "search": (tenant, project),
    }
    assert meter._scope.get() is None  # nothing leaks past the call
