"""Macro Skill and Workflow validation (E14.2): what must hold before an Agent or a Workflow
certifies, beyond each Sub-Skill's own certificate (E14.1).

Every check is a pure function of what the certify activity loaded, so every rule is a unit test:

  * `dag_findings`: every edge joins known nodes (DAG_UNKNOWN_NODE) and the edges never loop
    (DAG_CYCLE, the subject is the path);
  * `schema_findings`: the Workflow's schema.sql parses and passes the allowlist (SCHEMA_INVALID)
    and has a CREATE TABLE for every table a Sub-Skill declares (SCHEMA_TABLE_MISSING);
  * `children_findings`: every Sub-Skill is certified on the bundle it has now
    (SUB_SKILL_NOT_CERTIFIED): a certificate on an older bundle does not count.

A failing Sub-Skill blocks its Macro and the Workflow, never another Macro (`macro_verdicts`).
Findings are sorted, so the same inputs in any order give the same tuple. They name ids, tables
and hashes, never code or SQL text.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

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


def _sorted(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(set(findings)))


# --- DAG ------------------------------------------------------------------------------------------


def dag_findings(dag: DagLike) -> tuple[Finding, ...]:
    """Unknown edge endpoints, then one cycle (if any) as `a -> b -> a`."""
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
        if not unknown:
            adjacency.setdefault(ends[0], set()).add(ends[1])
    cycle = find_cycle({node: sorted(targets) for node, targets in adjacency.items()})
    if cycle:
        path = " -> ".join(cycle)
        findings.append(Finding(Check.DAG_CYCLE, path, f"the Workflow DAG loops: {path}"))
    return _sorted(findings)


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
) -> tuple[Finding, ...]:
    """Every finding that blocks the Workflow; empty: it may certify. It never certifies
    vacuously: every skill node of the DAG needs a Sub-Skill record, every record a DAG node,
    and a Workflow with no Sub-Skills at all is blocked."""
    children = list(skills)
    return _sorted(
        [
            *dag_findings(dag),
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
