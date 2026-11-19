"""E14.2 (#130): pure Macro and Workflow validators."""

import random
import uuid

from sbam_contracts.manifest import Dag
from sbam_contracts.state_machines import SubSkillState
from sbam_workers.build.dag import find_cycle
from sbam_workers.certify.workflow_checks import (
    Check,
    ChildSkill,
    Finding,
    children_findings,
    dag_findings,
    macro_verdicts,
    schema_findings,
    workflow_verdict,
)

BUNDLE_A = "a" * 64
BUNDLE_B = "b" * 64

SCHEMA = """
CREATE TABLE orders (id uuid PRIMARY KEY, total numeric);
CREATE TABLE app."Invoices" (id uuid PRIMARY KEY);
CREATE TABLE handoff_ms02 (id bigserial PRIMARY KEY, skill_id text, payload jsonb);
"""
DECLARED = {
    "MS01.1_Intake": ["orders"],
    "MS01.2_Bill": ["public.invoices"],
    "MS02.1_Ship": ["HANDOFF_MS02"],
}


def _dag(edges: list[tuple[str, str, str]], extra: tuple[str, ...] = ()) -> Dag:
    nodes = [{"id": "MS01_Order", "kind": "macro"}, {"id": "MS02_Ship", "kind": "macro"}]
    nodes += [
        {"id": s, "kind": "skill", "macro_category": s[:4]}
        for s in ("MS01.1_Intake", "MS01.2_Bill", "MS02.1_Ship", *extra)
    ]
    return Dag(
        nodes=nodes,  # type: ignore[arg-type]
        edges=[{"from": a, "to": b, "kind": k} for a, b, k in edges],
    )


GOOD_EDGES = [
    ("MS01_Order", "MS01.1_Intake", "contains"),
    ("MS01_Order", "MS01.2_Bill", "contains"),
    ("MS02_Ship", "MS02.1_Ship", "contains"),
    ("MS01.1_Intake", "MS01.2_Bill", "next"),
    ("MS01.2_Bill", "MS02_Ship", "next"),
    ("MS01.1_Intake", "MS01.2_Bill", "data"),
]


def _child(
    skill_id: str, macro: str, *, certified: bool = True, cert: str | None = None
) -> ChildSkill:
    return ChildSkill(
        skill_id=skill_id,
        macro=macro,
        state=SubSkillState.CERTIFIED if certified else SubSkillState.BUILD_ERROR,
        certified_content_sha256=cert if cert is not None else f"sha256:{BUNDLE_A}",
        bundle_sha256=BUNDLE_A,
    )


def _children() -> list[ChildSkill]:
    return [
        _child("MS01.1_Intake", "MS01_Order"),
        _child("MS01.2_Bill", "MS01_Order"),
        _child("MS02.1_Ship", "MS02_Ship"),
    ]


def test_e14_2_happy_path_has_no_findings() -> None:
    assert dag_findings(_dag(GOOD_EDGES)) == ()
    assert schema_findings(SCHEMA, DECLARED) == ()
    assert children_findings(_children()) == ()
    assert macro_verdicts(_children()) == {"MS01_Order": (), "MS02_Ship": ()}
    assert workflow_verdict(_dag(GOOD_EDGES), SCHEMA, DECLARED, _children()) == ()


def test_e14_2_t03_cycle_detected() -> None:
    edges = [*GOOD_EDGES, ("MS01.2_Bill", "MS01.1_Intake", "data")]
    findings = dag_findings(_dag(edges))
    assert findings == (
        Finding(
            Check.DAG_CYCLE,
            "MS01.1_Intake -> MS01.2_Bill -> MS01.1_Intake",
            "the Workflow DAG loops: MS01.1_Intake -> MS01.2_Bill -> MS01.1_Intake",
        ),
    )
    verdict = workflow_verdict(_dag(edges), SCHEMA, DECLARED, _children())
    assert [f.check for f in verdict] == [Check.DAG_CYCLE]


def test_e14_2_a_next_edge_back_to_its_own_macro_is_not_a_cycle() -> None:
    """build_dag accepts a `next` naming the skill's own Macro; with `contains` it only looks like
    a loop, and only data edges are build dependencies."""
    edges = [*GOOD_EDGES, ("MS01.2_Bill", "MS01_Order", "next")]
    assert dag_findings(_dag(edges)) == ()
    assert workflow_verdict(_dag(edges), SCHEMA, DECLARED, _children()) == ()


def test_e14_2_a_next_loop_between_skills_is_not_a_cycle() -> None:
    edges = [*GOOD_EDGES, ("MS01.2_Bill", "MS01.1_Intake", "next")]
    assert dag_findings(_dag(edges)) == ()
    assert workflow_verdict(_dag(edges), SCHEMA, DECLARED, _children()) == ()


def test_e14_2_find_cycle_is_generic_and_still_works_on_uuids() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert find_cycle({a: [b], b: [a]}) in ([a, b, a], [b, a, b])
    assert find_cycle({"x": ["y"], "y": []}) == []


def test_e14_2_unknown_edge_endpoint() -> None:
    edges = [*GOOD_EDGES, ("MS01.2_Bill", "MS09.1_Ghost", "next")]
    findings = dag_findings(_dag(edges))
    assert findings == (
        Finding(
            Check.DAG_UNKNOWN_NODE,
            "MS09.1_Ghost",
            "next edge MS01.2_Bill -> MS09.1_Ghost names an unknown node",
        ),
    )


