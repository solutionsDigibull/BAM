"""WG-36 (#923): the codegen and corrector requests carry the workflow's tables and columns, and a
state query on a column or table that does not exist is scored as a failure.

The live run of 2026-11-20 (real model) wrote state queries on a column `attempt` and a table
`attempts_table` that no workflow table has. The prompts never mentioned the `workflow_schema_sql`
block they were sent, so nothing told the model to stay on it. Everything here is SYNTHETIC
(`server/evals/golden/codegen-invented-column.json`) and calls no model. The corrector's
"no member" patch hint is WG40-T06 (#938) and is not repeated.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from llm_db import FakeBifrost, completion, make_client, scope
from sbam_workers.build.codegen import LoadedTemplate, load_template, render_skeleton
from sbam_workers.build.codegen.bodies import (
    BodyFindingCode,
    CodeBodies,
    _check_state_queries,
    generate_bodies,
    request_context,
)
from sbam_workers.build.codegen.correction import request_context as corrector_context
from sbam_workers.build.codegen.params import SCHEMA_PARAM
from sbam_workers.build.codegen.schema_outline import outline
from sbam_workers.llm.registry import PromptRegistry
from test_codegen_bodies import ms01_params

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[2] / "evals" / "golden" / "codegen-invented-column.json"
    ).read_text(encoding="utf-8")
)
SCHEMA: str = GOLDEN["schema_sql"]
ANSWERS: dict[str, dict[str, Any]] = {a["id"]: a for a in GOLDEN["answers"]}


def findings(answer: dict[str, Any]) -> list[str]:
    return [
        f.message
        for f in _check_state_queries(
            answer["state_queries"],
            answer["fresh_state"],
            GOLDEN["tables"],
            hint=True,
            schema_sql=SCHEMA,
        )
    ]


def score(answer: dict[str, Any]) -> float:
    """1.0 when the answer's state queries pass the build's own pre-check against the workflow
    schema, else 0.0. Invented tables and columns are what it exists to catch."""
    return 0.0 if findings(answer) else 1.0


def params() -> dict[str, Any]:
    out = ms01_params()
    out["fresh_state"] = GOLDEN["fresh_state"]
    out[SCHEMA_PARAM] = SCHEMA
    return out


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.1")


def test_wg36_t01_the_outline_lists_every_table_and_column_with_its_type() -> None:
    assert outline(SCHEMA).splitlines() == [
        "order_events(id uuid, order_id text, kind text, payload jsonb)",
        "order_requests(id uuid, order_id text, status text, missing_count int, "
        "payload jsonb, created_at timestamptz)",
    ]
    assert outline("not sql at all (") == "" and outline("") == ""


def test_wg36_t02_the_codegen_request_carries_the_schema_and_the_outline(
    template: LoadedTemplate,
) -> None:
    p = params()
    context = request_context(p, render_skeleton(template, p), ["psycopg"])
    assert context["workflow_schema_sql"] == SCHEMA
    assert context["workflow_tables"] == outline(SCHEMA)
    assert SCHEMA_PARAM not in context["manifest"]  # one place, not two
    assert "order_requests" not in context["manifest"]  # and the skeleton does not replace it
    assert all("order_requests" not in v for k, v in context.items() if k.startswith("skeleton."))


def test_wg36_t03_a_request_without_a_schema_has_neither_block(template: LoadedTemplate) -> None:
    p = ms01_params()
    context = request_context(p, render_skeleton(template, p), ["psycopg"])
    assert "workflow_schema_sql" not in context and "workflow_tables" not in context


def test_wg36_t04_the_corrector_request_carries_both_blocks(template: LoadedTemplate) -> None:
    p = params()
    bodies = CodeBodies(action_body="pass", check_body="pass", state_queries={}, helpers="")
    skeleton = render_skeleton(template, p)
    context = corrector_context({}, bodies, skeleton, ["psycopg"], [], schema_sql=SCHEMA)
    assert context["workflow_tables"] == outline(SCHEMA)
    assert context["workflow_schema_sql"] == SCHEMA
    bare = corrector_context({}, bodies, skeleton, ["psycopg"], [])
    assert "workflow_tables" not in bare and "workflow_schema_sql" not in bare


@pytest.mark.parametrize("answer_id", list(ANSWERS))
def test_wg36_t05_the_golden_answers_score_as_their_case_says(answer_id: str) -> None:
    answer = ANSWERS[answer_id]
    assert score(answer) == (1.0 if answer["expect"] == "accepted" else 0.0), answer_id
    if answer["expect"] == "rejected":  # and the reason is the one the live run reported
        assert answer["reason"] in " ".join(findings(answer)), answer_id


def test_wg36_t06_the_recorded_finding_shape_is_reproduced_on_synthetic_data() -> None:
    [message] = findings(ANSWERS["invented-column"])
    assert message.startswith("'attempt': order_requests has no column attempt (columns: ")
    assert "created_at, id, missing_count, order_id, payload, status" in message


def test_wg36_t07_an_invented_column_is_held_with_the_findings_and_the_request_had_the_schema(
    template: LoadedTemplate,
) -> None:
    p = params()
    answer = {
        "action_body": "return {}",
        "check_body": "return {'otp_match': True, 'policy_violation': False}",
        "state_queries": ANSWERS["invented-column"]["state_queries"],
        "helpers": "",
    }
    bifrost = FakeBifrost([completion(json.dumps(answer))])
    client, _ = make_client(bifrost)
    out = asyncio.run(
        generate_bodies(client, scope(), template, p, render_skeleton(template, p), ["psycopg"])
    )
    assert out.files == {} and any(
        f.code is BodyFindingCode.STATE_QUERY_INVALID and "'attempt'" in f.message
        for f in out.findings
    )
    user = str(bifrost.requests[0]["messages"][-1]["content"])
    assert '<data name="workflow_tables">' in user and "order_requests(id uuid" in user
    assert '<data name="workflow_schema_sql">' in user and "CREATE TABLE order_events" in user


def test_wg36_t08_the_prompts_tell_the_model_to_use_only_the_listed_names() -> None:
    registry = PromptRegistry()
    coder = registry.get("codegen-bodies")
    assert coder.version == "1.5.0"
    assert "`workflow_tables`" in coder.text and "`workflow_schema_sql`" in coder.text
    assert "ONLY tables and columns that exist" in coder.text.replace("\n", " ")
    assert "Never invent a column or a table" in coder.text.replace("\n", " ")
    assert "leave that name out of `state_queries`" in coder.text.replace("\n", " ")
    corrector = registry.get("correct-code")
    flat = corrector.text.replace("\n", " ")
    assert corrector.version == "1.5.0" and "`workflow_tables`" in flat
    assert "never answer a `STATE_QUERY_INVALID` finding with another name" in flat
    assert "the message lists the members that exist" in flat
    # released versions are untouched
    assert "workflow_tables" not in registry.get("codegen-bodies", "1.4.0").text
    assert "workflow_tables" not in registry.get("correct-code", "1.4.0").text
