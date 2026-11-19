"""E14.2 (#130): pure Macro and Workflow validators."""

import json
import random
import uuid
from pathlib import Path
from typing import Any

import pytest
from sbam_contracts.isatvon14 import SubSkill, build_contracts
from sbam_contracts.manifest import Dag
from sbam_contracts.state_machines import SubSkillState
from sbam_workers.build.dag import find_cycle
from sbam_workers.certify.workflow_checks import (
    Check,
    ChildSkill,
    Finding,
    Interface,
    children_findings,
    dag_findings,
    dangling_findings,
    interface_findings,
    macro_verdicts,
    schema_findings,
    workflow_verdict,
)

ISA14 = Path(__file__).parents[1] / "fixtures/isa14/ms01_1_structured.json"

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


# --- interfaces (T05) -----------------------------------------------------------------------------


def _iface(
    inputs: dict[str, str], outputs: dict[str, str], *, output_required: list[str] | None = None
) -> Interface:
    """Contracts shaped like `build_contracts`; `output_required` only when the test sets it."""
    output: dict[str, Any] = {
        "type": "object",
        "properties": {k: {"type": t} for k, t in outputs.items()},
    }
    if output_required is not None:
        output["required"] = output_required
    return Interface(
        {
            "input_schema": {
                "type": "object",
                "properties": {k: {"type": t} for k, t in inputs.items()},
                "required": list(inputs),
            },
            "output_schema": output,
        }
    )


def _linked(field: str = "amount") -> Dag:
    """GOOD_EDGES plus a field-level data edge MS01.1_Intake -> MS01.2_Bill (as build_dag)."""
    dag = _dag(GOOD_EDGES)
    edge = {"from": "MS01.1_Intake", "to": "MS01.2_Bill", "kind": "data", "field": field}
    return Dag(nodes=dag.nodes, edges=[*dag.edges, edge])  # type: ignore[list-item]


def _ifaces(amount_out: str, amount_in: str, **out: Any) -> dict[str, Interface]:
    return {
        "MS01.1_Intake": _iface({"email": "string"}, {"amount": amount_out}, **out),
        "MS01.2_Bill": _iface({"amount": amount_in}, {"invoice_id": "string"}),
        "MS02.1_Ship": _iface({"invoice_id": "string"}, {"tracking": "string"}),
    }


def test_e14_2_t05_interface_mismatch_string_into_number() -> None:
    assert interface_findings(_linked(), _ifaces("string", "number")) == (
        Finding(
            Check.INTERFACE_INCOMPATIBLE,
            "MS01.2_Bill",
            "MS01.1_Intake outputs amount as string; MS01.2_Bill takes amount as number",
        ),
    )
    verdict = workflow_verdict(
        _linked(), SCHEMA, DECLARED, _children(), interfaces=_ifaces("string", "number")
    )
    assert [f.check for f in verdict] == [Check.INTERFACE_INCOMPATIBLE]


@pytest.mark.parametrize(
    ("out", "into", "ok"),
    [
        ("string", "string", True),
        ("integer", "number", True),  # the one widening the spec allows
        ("number", "number", True),
        ("number", "integer", False),  # narrowing loses data
        ("boolean", "string", False),
        ("object", "array", False),
    ],
)
def test_e14_2_t05_compatible_types(out: str, into: str, ok: bool) -> None:
    findings = interface_findings(_linked(), _ifaces(out, into))
    assert (findings == ()) is ok, findings


def test_e14_2_t05_optional_output_into_a_required_input() -> None:
    """Edge case: the upstream output schema says the field may be absent, the downstream needs it.
    `build_contracts` never writes `required` on an output schema, so today every output counts as
    present and this fires only for an output schema that declares `required`."""
    optional = _ifaces("number", "number", output_required=["state_handoff_id"])
    assert interface_findings(_linked(), optional) == (
        Finding(
            Check.INTERFACE_INCOMPATIBLE,
            "MS01.2_Bill",
            "MS01.1_Intake output amount is optional; MS01.2_Bill input amount is required",
        ),
    )
    required = _ifaces("number", "number", output_required=["amount"])
    assert interface_findings(_linked(), required) == ()
    assert interface_findings(_linked(), _ifaces("number", "number")) == ()  # no `required`


def test_e14_2_t05_a_linked_field_or_contract_missing_is_a_finding() -> None:
    ifaces = _ifaces("string", "string")
    assert [f.message for f in interface_findings(_linked("total"), ifaces)] == [
        "MS01.1_Intake -> MS01.2_Bill: total is not in the output_schema of MS01.1_Intake "
        "and not in the input_schema of MS01.2_Bill"
    ]
    del ifaces["MS01.1_Intake"]
    assert [f.message for f in interface_findings(_linked(), ifaces)] == [
        "MS01.1_Intake -> MS01.2_Bill: no interface contract for MS01.1_Intake"
    ]
    # the activity always passes the interfaces: none at all is not a pass
    assert [f.check for f in workflow_verdict(_linked(), SCHEMA, DECLARED, _children())] == [
        Check.INTERFACE_INCOMPATIBLE
    ]


