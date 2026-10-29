"""#420: budgets that cannot silently stop working: finite limits, a cost for every counted call.

Same harness as test_llm_derivative_cache.py (fake Bifrost over httpx, SQLite with the real tables).
"""

import asyncio
import json
import logging
import uuid
from decimal import Decimal
from typing import Any

import pytest
from llm_db import PROJECT, TENANT, FakeBifrost, completion, make_client, scope, settings
from pydantic import BaseModel, ConfigDict, ValidationError
from sbam_api.db.repository import TenantContext
from sbam_api.llm.repository import LlmCallRepository
from sbam_workers.llm.budget import BudgetExceeded

GOOD = json.dumps({"count": 3, "label": "ok"})
TOKENS: dict[str, Any] = {
    "prompt_tokens": 1_000_000,
    "completion_tokens": 500_000,
}  # 1 M in, 0.5 M out


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int
    label: str


def run(client: Any, sc: Any, rfq: str = "A", alias: str = "sbam-sow") -> Any:
    return asyncio.run(
        client.run(
            alias=alias,
            prompt_id="smoke",
            output_type=Answer,
            context={"rfq": rfq},
            scope=sc,
        )
    )


def no_cost(**usage: int) -> dict[str, Any]:
    return completion(GOOD, **{**TOKENS, **usage})


# --- 1. the limit must be a real number -------------------------------------------------------


@pytest.mark.parametrize("value", ["NaN", "nan", "sNaN", "Infinity", "inf", "-Infinity"])
@pytest.mark.parametrize("name", ["llm_project_budget_usd", "llm_tenant_budget_usd"])
def test_420_t01_a_non_finite_budget_refuses_to_start(name: str, value: str) -> None:
    with pytest.raises(ValidationError, match="finite"):
        settings(**{name: value})


@pytest.mark.parametrize("value", ["0", "-1", "-0.01"])
def test_420_t01_a_zero_or_negative_budget_refuses_to_start(value: str) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        settings(llm_project_budget_usd=value)


def test_420_t01_unset_empty_and_real_budgets_are_accepted() -> None:
    assert settings().llm_project_budget_usd is None
    assert (
        settings(llm_project_budget_usd="", llm_tenant_budget_usd="  ").llm_project_budget_usd
        is None
    )
    assert settings(llm_project_budget_usd="12.5").llm_project_budget_usd == Decimal("12.5")
    assert settings(llm_tenant_budget_usd="1e3").llm_tenant_budget_usd == Decimal("1E+3")


def test_420_t01_compose_passes_empty_values_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LLM_PROJECT_BUDGET_USD", "LLM_TENANT_BUDGET_USD", "LLM_PRICE_USD_PER_MTOK"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    from sbam_workers.settings import WorkerSettings

    loaded = WorkerSettings(_env_file=None)  # type: ignore[call-arg]
    assert loaded.llm_project_budget_usd is None and loaded.llm_price_usd_per_mtok == {}


