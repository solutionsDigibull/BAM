"""Master-context input for spike E0.9, shaped like the real Admin contract.

Contracts mirrored here (so the real Admin call can replace the mock without touching the mapper):
  * E3.2 (#55): MasterSnapshot = {id, project_id, entries[{entry_id, version}], sha256}; each entry has
    stable_id, status (parsed from Misc), row_label and a payload keyed by the BAM 0.2 columns.
  * E3.6 (#59): to_master_context() turns a snapshot into masters.jsonl rows
    {node_id, node_type, master_type, payload, parent_id} plus an inclusion report.

Only the *source* is mocked: WorkbookMockSource reads the repo workbook. AdminApiSource is the seam for the
real call. Nothing below the source depends on where the snapshot came from.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

MASTER_TABS = {"ORGANIZATION": "Organization", "RULES": "Rules", "AGENTS": "Agents", "SKILLS": "Skills"}
NODE_TYPE_DEFAULT = {"ORGANIZATION": "Organization", "RULES": "RULE", "AGENTS": "AGENT", "SKILLS": "SKILL"}
EXCLUDED_STATUSES = {"ILLUSTRATIVE", "RETIRED"}  # E3.6 safety: never reach Cognee
DEFAULT_STATUS = "PROPOSED"  # MSC: content is Proposed; the first row of each tab (BAM 0.2 sample) has no status

_STATUS_RE = re.compile(r"Status:\s*([A-Za-z]+)\s*$", re.IGNORECASE)
_STABLE_ID_RULES = {
    "RULES": ("Domain Rule", re.compile(r"^(RULE-[A-Z0-9-]+)\s*:")),
    "AGENTS": ("Description", re.compile(r"^(AG-[A-Z0-9-]+)\b")),
    "SKILLS": ("Skill", re.compile(r"^\s*(SK-[A-Z0-9-]+)\s*:")),
}


@dataclass(frozen=True)
class MasterEntry:
    entry_id: str
    version: int
    master_type: str
    row_label: str
    stable_id: str
    status: str
    payload: dict[str, str]


@dataclass(frozen=True)
class MasterSnapshot:
    id: str
    tenant_id: str
    project_id: str
    entries: tuple[MasterEntry, ...]

    def ref(self) -> dict[str, Any]:
        """The E3.2 contract shape: {id, project_id, entries[{entry_id, version}], sha256}."""
        refs = [{"entry_id": e.entry_id, "version": e.version} for e in sorted(self.entries, key=lambda e: e.entry_id)]
        body = {"project_id": self.project_id, "entries": refs}
        return {"id": self.id, "project_id": self.project_id, "entries": refs, "sha256": _sha256(_canonical(body))}


class MasterSnapshotSource(Protocol):
    def get_snapshot(self, tenant_id: str, project_id: str) -> MasterSnapshot: ...


class AdminApiSource:
    """Seam for the real call (E3.2): GET /admin/masters/snapshots/{id}, tenant-scoped, sha256 verified.

    Not implemented in the spike: the Admin API does not exist yet (#55, #21 are open).
    """

    def get_snapshot(self, tenant_id: str, project_id: str) -> MasterSnapshot:
        raise NotImplementedError("Admin masters snapshot API is not built yet (#55)")


class WorkbookMockSource:
    """Builds a snapshot from the consolidated masters workbook (MSC). Test input only."""

    def __init__(self, workbook_path: str | Path) -> None:
        self._path = Path(workbook_path)

    def get_snapshot(self, tenant_id: str, project_id: str) -> MasterSnapshot:
        import openpyxl

        wb = openpyxl.load_workbook(self._path, data_only=True, read_only=True)
        entries: list[MasterEntry] = []
        for master_type, tab in MASTER_TABS.items():
            rows = list(wb[tab].iter_rows(values_only=True))
            headers = [_clean(h) for h in rows[1]]  # row 1 Metadata, row 2 Name (BAM 0.2 column names)
            for raw in rows[3:]:  # row 3 Description
                label = _clean(raw[0])
                payload = {h: _clean(v) for h, v in zip(headers[1:], raw[1:], strict=False) if h}
                if not label or not any(payload.values()):
                    continue  # blank template row (e.g. "Unit #5")
                entries.append(self._entry(master_type, label, payload))
        return MasterSnapshot(id=f"mock-snapshot-{project_id}", tenant_id=tenant_id, project_id=project_id,
                              entries=tuple(entries))

    @staticmethod
    def _entry(master_type: str, label: str, payload: dict[str, str]) -> MasterEntry:
        misc = next((v for k, v in payload.items() if k.startswith("Misc")), "") or _last_text(payload)
        m = _STATUS_RE.search(misc)
        status = m.group(1).upper() if m else DEFAULT_STATUS
        return MasterEntry(
            entry_id=f"mock-{master_type.lower()}-{label.lower().replace(' ', '').replace('#', '')}",
            version=1, master_type=master_type, row_label=label,
            stable_id=_stable_id(master_type, label, payload), status=status, payload=payload,
        )


def to_master_context(snapshot: MasterSnapshot, root_id: str, allow_restricted: bool = False
                      ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """E3.6 mapping. Returns (masters.jsonl rows, inclusion report). Deterministic for a given snapshot."""
    org_codes = {e.stable_id for e in snapshot.entries if e.master_type == "ORGANIZATION"}
    rows: list[dict[str, Any]] = []
    included: list[str] = []
    excluded: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    used_ids: set[str] = set()

    for e in sorted(snapshot.entries, key=lambda x: (list(MASTER_TABS).index(x.master_type), x.row_label)):
        rag = _rag(e)
        if e.status in EXCLUDED_STATUSES:
            excluded.append({"stable_id": e.stable_id, "reason": e.status})
            continue
        if rag.get("Security / Access Level", "").lower() == "restricted" and not allow_restricted:
            excluded.append({"stable_id": e.stable_id, "reason": "RESTRICTED"})
            continue

        node_id = rag.get("ID Field") or e.stable_id
        if node_id in used_ids:  # duplicate ID Field: suffix with stable_id (E3.6 edge case)
            warnings.append({"stable_id": e.stable_id, "code": "DUPLICATE_ID_FIELD", "detail": node_id})
            node_id = f"{node_id}:{e.stable_id}"
        used_ids.add(node_id)

        parent_id: str | None = None
        if e.master_type != "ORGANIZATION":
            declared = rag.get("Parent Entity")
            if declared and declared in org_codes:
                parent_id = declared
            elif declared:  # E3.6: unknown parent -> edge to graph root + warning
                parent_id = root_id
                warnings.append({"stable_id": e.stable_id, "code": "UNKNOWN_PARENT", "detail": declared})
            else:
                warnings.append({"stable_id": e.stable_id, "code": "NO_PARENT", "detail": ""})
        rows.append({"node_id": node_id, "node_type": rag.get("Entity / Node Type") or NODE_TYPE_DEFAULT[e.master_type],
                     "master_type": e.master_type, "payload": e.payload, "parent_id": parent_id})
        included.append(e.stable_id)

    report = {"snapshot_sha256": snapshot.ref()["sha256"], "snapshot_size": len(snapshot.entries),
              "included": included, "excluded": excluded, "warnings": warnings}
    assert len(included) + len(excluded) == len(snapshot.entries), "inclusion report must sum to snapshot size"
    return rows, report


def dump_masters_jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical(r) + b"\n" for r in rows)


def _stable_id(master_type: str, label: str, payload: dict[str, str]) -> str:
    if master_type == "ORGANIZATION":
        return payload.get("Code") or _slug(label)
    column, pattern = _STABLE_ID_RULES[master_type]
    m = pattern.search(payload.get(column, ""))
    return m.group(1) if m else (_rag_from(payload).get("ID Field") or _slug(label))


def _rag(entry: MasterEntry) -> dict[str, str]:
    return _rag_from(entry.payload)


def _rag_from(payload: dict[str, str]) -> dict[str, str]:
    raw = payload.get("RAG Reference", "")
    try:
        data = json.loads(raw) if raw.strip().startswith("{") else {}
    except json.JSONDecodeError:
        return {}
    return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _last_text(payload: dict[str, str]) -> str:
    return next((v for k, v in reversed(list(payload.items())) if v and k != "RAG Reference"), "")


def _clean(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").upper()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build master context from the workbook mock (spike E0.9 / T04)")
    ap.add_argument("--workbook", default="/spike/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx")
    ap.add_argument("--root", default="ORG-EMS")
    ap.add_argument("--out", default="/spike/out")
    args = ap.parse_args()
    snap = WorkbookMockSource(args.workbook).get_snapshot("tenant-spike", "project-spike")
    rows, report = to_master_context(snap, root_id=args.root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = dump_masters_jsonl(rows)
    (out / "masters.jsonl").write_bytes(data)
    (out / "masters.inclusion-report.json").write_bytes(_canonical(report) + b"\n")
    print(f"snapshot entries={report['snapshot_size']} included={len(report['included'])} "
          f"excluded={len(report['excluded'])} warnings={len(report['warnings'])}")
    print("excluded:", report["excluded"])
    print("warnings:", [(w['stable_id'], w['code']) for w in report["warnings"]])
    print("masters.jsonl sha256:", _sha256(data))


if __name__ == "__main__":
    main()
