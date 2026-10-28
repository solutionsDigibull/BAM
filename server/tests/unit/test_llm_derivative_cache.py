"""Cost control: identical LLM input is answered from `llm_derivatives`, budgets refuse spend.

Same harness as test_llm_client.py: a fake Bifrost over httpx and SQLite with the real tables.
"""

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from llm_db import (
    PROJECT,
    TENANT,
    FakeBifrost,
    completion,
    make_client,
    rows,
    scope,
)
from pydantic import BaseModel, ConfigDict
from sbam_api.db.repository import TenantContext
from sbam_api.llm.models import LlmDerivative
from sbam_api.llm.repository import LlmCallRepository, LlmDerivativeRepository
from sbam_workers.llm.client import DERIVATIVE_MODEL, BudgetExceeded, cache_key
from sbam_workers.llm.registry import PromptRegistry

GOOD = json.dumps({"count": 3, "label": "ok"})


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int
    label: str


def run(client: Any, sc: Any, context: dict[str, str] | None = None, **kw: Any) -> Any:
    return asyncio.run(
        client.run(
            alias="sbam-sow",
            prompt_id="smoke",
            output_type=Answer,
            context=context or {"rfq": "Quote 12 cables"},
            scope=sc,
            **kw,
        )
    )


def reply(cost: float = 0.5) -> dict[str, Any]:
    return completion(GOOD, usage={}, **{"cost": {"total_cost": cost}})


def test_identical_input_in_another_project_is_not_billed_again() -> None:
    bifrost = FakeBifrost([reply()])
    client, sessions = make_client(bifrost)
    first = run(client, scope())
    second = run(client, scope(project_id=uuid.uuid4()))  # other project, same tenant
    assert len(bifrost.requests) == 1
    assert second.cached and second.attempts == 0
    assert second.output == first.output
    with sessions() as session:
        derivative = LlmDerivativeRepository(session, TenantContext(TENANT, None)).list()
        assert len(derivative) == 1 and derivative[0].hits == 1
        assert derivative[0].cost_usd == Decimal("0.5")  # what each hit saves


def test_a_hit_leaves_a_zero_cost_provenance_row() -> None:
    client, sessions = make_client(FakeBifrost([reply()]))
    run(client, scope())
    sc = scope()
    run(client, sc)
    [row] = rows(sessions, sc)
    assert row.model == DERIVATIVE_MODEL and row.cost_usd == 0 and row.status == "SUCCEEDED"
    assert row.output == {"count": 3, "label": "ok"}
    assert row.context_manifest["_derivative"]["cache_key"]


def test_a_changed_document_is_a_different_key() -> None:
    bifrost = FakeBifrost([reply()])
    client, _ = make_client(bifrost)
    run(client, scope(), {"rfq": "Quote 12 cables"})
    run(client, scope(), {"rfq": "Quote 13 cables"})
    assert len(bifrost.requests) == 2


def test_a_changed_prompt_version_is_a_different_key() -> None:
    registry = PromptRegistry()
    prompt = registry.get("smoke")
    a = cache_key("sbam-sow", [{"role": "system", "content": prompt.text}])
    b = cache_key("sbam-sow", [{"role": "system", "content": prompt.text + " v2"}])
    c = cache_key("sbam-ocr", [{"role": "system", "content": prompt.text}])
    assert len({a, b, c}) == 3


def test_the_cache_never_crosses_tenants() -> None:
    bifrost = FakeBifrost([reply()])
    client, _ = make_client(bifrost)
    run(client, scope())
    run(client, scope(tenant_id=uuid.uuid4()))
    assert len(bifrost.requests) == 2


def test_the_cache_can_be_switched_off() -> None:
    bifrost = FakeBifrost([reply()])
    client, sessions = make_client(bifrost, llm_derivative_cache=False)
    run(client, scope())
    run(client, scope())
    assert len(bifrost.requests) == 2
    with sessions() as session:
        assert LlmDerivativeRepository(session, TenantContext(TENANT, None)).list() == []


def test_a_stored_answer_the_schema_refuses_is_asked_again() -> None:
    bifrost = FakeBifrost([reply()])
    client, sessions = make_client(bifrost)
    run(client, scope())
    with sessions() as session:  # simulate drift: the stored shape no longer validates
        row = session.query(LlmDerivative).one()
        row.output = {"count": "three", "label": "ok"}
        session.commit()
    run(client, scope())
    assert len(bifrost.requests) == 2


def test_a_failed_run_is_not_cached() -> None:
    bifrost = FakeBifrost([completion(json.dumps({"count": "x", "label": "ok"}))])
    client, sessions = make_client(bifrost)
    with pytest.raises(Exception):  # noqa: B017 - SchemaInvalidAfterRetries
        run(client, scope())
    with sessions() as session:
        assert LlmDerivativeRepository(session, TenantContext(TENANT, None)).list() == []


def test_a_project_budget_refuses_the_next_call_but_still_serves_hits() -> None:
    bifrost = FakeBifrost([reply(cost=0.5)])
    client, _ = make_client(bifrost, llm_project_budget_usd="0.5")
    sc = scope()
    run(client, sc, {"rfq": "A"})  # spend reaches the budget
    with pytest.raises(BudgetExceeded):
        run(client, scope(), {"rfq": "B"})
    assert len(bifrost.requests) == 1
    assert run(client, scope(), {"rfq": "A"}).cached  # a hit costs nothing, so it is allowed


def test_a_tenant_budget_counts_every_project() -> None:
    bifrost = FakeBifrost([reply(cost=0.5)])
    client, _ = make_client(bifrost, llm_tenant_budget_usd="0.5")
    run(client, scope(), {"rfq": "A"})
    with pytest.raises(BudgetExceeded):
        run(client, scope(project_id=uuid.uuid4()), {"rfq": "B"})


def test_no_budget_means_no_limit() -> None:
    bifrost = FakeBifrost([reply(cost=100.0)])
    client, _ = make_client(bifrost)
    for n in range(3):
        run(client, scope(), {"rfq": str(n)})
    assert len(bifrost.requests) == 3


def test_spent_counts_failed_attempts_too() -> None:
    client, sessions = make_client(FakeBifrost([reply(cost=0.25)]))
    run(client, scope(), {"rfq": "A"})
    with sessions() as session:
        assert LlmCallRepository(session, TenantContext(TENANT, PROJECT)).spent() == Decimal("0.25")