@pytest.mark.parametrize(
    "prices",
    [
        {"sbam-sow": {"in": 1}},  # no 'out'
        {"sbam-sow": {"in": 1, "out": 2, "extra": 3}},
        {"sbam-sow": {"in": -1, "out": 2}},
        {"sbam-sow": {"in": "NaN", "out": 2}},
        {"sbam-sow": {"in": "Infinity", "out": 2}},
    ],
)
def test_420_t02_a_malformed_price_table_refuses_to_start(prices: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        settings(llm_price_usd_per_mtok=prices)


def test_420_t02_the_price_table_comes_from_one_json_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PRICE_USD_PER_MTOK", '{"sbam-sow": {"in": 2.5, "out": 10}}')
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    from sbam_workers.settings import WorkerSettings

    loaded = WorkerSettings(_env_file=None)  # type: ignore[call-arg]
    assert loaded.llm_price_usd_per_mtok == {
        "sbam-sow": {"in": Decimal("2.5"), "out": Decimal("10")}
    }


# --- 2. a budget needs a cost ------------------------------------------------------------------


def test_420_t03_a_blind_alias_is_refused_after_its_first_call_not_counted_as_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bifrost = FakeBifrost([no_cost()])
    client, _ = make_client(bifrost, llm_project_budget_usd="1")
    with caplog.at_level(logging.ERROR):
        first = run(client, scope(), "A")  # the answer that was paid for is kept and returned
    assert first.output.count == 3 and "reported no cost" in caplog.text
    with pytest.raises(BudgetExceeded, match="cannot count them"):
        run(client, scope(), "B")
    assert len(bifrost.requests) == 1  # the second call never reached the model
    assert run(client, scope(), "A").cached  # a hit costs nothing, so it is still served


def test_420_t03_the_tenant_budget_has_the_same_guard() -> None:
    client, _ = make_client(FakeBifrost([no_cost()]), llm_tenant_budget_usd="1")
    run(client, scope(), "A")
    with pytest.raises(BudgetExceeded, match="cannot count them"):
        run(client, scope(project_id=uuid.uuid4()), "B")


def test_420_t04_a_configured_price_stands_in_for_the_missing_cost() -> None:
    prices = {"sbam-sow": {"in": 2, "out": 4}}  # 1 M in * 2 + 0.5 M out * 4 = 4 USD per call
    bifrost = FakeBifrost([no_cost()])
    client, sessions = make_client(
        bifrost, llm_project_budget_usd="5", llm_price_usd_per_mtok=prices
    )
    run(client, scope(), "A")
    with sessions() as session:
        assert LlmCallRepository(session, TenantContext(TENANT, PROJECT)).spent() == Decimal("4")
    run(client, scope(), "B")  # 4 < 5: allowed; spend is now 8
    with pytest.raises(BudgetExceeded, match=r"spent \$8\.0000 of its \$5\.0000"):
        run(client, scope(), "C")
    assert len(bifrost.requests) == 2


def test_420_t04_bifrosts_own_cost_wins_over_the_price_table() -> None:
    prices = {"sbam-sow": {"in": 100, "out": 100}}
    bifrost = FakeBifrost([completion(GOOD, cost={"total_cost": 0.25}, **TOKENS)])
    client, sessions = make_client(
        bifrost, llm_project_budget_usd="5", llm_price_usd_per_mtok=prices
    )
    run(client, scope(), "A")
    with sessions() as session:
        assert LlmCallRepository(session, TenantContext(TENANT, PROJECT)).spent() == Decimal("0.25")


def test_420_t05_a_free_model_is_declared_with_a_zero_price_and_never_blinds_the_budget() -> None:
    prices = {"sbam-sow": {"in": 0, "out": 0}}
    bifrost = FakeBifrost([no_cost()])
    client, _ = make_client(bifrost, llm_project_budget_usd="1", llm_price_usd_per_mtok=prices)
    for rfq in "ABC":
        run(client, scope(), rfq)
    assert len(bifrost.requests) == 3


def test_420_t05_without_a_budget_nothing_changes() -> None:
    bifrost = FakeBifrost([no_cost()])
    client, sessions = make_client(
        bifrost, llm_price_usd_per_mtok={"sbam-sow": {"in": 9, "out": 9}}
    )
    for rfq in "ABC":
        run(client, scope(), rfq)
    with sessions() as session:  # the table is only read while a budget is set
        assert LlmCallRepository(session, TenantContext(TENANT, PROJECT)).spent() == 0


def test_420_t06_a_reply_with_no_tokens_is_not_blind() -> None:
    bifrost = FakeBifrost([completion(GOOD, prompt_tokens=0, completion_tokens=0)])
    client, _ = make_client(bifrost, llm_project_budget_usd="1")
    run(client, scope(), "A")
    run(client, scope(), "B")
    assert len(bifrost.requests) == 2


def test_420_t07_blindness_is_per_alias() -> None:
    client, _ = make_client(FakeBifrost([no_cost()]), llm_project_budget_usd="1")
    run(client, scope(), "A", alias="sbam-sow")
    with pytest.raises(BudgetExceeded):
        asyncio.run(client.budget.check(scope(), "sbam-sow"))
    asyncio.run(client.budget.check(scope(), "sbam-isatvon"))  # judged on its own calls


def test_420_t08_a_price_value_that_is_not_json_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PRICE_USD_PER_MTOK", "sbam-sow=2")
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    from sbam_workers.settings import WorkerSettings

    with pytest.raises(ValidationError, match="JSON object"):
        WorkerSettings(_env_file=None)  # type: ignore[call-arg]
