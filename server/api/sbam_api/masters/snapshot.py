"""Which master entries a project's snapshot pins (E3.2). Pure: rows in, pinned entries out.

* Scope: the tenant's entries plus the project's own; a project entry overrides the tenant entry
  with the same type and stable id. Entries of other projects never reach here (the repository
  filters them).
* Every chosen entry is pinned with its status and whether it is Restricted (Security / Access
  Level). Consumers decide inclusion (E3.6): ILLUSTRATIVE (sample rows) and RETIRED never reach an
  authoritative context, Restricted only when the project allows it. So a report can account for
  every pinned entry. Old snapshots keep whatever they pinned.
* The hash is `sha256_of(snapshot_document(...))`: the same entries and versions always hash the
  same, whatever order the rows came in.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sbam_contracts.canonical import sha256_of
from sbam_contracts.masters import MasterStatus, MasterType, is_restricted
from sbam_contracts.masters_api import SnapshotEntry, snapshot_document
from sbam_contracts.masters_workbook import PAYLOAD_MODELS

# Never part of an authoritative context, whoever consumes the snapshot (O-24).
EXCLUDED = frozenset({MasterStatus.ILLUSTRATIVE.value, MasterStatus.RETIRED.value})


@dataclass(frozen=True)
class Candidate:
    """One live entry and its current payload."""

    entry_id: uuid.UUID
    project_id: uuid.UUID | None
    master_type: str
    stable_id: str
    status: str
    version: int
    payload: Mapping[str, object]


def _restricted(candidate: Candidate) -> bool:
    model = PAYLOAD_MODELS[MasterType(candidate.master_type)]
    return is_restricted(model.model_validate(candidate.payload))


def resolve(project_id: uuid.UUID, candidates: Iterable[Candidate]) -> list[SnapshotEntry]:
    chosen: dict[tuple[str, str], Candidate] = {}
    for c in candidates:
        if c.project_id not in (None, project_id):
            continue
        key = (c.master_type, c.stable_id)
        current = chosen.get(key)
        if current is None or (current.project_id is None and c.project_id == project_id):
            chosen[key] = c  # the project's own entry wins over the tenant's
    pinned = [
        SnapshotEntry(
            entry_id=c.entry_id,
            master_type=MasterType(c.master_type),
            stable_id=c.stable_id,
            version=c.version,
            status=MasterStatus(c.status),
            restricted=_restricted(c),
        )
        for c in chosen.values()
    ]
    return sorted(pinned, key=lambda e: (e.master_type.value, e.stable_id))


def snapshot_hash(project_id: uuid.UUID, entries: list[SnapshotEntry]) -> str:
    return sha256_of(snapshot_document(project_id, entries))
