"""#777 (WG-24): a Cognee build ends at once on a provider credit refusal or a reached token cap.

Nothing here calls a model. The refusal is a LiteLLM call whose mock answer is Bifrost's
`502 upstream_credentials_exhausted` (LiteLLM raises it and runs its failure callbacks, like a real
refused call); the cap is the recorded `llm_calls` tokens against a small `BudgetCaps`.
"""

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import litellm
import pytest
from cognee_fake import FakeCognee
from litellm.exceptions import BadGatewayError
from llm_db import PROJECT, TENANT, llm_sessions, settings
from sbam_api.core.ids import uuid7
from sbam_api.llm.models import LlmCall
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.budget import BudgetCaps
from sbam_contracts.knowledge import ProjectScope
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter, CogneeUnavailable
from sbam_workers.llm import meter
from sbam_workers.llm.meter import CogneeStopped, StopCause
from sbam_workers.llm.store import SqlCallStore
from sqlalchemy import func, select
from test_cognee_adapter import SECRET, FakeGuard

CHAT = {"model": "openai/sbam-knowledge", "api_base": "http://bifrost/v1", "api_key": "k"}
REFUSAL = (
    '{"error": {"message": "all provider keys refused", "type": "server_error", '
    '"code": "upstream_credentials_exhausted"}}'
)


class World:
    def __init__(self, caps: BudgetCaps | None = None) -> None:
        self.sessions = llm_sessions()
        self.store = SqlCallStore(self.sessions)
        self.budget = meter.install(self.store, settings())
        if caps is not None:
            self.budget.caps = caps


@pytest.fixture
def world() -> Iterator[Callable[..., World]]:
    def make(caps: BudgetCaps | None = None) -> World:
        litellm.logging_callback_manager._reset_all_callbacks()  # type: ignore[no-untyped-call]
        meter._installed = False
        return World(caps)

    yield make
    litellm.logging_callback_manager._reset_all_callbacks()  # type: ignore[no-untyped-call]
    meter._installed = False
    meter._budget = None


def refused() -> BadGatewayError:
    return BadGatewayError(message=REFUSAL, model="sbam-knowledge", llm_provider="openai")


async def one_call(**extra: Any) -> None:
    await litellm.acompletion(messages=[{"role": "user", "content": "hi"}], **CHAT, **extra)


class Cognify(FakeCognee):
    """A `cognify` that makes `calls` model calls through LiteLLM, like Cognee's extraction."""

    def __init__(self, calls: int, refuse_at: int | None = None) -> None:
        super().__init__()
        self.n_calls, self.refuse_at, self.made = calls, refuse_at, 0
        self.finished = False

    async def cognify(self, who: Any, dataset: str) -> None:
        await super().cognify(who, dataset)
        for i in range(self.n_calls):
            if i == self.refuse_at:
                # LiteLLM would retry this for about an hour (num_retries, backoff)
                await one_call(mock_response=refused(), num_retries=1000)
            else:
                await one_call(mock_response="ok")
            self.made += 1
        self.finished = True


def adapter_for(backend: FakeCognee, tmp_path: Path) -> tuple[CogneeAdapter, ProjectScope]:
    tenant, project = uuid7(), uuid7()
    guard = FakeGuard({project: tenant}, set())
    adapter = CogneeAdapter(backend, guard, LocalDiskBackend(tmp_path), SECRET)
    sc = ProjectScope(tenant_id=tenant, project_id=project)
    return adapter, sc


async def with_dataset(adapter: CogneeAdapter, sc: ProjectScope) -> None:
    await adapter.ensure_dataset(sc)


def test_wg24_t01_a_credit_refusal_ends_the_cognify_at_once_with_the_named_cause(
    world: Callable[..., World], tmp_path: Path
) -> None:
    world()
    backend = Cognify(calls=5, refuse_at=1)
    adapter, sc = adapter_for(backend, tmp_path)

    async def go() -> float:
        await with_dataset(adapter, sc)
        started = time.monotonic()
        with pytest.raises(CogneeStopped) as info:
            await adapter.cognify(sc)
        assert info.value.cause is StopCause.CREDIT_EXHAUSTED
        assert "out of credit" in str(info.value)
        return time.monotonic() - started

    elapsed = asyncio.run(go())
    assert elapsed < 8  # the first refusal, not the activity timeout (or 1000 retries)
    assert backend.made == 1 and not backend.finished  # nothing after the refusal ran


