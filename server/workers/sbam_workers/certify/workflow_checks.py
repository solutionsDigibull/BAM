"""Macro Skill and Workflow validation (E14.2): what must hold before an Agent or a Workflow
certifies, beyond each Sub-Skill's own certificate (E14.1).

Every check is a pure function of what the certify activity loaded, so every rule is a unit test:

  * `dag_findings`: every edge joins known nodes (DAG_UNKNOWN_NODE) and the data edges never loop
    (DAG_CYCLE, the subject is the path). Only `data` edges count for a cycle, as in
    `build.dag.build_dag`: they are the build dependencies. A `next` may loop or name the skill's
    own Macro (with `contains` that would look like a cycle), and the build accepts it, so the
    certification is not stricter than the build;
  * `schema_findings`: the Workflow's schema.sql parses and passes the allowlist (SCHEMA_INVALID)
    and has a CREATE TABLE for every table a Sub-Skill declares (SCHEMA_TABLE_MISSING);
  * `children_findings`: every Sub-Skill is certified on the bundle it has now
    (SUB_SKILL_NOT_CERTIFIED): a certificate on an older bundle does not count;
  * `interface_findings`: across every field-level data edge, the upstream's
    `contracts.output_schema` property and the downstream's `contracts.input_schema` property
    (`build_contracts`) have compatible types: equal, or integer into number
    (INTERFACE_INCOMPATIBLE). An output the upstream marks optional (its output schema has a
    `required` list without the field) into a required input is one too. `build_contracts` never
    writes `required` on an output schema, so today every output counts as present: optionality
    is judged only where the schema states it, never assumed. A data edge with no `field` (not
    from `build_dag`) links nothing to compare.

A failing Sub-Skill blocks its Macro and the Workflow, never another Macro (`macro_verdicts`).
Findings are sorted, so the same inputs in any order give the same tuple. They name ids, tables
and hashes, never code or SQL text.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Protocol

from sbam_api.schema_sql.validate import check
from sbam_contracts.state_machines import SubSkillState

from sbam_workers.build.dag import find_cycle

# A FROZEN Sub-Skill was certified and then frozen with its Workflow; it is still certified.
CERTIFIED_STATES: Final = frozenset({SubSkillState.CERTIFIED, SubSkillState.FROZEN})
SCHEMA_SUBJECT: Final = "schema.sql"


class Check(StrEnum):
    DAG_CYCLE = "DAG_CYCLE"
    DAG_UNKNOWN_NODE = "DAG_UNKNOWN_NODE"
    SCHEMA_TABLE_MISSING = "SCHEMA_TABLE_MISSING"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    SUB_SKILL_NOT_CERTIFIED = "SUB_SKILL_NOT_CERTIFIED"
    INTERFACE_INCOMPATIBLE = "INTERFACE_INCOMPATIBLE"


@dataclass(frozen=True, order=True)
class Finding:
    check: str
    subject: str
    message: str


class DagLike(Protocol):
    """`sbam_contracts.manifest.Dag` or `sbam_workers.build.dag.Graph`."""

    @property
    def nodes(self) -> Sequence[Mapping[str, object]]: ...

    @property
    def edges(self) -> Sequence[Mapping[str, object]]: ...


@dataclass(frozen=True)
class ChildSkill:
    """One Sub-Skill of a Macro as the activity loaded it."""

    skill_id: str
    macro: str  # its macro_category
    state: str  # SubSkillState
    certified_content_sha256: str | None  # "sha256:<hex>" of its latest certificate, or None
    bundle_sha256: str | None  # hex of its current code bundle, or None before codegen


@dataclass(frozen=True)
class Interface:
    """One Sub-Skill's boundary as its stored ISATVON declares it."""

    # `build_contracts`: {"input_schema": ..., "output_schema": ...}; None when the stored
    # ISATVON can't give them (a data edge to or from it is then a finding)
    contracts: Mapping[str, Any] | None


NO_INTERFACES: Final[Mapping[str, Interface]] = MappingProxyType({})
# The one widening a link may make (TD §14.5): every integer is a number, not the other way.
_WIDENS: Final = frozenset({("integer", "number")})


def _sorted(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(set(findings)))


# --- DAG ------------------------------------------------------------------------------------------


