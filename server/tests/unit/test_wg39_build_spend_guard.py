"""WG-39 (#926): a per-build spend cap. `LLM_BUILD_BUDGET_USD` stops the next model call of one
build workflow (`build-<sub_skill>-<n>`) once the spend recorded under that workflow id reaches
it. The refusal is a `BudgetExceeded`, which the SubSkillBuild workflow already turns into
generation_failed and NEEDS_FDE_REVIEW (MODEL_FAILURES): the Sub-Skill parks with a reason."""

import asyncio
import json
import uuid
from dataclasses import replace
from typing import Any

import pytest
from llm_db import FakeBifrost, completion, make_client, scope
from pydantic import BaseModel, ConfigDict
from sbam_contracts.runs import build_workflow_id
from sbam_workers.llm.budget import BudgetExceeded
from sbam_workers.pipeline.sub_skill_build import MODEL_FAILURES

GOOD = json.dumps({"count": 3, "label": "ok"})
BUILD = build_workflow_id(uuid.uuid4(), 1)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    count: int
    label: str


def reply() -> dict[str, Any]:
    body: dict[str, Any] = completion(GOOD)
    body["usage"]["cost"] = 0.5  # what Bifrost reports for one call, in USD
    return body


def run(client: Any, sc: Any, n: int) -> Any:
    sc = replace(sc, activity_id=f"{sc.activity_id}-{n}")  # a new activity: no replay hit
    return asyncio.run(
        client.run(
            alias="sbam-sow",
            prompt_id="smoke",
            output_type=Answer,
            context={"rfq": str(n)},
            scope=sc,
        )
    )


def test_wg39_t20_a_build_is_refused_once_its_spend_reaches_the_cap() -> None:
    bifrost = FakeBifrost([reply()])
    client, _ = make_client(bifrost, llm_build_budget_usd="1.0")
    sc = scope(temporal_workflow_id=BUILD)
    run(client, sc, 1)  # 0.5 of 1.0
    run(client, sc, 2)  # 1.0 recorded: the cap is reached
    with pytest.raises(BudgetExceeded, match="per-build budget"):
        run(client, sc, 3)
    assert len(bifrost.requests) == 2  # the third never left


def test_wg39_t21_another_builds_spend_does_not_count() -> None:
    bifrost = FakeBifrost([reply()])
    client, _ = make_client(bifrost, llm_build_budget_usd="1.0")
    for n in (1, 2):
        run(client, scope(temporal_workflow_id=build_workflow_id(uuid.uuid4(), n)), n)
    run(client, scope(temporal_workflow_id=build_workflow_id(uuid.uuid4(), 3)), 3)
    assert len(bifrost.requests) == 3  # each build is far below its own cap


def test_wg39_t22_a_stage_that_is_not_a_build_is_not_capped_by_it() -> None:
    bifrost = FakeBifrost([reply()])
    client, _ = make_client(bifrost, llm_build_budget_usd="0.1")
    sc = scope(temporal_workflow_id="project-pipeline-1")
    for n in (1, 2, 3):
        run(client, sc, n)
    assert len(bifrost.requests) == 3


def test_wg39_t23_without_the_setting_there_is_no_per_build_cap() -> None:
    bifrost = FakeBifrost([reply()])
    client, _ = make_client(bifrost)
    assert client.budget.settings.llm_build_budget_usd is None
    for n in (1, 2, 3):
        run(client, scope(temporal_workflow_id=BUILD), n)
    assert len(bifrost.requests) == 3


def test_wg39_t24_the_refusal_parks_the_sub_skill_through_the_existing_model_failure_path() -> None:
    # MODEL_STAGES -> generation_failed -> NEEDS_FDE_REVIEW
    assert "BudgetExceeded" in MODEL_FAILURES


def test_wg39_t25_the_setting_is_validated_like_the_other_budgets() -> None:
    from llm_db import settings

    assert settings(llm_build_budget_usd="").llm_build_budget_usd is None
    with pytest.raises(ValueError):
        settings(llm_build_budget_usd="0")
