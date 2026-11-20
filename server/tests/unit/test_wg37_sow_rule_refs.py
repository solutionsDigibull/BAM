"""WG37: documents that name rules as R-xx, not RULE-*, pass the rule_refs check without a manual
hint (live run 2026-11-20, #924). The Rules master is empty there, so the only valid `rule_refs` is
`[]` and the documents' own labels belong in `constraints` as text. The golden case is
`server/evals/golden/sow-rule-labels.json`. Nothing here calls a model."""

import json
from pathlib import Path
from typing import Any

import pytest
from knowledge_db import World
from llm_db import FakeBifrost, completion
from sbam_contracts.sow import SowDocument
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.sow.context import rule_ids_of
from sbam_workers.sow.generate import check_rule_refs
from test_sow_generate import Gen

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[2] / "evals" / "golden" / "sow-rule-labels.json").read_text(
        encoding="utf-8"
    )
)
EXPECT = GOLDEN["expect"]


@pytest.fixture
def g(tmp_path: Path) -> Gen:
    return Gen(World(tmp_path), *GOLDEN["documents"])


def answer(g: Gen, rule_refs: list[str], constraints: list[str] | None = None) -> str:
    doc: dict[str, Any] = json.loads(g.answer())
    doc["rules_constraints"]["rule_refs"] = rule_refs
    doc["rules_constraints"]["constraints"] = constraints or [
        "R-01: every listed field must be present",
        "R-03: customer_code must exist in the customers table",
        "R-13: approval is decided by the quote total",
    ]
    return json.dumps(doc)


def feedback(bifrost: FakeBifrost, n: int) -> str:
    return str(bifrost.requests[n]["messages"][-1]["content"])


def test_wg37_t01_the_prompt_states_both_kinds_of_rule_id_with_examples() -> None:
    newest = PromptRegistry().get("sow-generate")
    assert newest.version == "1.3.0"
    text = newest.text
    assert "`RULE-<NAME>`" in text and "`masters`" in text
    assert "`R-01`" in text and "never rewrite one into a `RULE-*` id" in text
    assert "`rule_refs` is `[]`" in text and "keeps its label" in text


def test_wg37_t02_the_golden_case_passes_on_the_first_attempt(g: Gen) -> None:
    bifrost = FakeBifrost([completion(answer(g, EXPECT["rule_refs"]))])
    g.run(bifrost)
    assert len(bifrost.requests) == 1
    [v] = g.versions()
    doc = SowDocument.model_validate(v.structured)
    assert doc.rules_constraints.rule_refs == EXPECT["rule_refs"]
    text = " ".join(doc.rules_constraints.constraints)
    assert all(label in text for label in EXPECT["constraints_keep_labels"])
    # the project's own labels reached the model as data, with no master rule beside them
    user = bifrost.requests[0]["messages"][1]["content"]
    assert "R-03" in user and '<data name="masters">\n(none)' in user


@pytest.mark.parametrize("bad", EXPECT["rejected_answers"], ids=lambda b: b["rule_refs"][0])
def test_wg37_t03_a_label_or_guessed_id_is_named_once_and_the_second_answer_passes(
    g: Gen, bad: dict[str, Any]
) -> None:
    bifrost = FakeBifrost([completion(answer(g, bad["rule_refs"])), completion(answer(g, []))])
    g.run(bifrost)
    assert len(bifrost.requests) <= EXPECT["max_attempts"]
    line = feedback(bifrost, 1)
    assert "rules_constraints.rule_refs[0]: " in line
    # RULE-03 passes the format check, so the master check names it; R-01 fails the format check
    assert any(code in line for code in ("UNKNOWN_RULE_REF", "NOT_A_RULE_ID"))
    assert "goes in constraints as text" in line
    [v] = g.versions()
    assert (v.structured or {})["rules_constraints"]["rule_refs"] == []


def test_wg37_t04_the_reask_carries_fixed_text_never_the_documents_or_the_models_ids(
    g: Gen,
) -> None:
    bifrost = FakeBifrost([completion(answer(g, ["RULE-SECRETISH-9"])), completion(answer(g, []))])
    g.run(bifrost)
    line = feedback(bifrost, 1)
    assert "RULE-SECRETISH-9" not in line  # the model's own text is named by position only
    assert GOLDEN["documents"][0][:30] not in line


def test_wg37_t05_a_master_rule_id_is_accepted_and_an_unknown_one_lists_the_real_ids() -> None:
    nodes = [
        {"node_id": "RULE-PRICING-01", "stable_id": "R1", "master_type": "RULES"},
        {"node_id": "TOOL-X", "stable_id": "T1", "master_type": "TOOLING"},
        {"node_id": "ORG", "stable_id": "O", "master_type": "ORGANIZATION"},
    ]
    masters = "\n".join(json.dumps(n) for n in nodes) + "\nnot json\n"
    ids = rule_ids_of(masters)
    assert ids == {"RULE-PRICING-01", "R1"}  # only Rules entries; junk lines are skipped
    assert rule_ids_of("(none)") == frozenset()

    from sbam_workers.sow.context import SowContext

    check = check_rule_refs(SowContext("FULL", {}, {}, {}, rule_ids=ids))
    doc = SowDocument.model_validate_json(
        json.dumps(
            {
                **json.loads(
                    (
                        Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"
                    ).read_text(encoding="utf-8")
                ),
            }
        )
    )  # its rule_refs is RULE-AUTH-LOCKOUT: not in this master
    [line] = check(doc)
    assert "UNKNOWN_RULE_REF" in line and "RULE-PRICING-01" in line
    assert "RULE-AUTH-LOCKOUT" not in line
    ok = doc.model_copy(
        update={
            "rules_constraints": doc.rules_constraints.model_copy(
                update={"rule_refs": ["RULE-PRICING-01"]}
            )
        }
    )
    assert check(ok) == ()