def test_e14_2_t05_reference_contracts_from_build_contracts_link_cleanly() -> None:
    """MS01.1's real contracts (`build_contracts` on the reference fixture) feed a Sub-Skill that
    takes `auth_status` (an enum, so a string) and `otp_match` (a boolean)."""
    sub = json.loads(ISA14.read_text("utf-8"))
    upstream = Interface(build_contracts(SubSkill.model_validate(sub)))
    downstream = _iface({"auth_status": "string", "otp_match": "boolean"}, {"kyc": "string"})
    nodes = [{"id": s, "kind": "skill"} for s in ("MS01.1_Investor_Auth_Manager", "MS01.2_Kyc")]
    edges = [
        {"from": "MS01.1_Investor_Auth_Manager", "to": "MS01.2_Kyc", "kind": "data", "field": f}
        for f in ("auth_status", "otp_match")
    ]
    dag = Dag(nodes=nodes, edges=edges)  # type: ignore[arg-type]
    ifaces = {"MS01.1_Investor_Auth_Manager": upstream, "MS01.2_Kyc": downstream}
    assert interface_findings(dag, ifaces) == ()
    downstream = _iface({"auth_status": "integer", "otp_match": "boolean"}, {"kyc": "string"})
    ifaces["MS01.2_Kyc"] = downstream
    assert [f.message for f in interface_findings(dag, ifaces)] == [
        "MS01.1_Investor_Auth_Manager outputs auth_status as string; "
        "MS01.2_Kyc takes auth_status as integer"
    ]


# --- dangling inputs (T04, option B on #130) ------------------------------------------------------


def _source(inputs: list[str], external: list[str] | None = None) -> Interface:
    return Interface(None, inputs=tuple(inputs), external=frozenset(external or ()))


def _chain(*extra: dict[str, Any]) -> Dag:
    """GOOD_EDGES (MS01.1 -next-> MS01.2 -next-> MS02_Ship) plus `extra` edges."""
    dag = _dag(GOOD_EDGES)
    return Dag(nodes=dag.nodes, edges=[*dag.edges, *extra])


DANGLING = "required input account_id has no upstream output and is not in s_source.external_data"


def test_e14_2_t04_dangling_input() -> None:
    sources = {
        "MS01.1_Intake": _source(["email"]),
        "MS01.2_Bill": _source(["account_id"]),
        "MS02.1_Ship": _source(["invoice_id"]),
    }
    assert dangling_findings(_chain(), sources) == (
        Finding(Check.DAG_DANGLING_INPUT, "MS01.2_Bill", DANGLING),
    )
    verdict = workflow_verdict(_chain(), SCHEMA, DECLARED, _children(), interfaces=sources)
    assert [f.check for f in verdict] == [Check.DAG_DANGLING_INPUT]


def test_e14_2_t04_an_upstream_output_or_external_data_provides_the_input() -> None:
    linked = {"from": "MS01.1_Intake", "to": "MS01.2_Bill", "kind": "data", "field": "account_id"}
    assert dangling_findings(_chain(linked), {"MS01.2_Bill": _source(["account_id"])}) == ()
    external = {"MS01.2_Bill": _source(["account_id"], external=["account_id"])}
    assert dangling_findings(_chain(), external) == ()
    # a data edge for another field does not provide this one
    other = {**linked, "field": "email"}
    assert dangling_findings(_chain(other), {"MS01.2_Bill": _source(["account_id"])}) == (
        Finding(Check.DAG_DANGLING_INPUT, "MS01.2_Bill", DANGLING),
    )


def test_e14_2_t04_entry_points_take_workflow_inputs() -> None:
    """No Sub-Skill `next` leads to MS01.1 (the Workflow's entry) nor to MS02.1: a `next` naming a
    Macro (MS01.2 -> MS02_Ship) hands over to the next Agent, it is not an edge into one of its
    Sub-Skills, so MS02.1's inputs are inputs of that Agent."""
    sources = {
        "MS01.1_Intake": _source(["account_id"]),
        "MS02.1_Ship": _source(["account_id"]),
    }
    assert dangling_findings(_chain(), sources) == ()


def test_e14_2_t04_the_reference_and_existing_multi_skill_fixtures_have_no_dangling_input() -> None:
    """MS01.1 (tests/fixtures/isa14) declares no external_data for its entry inputs; the E9.5 and
    E14.2 multi-skill DAGs take `rfq_file` / `email` from outside. None of them dangles."""
    from sbam_workers.build.dag import Node, build_dag
    from sbam_workers.certify.workflow import interface_of
    from test_e9_5_workflow_manifest import three

    auth = json.loads(ISA14.read_text("utf-8"))
    nxt = auth["isatvon_definition"]["n_notification"]["next"]  # MS01.2_Investor_KYC_Verifier
    kyc = json.loads(ISA14.read_text("utf-8"))  # its next Sub-Skill takes two of its outputs
    kyc["skill_metadata"]["skill_id"] = nxt
    kyc["isatvon_definition"]["s_source"]["required_inputs"] = ["auth_status", "user_role"]
    kyc["isatvon_definition"]["n_notification"]["next"] = None
    macro = auth["skill_metadata"]["macro_category"]
    pair = [Node(uuid.uuid4(), s["skill_metadata"]["skill_id"], macro, s) for s in (auth, kyc)]
    graph = build_dag(pair)
    sources = {n.skill_id: interface_of(n.isatvon) for n in pair}
    assert "external_data" not in auth["isatvon_definition"]["s_source"]
    assert dangling_findings(graph, sources) == ()
    assert interface_findings(graph, sources) == ()
    kyc["isatvon_definition"]["s_source"]["required_inputs"].append("account_id")
    assert [f.subject for f in dangling_findings(graph, {nxt: interface_of(kyc)})] == [nxt]

    nodes = [s.node for s in three()]
    graph = build_dag(nodes)
    sources = {n.skill_id: interface_of(n.isatvon) for n in nodes}
    assert sources["MS02.1_Quote"].inputs == ("investor_id", "rfq_file")
    assert dangling_findings(graph, sources) == ()