def dag_findings(dag: DagLike) -> tuple[Finding, ...]:
    """Unknown edge endpoints (any edge kind), then one cycle over the data edges (if any) as
    `a -> b -> a`; `contains` and `next` edges never make a cycle (see the module docstring)."""
    known = {str(node.get("id")) for node in dag.nodes}
    findings: list[Finding] = []
    adjacency: dict[str, set[str]] = {}
    for edge in dag.edges:
        ends = (str(edge.get("from")), str(edge.get("to")))
        unknown = [end for end in ends if end not in known]
        for end in unknown:
            findings.append(
                Finding(
                    Check.DAG_UNKNOWN_NODE,
                    end,
                    f"{edge.get('kind', 'edge')} edge {ends[0]} -> {ends[1]} names an unknown node",
                )
            )
        if not unknown and edge.get("kind") == "data":
            adjacency.setdefault(ends[0], set()).add(ends[1])
    cycle = find_cycle({node: sorted(targets) for node, targets in adjacency.items()})
    if cycle:
        path = " -> ".join(cycle)
        findings.append(Finding(Check.DAG_CYCLE, path, f"the Workflow DAG loops: {path}"))
    return _sorted(findings)


# --- interfaces -----------------------------------------------------------------------------------


def _field_links(dag: DagLike) -> list[tuple[str, str, str]]:
    """(upstream, downstream, field) of every data edge that names its field, sorted."""
    return sorted(
        {
            (str(e.get("from")), str(e.get("to")), field)
            for e in dag.edges
            if e.get("kind") == "data" and isinstance(field := e.get("field"), str)
        }
    )


def _schema(interface: Interface | None, name: str) -> Mapping[str, Any] | None:
    if interface is None or interface.contracts is None:
        return None
    schema = interface.contracts.get(name)
    return schema if isinstance(schema, Mapping) else None


def _property(schema: Mapping[str, Any], field: str) -> Mapping[str, Any] | None:
    properties = schema.get("properties")
    spec = properties.get(field) if isinstance(properties, Mapping) else None
    return spec if isinstance(spec, Mapping) else None


def _required(schema: Mapping[str, Any]) -> list[str] | None:
    required = schema.get("required")
    return [str(r) for r in required] if isinstance(required, list) else None


def _link_findings(
    up: str, down: str, field: str, interfaces: Mapping[str, Interface]
) -> list[Finding]:
    output = _schema(interfaces.get(up), "output_schema")
    inp = _schema(interfaces.get(down), "input_schema")
    missing = [skill for skill, schema in ((up, output), (down, inp)) if schema is None]
    if output is None or inp is None:
        names = " and ".join(missing)
        return [
            Finding(
                Check.INTERFACE_INCOMPATIBLE,
                down,
                f"{up} -> {down}: no interface contract for {names}",
            )
        ]
    out_spec, in_spec = _property(output, field), _property(inp, field)
    if out_spec is None or in_spec is None:
        sides = [
            f"the {side} of {skill}"
            for skill, side, spec in (
                (up, "output_schema", out_spec),
                (down, "input_schema", in_spec),
            )
            if spec is None
        ]
        return [
            Finding(
                Check.INTERFACE_INCOMPATIBLE,
                down,
                f"{up} -> {down}: {field} is not in " + " and not in ".join(sides),
            )
        ]
    out_type, in_type = out_spec.get("type"), in_spec.get("type")
    findings = []
    if out_type != in_type and (out_type, in_type) not in _WIDENS:
        findings.append(
            Finding(
                Check.INTERFACE_INCOMPATIBLE,
                down,
                f"{up} outputs {field} as {out_type}; {down} takes {field} as {in_type}",
            )
        )
    produced, needed = _required(output), _required(inp) or []
    if produced is not None and field not in produced and field in needed:
        findings.append(
            Finding(
                Check.INTERFACE_INCOMPATIBLE,
                down,
                f"{up} output {field} is optional; {down} input {field} is required",
            )
        )
    return findings


def interface_findings(dag: DagLike, interfaces: Mapping[str, Interface]) -> tuple[Finding, ...]:
    """`interfaces` is skill_id -> its `Interface`; see the module docstring for the rules."""
    return _sorted(
        finding
        for up, down, field in _field_links(dag)
        for finding in _link_findings(up, down, field, interfaces)
    )


# --- schema.sql -----------------------------------------------------------------------------------


def _table_key(name: str) -> str:
    """Compared the way `build.schema_sql.merge` does: unqualified, unquoted, lower-case."""
    return name.strip().rsplit(".", 1)[-1].strip().strip('"').lower()