def test_wg24_t01_a_refusal_that_reaches_the_adapter_as_an_error_is_the_same_cause(
    tmp_path: Path,
) -> None:
    """Cognee may surface the 502 itself (no callback ran): it must not become the retryable
    `CogneeUnavailable` the 502 status would otherwise map to."""
    backend = FakeCognee()
    backend.fail = refused()
    adapter, sc = adapter_for(backend, tmp_path)

    async def go() -> None:
        backend.fail = None
        await adapter.ensure_dataset(sc)
        backend.fail = refused()
        with pytest.raises(CogneeStopped) as info:
            await adapter.cognify(sc)
        assert info.value.cause is StopCause.CREDIT_EXHAUSTED

    asyncio.run(go())


def test_wg24_t01_other_502s_stay_retryable(tmp_path: Path) -> None:
    backend = FakeCognee()
    adapter, sc = adapter_for(backend, tmp_path)

    async def go() -> None:
        await adapter.ensure_dataset(sc)
        backend.fail = BadGatewayError(
            message='{"error": {"code": "upstream_timeout"}}',
            model="sbam-knowledge",
            llm_provider="openai",
        )
        with pytest.raises(CogneeUnavailable):
            await adapter.cognify(sc)

    asyncio.run(go())


def test_wg24_t02_a_build_under_the_cap_is_unaffected(
    world: Callable[..., World], tmp_path: Path
) -> None:
    world(BudgetCaps(knowledge_tokens=10_000))  # a mock call is 30 tokens: 4 calls = 120
    backend = Cognify(calls=4)
    adapter, sc = adapter_for(backend, tmp_path)

    async def go() -> None:
        await with_dataset(adapter, sc)
        await adapter.cognify(sc)
        await asyncio.sleep(0.3)  # the callbacks run as background tasks

    asyncio.run(go())
    assert backend.finished and backend.made == 4


def test_wg24_t01_a_reached_cap_stops_the_running_build(
    world: Callable[..., World], tmp_path: Path
) -> None:
    """The #753 knowledge cap also ends a running build (a mock call is 10 + 20 tokens)."""
    w = world(BudgetCaps(knowledge_tokens=60))
    backend = Cognify(calls=50)
    adapter, sc = adapter_for(backend, tmp_path)
    # the project is the one the calls are metered for
    backend_scope = sc

    async def go() -> None:
        await with_dataset(adapter, sc)
        with pytest.raises(CogneeStopped) as info:
            await adapter.cognify(backend_scope)
        assert info.value.cause is StopCause.TOKEN_CAP
        assert "token budget reached" in str(info.value)

    asyncio.run(go())
    assert not backend.finished and backend.made < 50
    assert w.budget.caps is not None and w.budget.caps.knowledge_tokens == 60


def test_wg24_t03_the_next_call_after_the_cap_is_refused(
    world: Callable[..., World], tmp_path: Path
) -> None:
    w = world(BudgetCaps(knowledge_tokens=60))
    backend = Cognify(calls=1)
    adapter, sc = adapter_for(backend, tmp_path)

    async def fill() -> None:
        # the project already used its cap: two recorded Cognee calls of 30 tokens
        with meter.metered(sc.tenant_id, sc.project_id, "cognify"):
            await one_call(mock_response="ok")
            await one_call(mock_response="ok")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with w.sessions() as session:
                if (session.scalar(select(func.count()).select_from(LlmCall)) or 0) >= 2:
                    return
            await asyncio.sleep(0.05)

    async def go() -> None:
        await with_dataset(adapter, sc)
        await fill()
        with pytest.raises(CogneeStopped) as info:
            await adapter.cognify(sc)
        assert info.value.cause is StopCause.TOKEN_CAP

    asyncio.run(go())
    assert backend.calls.count("cognify") == 0  # refused before Cognee was touched


def test_wg24_t03_a_stopped_block_refuses_further_work() -> None:
    async def go() -> None:
        with meter.metered(TENANT, PROJECT, "x") as stop:
            stop.trigger(StopCause.CREDIT_EXHAUSTED, "no credit")
            ran = []

            async def work() -> None:
                ran.append(1)

            coro = work()
            with pytest.raises(CogneeStopped):
                await stop.run(coro)
            coro.close()  # never started
            assert ran == []

    asyncio.run(go())
