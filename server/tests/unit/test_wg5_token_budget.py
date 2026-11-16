"""WG-5 (#753): token caps over `llm_calls`, a pre-call refusal, one alert per level, a price for
every alias that has one. Same harness as test_llm_budget_hardening.py (fake Bifrost, SQLite).

Cognee's own calls are metered, not refused (ending a running build is #777); the Sub-Skill cap and
the per-project settings are #776.
"""

import asyncio
import json
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from llm_db import FakeBifrost, completion, make_client, scope
from pydantic import BaseModel, ConfigDict
from sbam_contracts.budget import BudgetCaps
from sbam_contracts.notifications import NotificationType
from sbam_workers.llm.budget import STOP_REASON, BudgetExceeded

GOOD = json.dumps({"count": 3, "label": "ok"})
CAPS = BudgetCaps(
    sub_skill_tokens=500, workflow_tokens=1000, knowledge_tokens=2000
)  # alerts at 50% and 80%


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int
    label: str


def reply(tokens: int) -> dict[str, Any]:
    return completion(GOOD, prompt_tokens=tokens - 10, completion_tokens=10)


def run(client: Any, sc: Any, alias: str = "sbam-sow", rfq: str = "A") -> Any:
    sc = replace(sc, activity_id=f"{sc.activity_id}-{rfq}")  # a new activity: no replay hit
    return asyncio.run(
        client.run(
            alias=alias, prompt_id="smoke", output_type=Answer, context={"rfq": rfq}, scope=sc
        )
    )


def client_with_alerts(tokens: int, **over: Any) -> tuple[Any, Any, list[tuple[Any, str]], Any]:
    bifrost = FakeBifrost([reply(tokens)])
    client, sessions = make_client(bifrost, **over)
    client.budget.caps = CAPS
    sent: list[tuple[Any, str]] = []

    def alert(sc: Any, type: NotificationType, key: str, params: dict[str, object]) -> None:
        sent.append((type, key))

    store: Any = client.store
    store.alert = alert
    return client, bifrost, sent, sessions


def test_wg5_t01_a_call_is_refused_once_the_cap_is_reached() -> None:
    client, bifrost, _, _ = client_with_alerts(600)
    sc = scope()
    run(client, sc, rfq="1")  # 600 of 1000: allowed
    run(client, sc, rfq="2")  # 1200 now recorded: the cap is passed
    with pytest.raises(BudgetExceeded, match=STOP_REASON):
        run(client, sc, rfq="3")
    assert len(bifrost.requests) == 2  # the third was refused before it was sent


def test_wg5_t01_a_retry_with_the_same_activity_id_cannot_step_around_the_cap() -> None:
    client, bifrost, _, _ = client_with_alerts(1200)
    run(client, scope(activity_id="same"), rfq="1")
    with pytest.raises(BudgetExceeded):
        run(client, scope(activity_id="same", attempt=2), rfq="2")
    assert len(bifrost.requests) == 1


def test_wg5_t02_each_level_has_its_own_alert_key() -> None:
    client, _, sent, _ = client_with_alerts(300)
    sc = scope()
    for n in range(4):  # used before each call: 0, 300, 600 (60%), 900 (90%)
        run(client, sc, rfq=str(n))
    # the store dedupes on the key, so a level that is seen twice still writes one notification
    assert sent == [
        (NotificationType.TOKEN_BUDGET_WARNING, "WORKFLOW:FIRST_ALERT"),
        (NotificationType.TOKEN_BUDGET_LIMIT, "WORKFLOW:SECOND_ALERT"),
    ]


def test_wg5_t02_the_first_level_tells_the_fde_only() -> None:
    client, _, sent, _ = client_with_alerts(550)
    sc = scope()
    run(client, sc, rfq="1")
    run(client, sc, rfq="2")  # 550 = 55%
    assert sent == [(NotificationType.TOKEN_BUDGET_WARNING, "WORKFLOW:FIRST_ALERT")]


def test_wg5_t02_the_stop_alerts_the_admin_too() -> None:
    from sbam_api.notifications.service import ADMIN_TYPES

    assert NotificationType.TOKEN_BUDGET_LIMIT in ADMIN_TYPES
    assert NotificationType.TOKEN_BUDGET_WARNING not in ADMIN_TYPES


def test_wg5_an_alert_that_cannot_be_written_never_blocks_the_call() -> None:
    client, bifrost, _, _ = client_with_alerts(550)

    def broken(*_: Any) -> None:
        raise RuntimeError("no database")

    store: Any = client.store
    store.alert = broken
    sc = scope()
    run(client, sc, rfq="1")
    run(client, sc, rfq="2")  # 55%: the alert fails, the call still runs
    assert len(bifrost.requests) == 2


def test_wg5_t04_knowledge_calls_carry_a_cost_when_the_alias_has_a_price() -> None:
    client, _, _, _ = client_with_alerts(1000)
    prices = {"sbam-knowledge": {"in": Decimal(2), "out": Decimal(10)}}
    client.budget.settings = client.budget.settings.model_copy(
        update={"llm_price_usd_per_mtok": prices}
    )
    cost = client.budget.cost_of("sbam-knowledge", 1_000_000, 500_000, Decimal(0))
    assert cost == Decimal(7)  # no USD budget is set: the price still applies


def test_wg5_t04_without_a_price_or_a_reported_cost_the_cost_stays_zero() -> None:
    client, _, _, _ = client_with_alerts(1000)
    assert client.budget.cost_of("sbam-knowledge", 1_000_000, 0, Decimal(0)) == 0


def test_wg5_t05_another_tenants_usage_is_never_counted() -> None:
    client, _, sent, _ = client_with_alerts(1200)
    run(client, scope(tenant_id=uuid.uuid4(), project_id=uuid.uuid4()), rfq="1")
    other = scope()  # the shared test tenant and project: nothing recorded for it
    run(client, other, rfq="2")  # allowed: the first run's tokens belong to someone else
    assert sent == []


def test_wg5_knowledge_tokens_have_their_own_cap() -> None:
    client, _, _, _ = client_with_alerts(1200)
    sc = scope()
    run(client, sc, rfq="1")  # 1200 workflow tokens: over the Workflow cap of 1000
    with pytest.raises(BudgetExceeded):
        run(client, sc, rfq="2")
    # a knowledge alias counts against its own, larger cap: 1200 of 2000 is only a warning
    client.store.add(
        sc,
        {
            "alias": "sbam-knowledge",
            "model": "m",
            "prompt_id": "p",
            "prompt_version": "1",
            "prompt_sha256": "0" * 64,
            "attempt": 1,
            "status": "SUCCEEDED",
            "trace_id": "t",
            "context_manifest": {},
            "tokens_in": 1200,
            "tokens_out": 0,
        },
    )
    asyncio.run(client.budget.check_tokens(sc, "sbam-knowledge"))
