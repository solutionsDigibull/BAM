"""The Workflow DAG (E9.5, TD §11.3): built by the platform from the stored ISATVON, never from LLM
free text.

Nodes are the Agents (Macro Skills) and their Sub-Skills. Edges:

  * contains: Agent -> each of its Sub-Skills;
  * next: a Sub-Skill's `n_notification.next` (the next Sub-Skill, or the next Agent);
  * data: upstream -> downstream when the downstream's `s_source.required_inputs` names a field of
    the upstream's `o_outcome` output. An input no other Sub-Skill outputs comes from outside (an
    external S source) and gets no edge.

The data edges are the build dependencies (`plan_builds`, E10.3) and must not loop: a cycle is
`DraftInvalid` with code DAG_CYCLE and the path. Everything is sorted by id, so the same Workflow
gives the same DAG whatever order the database returns its rows in.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sbam_contracts.api import ErrorCode
from sbam_contracts.isatvon14 import output_fields

KINDS = {ErrorCode.DAG_CYCLE: "DagCycle", ErrorCode.SCHEMA_SQL_MISSING: "SchemaSqlMissing"}

class DraftInvalid(Exception):  # noqa: N818 - named like the other stage failures
    """The Workflow can't get a manifest draft; `code` is the stable ErrorCode, `kind` the
    non-retryable ApplicationError type the activity raises."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.kind = KINDS.get(code, "WorkflowManifestInvalid")


@dataclass(frozen=True)
class Node:
    sub_skill_id: uuid.UUID
    skill_id: str
    macro_category: str
    isatvon: Mapping[str, Any]  # the stored ISATVON 1.4 Sub-Skill


@dataclass(frozen=True)
class Graph:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    depends_on: dict[uuid.UUID, list[uuid.UUID]]  # data edges: Sub-Skill -> its upstreams


def _definition(node: Node) -> Mapping[str, Any]:
    definition = node.isatvon.get("isatvon_definition")
    return definition if isinstance(definition, Mapping) else {}


def _outputs(node: Node) -> set[str]:
    schema = _definition(node).get("o_outcome", {}).get("json_schema")
    return set(output_fields(schema)) if isinstance(schema, Mapping) else set()


def _inputs(node: Node) -> list[str]:
    inputs = _definition(node).get("s_source", {}).get("required_inputs")
    return [i for i in inputs if isinstance(i, str)] if isinstance(inputs, list) else []


def _next(node: Node) -> str | None:
    value = _definition(node).get("n_notification", {}).get("next")
    return value if isinstance(value, str) and value else None


def data_links(nodes: Sequence[Node]) -> list[tuple[Node, Node, str]]:
    """(upstream, downstream, field) for each input a Sub-Skill takes from another's output, in
    skill_id and field order. Also the build dependencies `plan_builds` schedules by."""
    ordered = sorted(nodes, key=lambda n: n.skill_id)
    outputs = {n.skill_id: _outputs(n) for n in ordered}
    return [
        (up, down, field)
        for down in ordered
        for field in sorted(set(_inputs(down)))
        for up in ordered
        if up.skill_id != down.skill_id and field in outputs[up.skill_id]
    ]


def build_dag(nodes: Sequence[Node]) -> Graph:
    by_skill = {n.skill_id: n for n in nodes}
    if len(by_skill) != len(nodes):
        raise DraftInvalid(ErrorCode.ISATVON_CONTRACT, "two Sub-Skills share a skill_id")
    ordered = sorted(nodes, key=lambda n: n.skill_id)
    macros = sorted({n.macro_category for n in nodes})
    edges: list[dict[str, Any]] = [
        {"from": n.macro_category, "to": n.skill_id, "kind": "contains"} for n in ordered
    ]
    for n in ordered:
        target = _next(n)
        if target is None:
            continue
        if target == n.skill_id:
            raise DraftInvalid(ErrorCode.DAG_CYCLE, f"{n.skill_id}: next is itself")
        if target not in by_skill and target not in macros:
            raise DraftInvalid(ErrorCode.ISATVON_CONTRACT, f"{n.skill_id}: unknown next {target!r}")
        edges.append({"from": n.skill_id, "to": target, "kind": "next"})
    links = data_links(ordered)
    edges += [
        {"from": u.skill_id, "to": d.skill_id, "kind": "data", "field": f} for u, d, f in links
    ]
    depends_on: dict[uuid.UUID, list[uuid.UUID]] = {}
    for up, down, _ in links:
        ups = depends_on.setdefault(down.sub_skill_id, [])
        if up.sub_skill_id not in ups:
            ups.append(up.sub_skill_id)
    cycle = find_cycle(depends_on)
    if cycle:
        # find_cycle follows downstream -> upstream; the path reads in data-flow order reversed
        by_id = {n.sub_skill_id: n.skill_id for n in nodes}
        path = " -> ".join(by_id[s] for s in reversed(cycle))
        raise DraftInvalid(ErrorCode.DAG_CYCLE, f"Sub-Skill dependencies loop: {path}")
    skills = [
        {"id": n.skill_id, "kind": "skill", "macro_category": n.macro_category} for n in ordered
    ]
    return Graph(
        nodes=[{"id": m, "kind": "macro"} for m in macros] + skills,
        edges=edges,
        depends_on=depends_on,
    )


def find_cycle(edges: Mapping[uuid.UUID, list[uuid.UUID]]) -> list[uuid.UUID]:
    """One cycle as a path (first id repeated at the end), or [] when the graph is acyclic."""
    state: dict[uuid.UUID, int] = {}  # 1 = on the current path, 2 = finished
    for root in sorted(edges, key=str):
        if root in state:
            continue
        path: list[uuid.UUID] = [root]
        stack = [iter(edges.get(root, []))]
        state[root] = 1
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                state[path.pop()] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                return [*path[path.index(nxt) :], nxt]
            elif nxt not in state:
                state[nxt] = 1
                path.append(nxt)
                stack.append(iter(edges.get(nxt, [])))
    return []