def schema_findings(
    schema_sql: str, declared_tables: Mapping[str, Iterable[str]]
) -> tuple[Finding, ...]:
    """`declared_tables` is skill_id -> the tables that Sub-Skill declares (`merge.declared`)."""
    script = check(schema_sql)
    findings = [
        Finding(
            Check.SCHEMA_INVALID,
            (
                SCHEMA_SUBJECT
                if problem.statement_index is None
                else f"{SCHEMA_SUBJECT}#{problem.statement_index}"
            ),
            f"{problem.code}: {problem.message}",
        )
        for problem in script.problems
    ]
    created = {_table_key(table) for table in script.tables()}
    for skill_id, tables in declared_tables.items():
        for table in {_table_key(t) for t in tables} - created:
            findings.append(
                Finding(
                    Check.SCHEMA_TABLE_MISSING,
                    skill_id,
                    f"declared table {table} has no CREATE TABLE in schema.sql",
                )
            )
    return _sorted(findings)


# --- Sub-Skill certificates -----------------------------------------------------------------------


def _expected_hash(bundle_sha256: str) -> str:
    return bundle_sha256 if bundle_sha256.startswith("sha256:") else f"sha256:{bundle_sha256}"


def _child_finding(child: ChildSkill) -> Finding | None:
    if child.state not in CERTIFIED_STATES:
        return Finding(
            Check.SUB_SKILL_NOT_CERTIFIED, child.skill_id, f"state is {child.state}, not CERTIFIED"
        )
    if child.certified_content_sha256 is None:
        return Finding(Check.SUB_SKILL_NOT_CERTIFIED, child.skill_id, "no certificate on record")
    if child.bundle_sha256 is None:
        return Finding(Check.SUB_SKILL_NOT_CERTIFIED, child.skill_id, "no current code bundle")
    if child.certified_content_sha256 != _expected_hash(child.bundle_sha256):
        return Finding(
            Check.SUB_SKILL_NOT_CERTIFIED,
            child.skill_id,
            f"certificate is for {child.certified_content_sha256}, current bundle is "
            f"{_expected_hash(child.bundle_sha256)}",
        )
    return None


def children_findings(skills: Iterable[ChildSkill]) -> tuple[Finding, ...]:
    return _sorted(f for f in map(_child_finding, skills) if f is not None)


def macro_verdicts(skills: Iterable[ChildSkill]) -> dict[str, tuple[Finding, ...]]:
    """Macro -> the findings that block it (empty: it may certify). A Sub-Skill only ever blocks
    its own Macro."""
    by_macro: dict[str, list[ChildSkill]] = {}
    for skill in skills:
        by_macro.setdefault(skill.macro, []).append(skill)
    return {macro: children_findings(by_macro[macro]) for macro in sorted(by_macro)}


def workflow_verdict(
    dag: DagLike,
    schema_sql: str,
    declared: Mapping[str, Iterable[str]],
    skills: Iterable[ChildSkill],
    *,
    interfaces: Mapping[str, Interface] = NO_INTERFACES,
) -> tuple[Finding, ...]:
    """Every finding that blocks the Workflow; empty: it may certify. It never certifies
    vacuously: every skill node of the DAG needs a Sub-Skill record, every record a DAG node,
    a Workflow with no Sub-Skills at all is blocked, and a field-level data edge between skills
    with no `interfaces` is a finding."""
    children = list(skills)
    return _sorted(
        [
            *dag_findings(dag),
            *interface_findings(dag, interfaces),
            *schema_findings(schema_sql, declared),
            *children_findings(children),
            *_coverage_findings(dag, children),
        ]
    )


def _coverage_findings(dag: DagLike, children: Sequence[ChildSkill]) -> list[Finding]:
    nodes = {str(n.get("id")) for n in dag.nodes if n.get("kind") == "skill"}
    records = {c.skill_id for c in children}
    if not nodes and not records:
        return [
            Finding(Check.SUB_SKILL_NOT_CERTIFIED, "workflow", "the Workflow has no Sub-Skills")
        ]
    return [
        Finding(Check.SUB_SKILL_NOT_CERTIFIED, s, "no Sub-Skill record") for s in nodes - records
    ] + [Finding(Check.DAG_UNKNOWN_NODE, s, "Sub-Skill not in the DAG") for s in records - nodes]
