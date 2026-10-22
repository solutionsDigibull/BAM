"""Master context for Cognee (TD §7.3.1, issue E3.6): snapshot entries -> masters.jsonl nodes.

Pure and deterministic: no I/O, no clock (the caller passes `now`), sorted output, canonical JSON.

* Inclusion: ILLUSTRATIVE and RETIRED never; Restricted only with `allow_restricted`. Every pinned
  entry is either included or listed in `excluded` with its reason.
* A node per included entry, from its RAG Reference (Rules, Skills) where present:
    node_id   = ID Field, else the stable id; a duplicate gets `-<stable_id>` (+ warning)
    node_type = Entity / Node Type, else the master type (ORGANIZATION, RULE, AGENT, SKILL, TOOL)
    parent_id = the node named by Parent Entity; Organization rows are roots (None); an unknown
                parent also attaches to the root, with a warning.
* `Links / Path` stays text inside the payload: nothing is ever fetched or dereferenced.
* Refresh Frequency (Daily / Weekly / Monthly / Quarterly / Yearly) elapsed since the version was
  written -> `refresh_due`. Nothing is rebuilt automatically.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from sbam_contracts.masters import MasterStatus, MasterType
from sbam_contracts.masters_api import ExcludedEntry, ExclusionReason, MasterNode, SnapshotEntry

NODE_TYPE: Final = {
    MasterType.ORGANIZATION: "ORGANIZATION",
    MasterType.RULES: "RULE",
    MasterType.AGENTS: "AGENT",
    MasterType.SKILLS: "SKILL",
    MasterType.TOOLING: "TOOL",
    MasterType.FILETYPE: "FILETYPE",
}
REFRESH: Final = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
    "quarterly": timedelta(days=91),
    "yearly": timedelta(days=365),
    "annually": timedelta(days=365),
}
_RAG = "rag_reference"


@dataclass(frozen=True)
class Version:
    """The pinned version of an entry: its payload and when it was written."""

    payload: Mapping[str, Any]
    created_at: datetime


@dataclass
class MasterContext:
    jsonl: bytes
    nodes: list[MasterNode]
    excluded: list[ExcludedEntry] = field(default_factory=list)
    refresh_due: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _rag(payload: Mapping[str, Any], key: str) -> str:
    rag = payload.get(_RAG)
    value = rag.get(key, "") if isinstance(rag, Mapping) else ""
    return str(value).strip()


def _excluded(entry: SnapshotEntry, allow_restricted: bool) -> ExclusionReason | None:
    if entry.status is MasterStatus.ILLUSTRATIVE:
        return "ILLUSTRATIVE"
    if entry.status is MasterStatus.RETIRED:
        return "RETIRED"
    if entry.restricted and not allow_restricted:
        return "RESTRICTED"
    return None


def _refresh_due(payload: Mapping[str, Any], written: datetime, now: datetime) -> bool:
    period = REFRESH.get(_rag(payload, "Refresh Frequency").lower())
    return period is not None and now - written >= period


def build_master_context(
    entries: Sequence[SnapshotEntry],
    versions: Mapping[Any, Version],
    *,
    allow_restricted: bool,
    now: datetime,
) -> MasterContext:
    """`versions` is keyed by entry id; every entry of the snapshot must have one."""
    ctx = MasterContext(jsonl=b"", nodes=[])
    ordered = sorted(entries, key=lambda e: (e.master_type.value, e.stable_id, str(e.entry_id)))
    kept: list[tuple[SnapshotEntry, Version]] = []
    for entry in ordered:
        reason = _excluded(entry, allow_restricted)
        if reason is not None:
            ctx.excluded.append(
                ExcludedEntry(
                    stable_id=entry.stable_id,
                    master_type=entry.master_type,
                    reason=reason,
                )
            )
        else:
            kept.append((entry, versions[entry.entry_id]))

    # Node ids first (duplicates suffixed), so parents can be resolved against all of them.
    ids: dict[Any, str] = {}
    seen: set[str] = set()
    for entry, version in kept:
        node_id = _rag(version.payload, "ID Field") or entry.stable_id
        if node_id in seen:
            renamed = f"{node_id}-{entry.stable_id}"
            ctx.warnings.append(f"duplicate node id {node_id}: {entry.stable_id} uses {renamed}")
            node_id = renamed
        seen.add(node_id)
        ids[entry.entry_id] = node_id

    for entry, version in kept:
        parent: str | None = None
        if entry.master_type is not MasterType.ORGANIZATION:
            named = _rag(version.payload, "Parent Entity")
            if named and named in seen:
                parent = named
            elif named:
                ctx.warnings.append(
                    f"{entry.stable_id}: unknown parent {named}; attached to the graph root"
                )
        ctx.nodes.append(
            MasterNode(
                node_id=ids[entry.entry_id],
                node_type=_rag(version.payload, "Entity / Node Type")
                or NODE_TYPE[entry.master_type],
                master_type=entry.master_type,
                stable_id=entry.stable_id,
                entry_id=entry.entry_id,
                version=entry.version,
                parent_id=parent,
                payload=dict(version.payload),
            )
        )
        if _refresh_due(version.payload, version.created_at, now):
            ctx.refresh_due.append(entry.stable_id)

    ctx.nodes.sort(key=lambda n: n.node_id)
    lines = [
        json.dumps(
            n.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        for n in ctx.nodes
    ]
    ctx.jsonl = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    return ctx