def test_e14_2_single_sub_skill_macro_without_edges_is_valid() -> None:
    dag = Dag(nodes=[{"id": "MS03.1_Only", "kind": "skill"}], edges=[])
    children = [_child("MS03.1_Only", "MS03_Solo")]
    assert dag_findings(dag) == ()
    assert macro_verdicts(children) == {"MS03_Solo": ()}
    assert workflow_verdict(dag, "", {}, children) == ()


def test_e14_2_t02_one_failing_sub_skill_blocks_its_macro_and_the_workflow_only() -> None:
    children = _children()
    children[1] = _child("MS01.2_Bill", "MS01_Order", certified=False)
    verdicts = macro_verdicts(children)
    failing = Finding(
        Check.SUB_SKILL_NOT_CERTIFIED, "MS01.2_Bill", "state is BUILD_ERROR, not CERTIFIED"
    )
    assert verdicts == {"MS01_Order": (failing,), "MS02_Ship": ()}
    assert workflow_verdict(_dag(GOOD_EDGES), SCHEMA, DECLARED, children) == (failing,)


def test_e14_2_certificate_on_an_old_bundle_does_not_count() -> None:
    stale = _child("MS02.1_Ship", "MS02_Ship", cert=f"sha256:{BUNDLE_B}")
    assert children_findings([stale]) == (
        Finding(
            Check.SUB_SKILL_NOT_CERTIFIED,
            "MS02.1_Ship",
            f"certificate is for sha256:{BUNDLE_B}, current bundle is sha256:{BUNDLE_A}",
        ),
    )
    no_cert = ChildSkill("MS02.1_Ship", "MS02_Ship", SubSkillState.CERTIFIED, None, BUNDLE_A)
    no_bundle = ChildSkill("MS02.1_Ship", "MS02_Ship", SubSkillState.CERTIFIED, "sha256:x", None)
    assert [f.message for f in children_findings([no_cert])] == ["no certificate on record"]
    assert [f.message for f in children_findings([no_bundle])] == ["no current code bundle"]


def test_e14_2_t06_schema_missing_table() -> None:
    declared = {**DECLARED, "MS02.1_Ship": ["handoff_ms02", "shipments"]}
    assert schema_findings(SCHEMA, declared) == (
        Finding(
            Check.SCHEMA_TABLE_MISSING,
            "MS02.1_Ship",
            "declared table shipments has no CREATE TABLE in schema.sql",
        ),
    )


def test_e14_2_invalid_schema_sql() -> None:
    sql = SCHEMA + "DROP TABLE orders;\nCREATE TABLE broken (id uuid;"
    findings = schema_findings(sql, DECLARED)
    assert [f.check for f in findings] == [Check.SCHEMA_INVALID, Check.SCHEMA_INVALID]
    assert [f.subject for f in findings] == ["schema.sql#3", "schema.sql#4"]
    assert findings[0].message.startswith("FORBIDDEN_STATEMENT:")
    assert findings[1].message.startswith("PARSE_ERROR:")


def test_e14_2_findings_are_deterministic() -> None:
    edges = [
        *GOOD_EDGES,
        ("MS01.2_Bill", "MS01.1_Intake", "data"),
        ("MS01.2_Bill", "MS09.1_Ghost", "next"),
        ("MS00_Nowhere", "MS01.1_Intake", "contains"),
    ]
    declared = {**DECLARED, "MS02.1_Ship": ["shipments", "returns"]}
    children = _children()
    children[0] = _child("MS01.1_Intake", "MS01_Order", certified=False)
    children[2] = _child("MS02.1_Ship", "MS02_Ship", cert=f"sha256:{BUNDLE_B}")
    expected = workflow_verdict(_dag(edges), SCHEMA, declared, children)
    assert len(expected) == 7
    assert list(expected) == sorted(expected)
    rng = random.Random(130)  # noqa: S311 - shuffling test input, not crypto
    for _ in range(20):
        rng.shuffle(edges)
        rng.shuffle(children)
        shuffled = dict(rng.sample(sorted(declared.items()), len(declared)))
        assert workflow_verdict(_dag(edges), SCHEMA, shuffled, children) == expected
        assert macro_verdicts(children) == macro_verdicts(reversed(children))


def test_e14_2_a_dag_skill_without_a_record_blocks_the_workflow() -> None:
    children = _children()[:2]  # MS02.1_Ship is in the DAG but has no record
    assert workflow_verdict(_dag(GOOD_EDGES), SCHEMA, DECLARED, children) == (
        Finding(Check.SUB_SKILL_NOT_CERTIFIED, "MS02.1_Ship", "no Sub-Skill record"),
    )


def test_e14_2_a_record_outside_the_dag_is_reported() -> None:
    children = [*_children(), _child("MS04.1_Stray", "MS04_Stray")]
    assert workflow_verdict(_dag(GOOD_EDGES), SCHEMA, DECLARED, children) == (
        Finding(Check.DAG_UNKNOWN_NODE, "MS04.1_Stray", "Sub-Skill not in the DAG"),
    )


def test_e14_2_an_empty_workflow_never_certifies() -> None:
    no_sub_skills = (
        Finding(Check.SUB_SKILL_NOT_CERTIFIED, "workflow", "the Workflow has no Sub-Skills"),
    )
    assert workflow_verdict(Dag(nodes=[], edges=[]), "", {}, []) == no_sub_skills
    macros_only = Dag(nodes=[{"id": "MS01_Order", "kind": "macro"}], edges=[])
    assert workflow_verdict(macros_only, "", {}, []) == no_sub_skills
