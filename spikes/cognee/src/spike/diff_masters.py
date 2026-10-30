"""Mock vs real masters snapshot, as a Markdown diff for the spike report (#203, E0.18-T02).

    python -m spike.diff_masters <mock.xlsx> <real-admin-export.xlsx>

Both files are read with the spike's own `WorkbookMockSource`, so what is compared is what the spike
would have used: the tabs and column headers of each master type, the entries (by master type and
stable id) that only one side has, the status each entry resolves to, and the payload columns that
differ. It prints the differences and exits 1 when there are any, so a clean comparison is explicit.
Nothing here calls Cognee, Bifrost or any network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from spike.masters import MASTER_TABS, MasterEntry, WorkbookMockSource


def _entries(path: Path) -> dict[tuple[str, str], MasterEntry]:
    snapshot = WorkbookMockSource(path).get_snapshot("tenant-diff", "project-diff")
    return {(e.master_type, e.stable_id): e for e in snapshot.entries}


def _same(a: Any, b: Any) -> bool:
    """Equal cells. A cell holding JSON text (the `RAG Reference` payload) is compared as parsed
    JSON, so the same keys and values in a different order (the Admin export writes them sorted,
    the workbook keeps its column order) are not a difference."""
    if isinstance(a, str) and isinstance(b, str) and a != b:
        try:
            return bool(json.loads(a) == json.loads(b))
        except ValueError:
            return False
    return bool(a == b)


def _headers(path: Path) -> dict[str, list[str]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out: dict[str, list[str]] = {}
    for master_type, tab in MASTER_TABS.items():
        if tab in wb.sheetnames:
            rows = list(wb[tab].iter_rows(min_row=2, max_row=2, values_only=True))
            out[master_type] = [str(h).strip() for h in (rows[0] if rows else ()) if h]
    return out


def diff(mock: Path, real: Path) -> list[str]:
    """Human-readable differences; empty when the two snapshots are the same for the spike."""
    found: list[str] = []
    mock_h, real_h = _headers(mock), _headers(real)
    for master_type in MASTER_TABS:
        if master_type not in real_h:
            found.append(f"- tab `{MASTER_TABS[master_type]}` is missing from the real export")
            continue
        if master_type not in mock_h:
            found.append(f"- tab `{MASTER_TABS[master_type]}` is only in the real export")
            continue
        only_mock = [h for h in mock_h[master_type] if h not in real_h[master_type]]
        only_real = [h for h in real_h[master_type] if h not in mock_h[master_type]]
        if only_mock:
            found.append(f"- `{master_type}` columns only in the mock: {', '.join(only_mock)}")
        if only_real:
            found.append(f"- `{master_type}` columns only in the real export: {', '.join(only_real)}")
        if not only_mock and not only_real and mock_h[master_type] != real_h[master_type]:
            found.append(f"- `{master_type}` has the same columns in a different order")
    try:
        a, b = _entries(mock), _entries(real)
    except (KeyError, IndexError, ValueError) as exc:  # a tab or header row the spike expects is not there
        found.append(f"- the spike cannot read one of the files, so entries were not compared: {type(exc).__name__} {exc}")
        return found
    for key in sorted(set(a) - set(b)):
        found.append(f"- entry `{key[1]}` ({key[0]}) is only in the mock")
    for key in sorted(set(b) - set(a)):
        found.append(f"- entry `{key[1]}` ({key[0]}) is only in the real export")
    for key in sorted(set(a) & set(b)):
        x, y = a[key], b[key]
        if x.status != y.status:
            found.append(f"- `{key[1]}` status: mock {x.status}, real {y.status}")
        changed: list[Any] = sorted(
            c
            for c in set(x.payload) | set(y.payload)
            if not _same(x.payload.get(c), y.payload.get(c))
        )
        if changed:
            found.append(f"- `{key[1]}` payload differs in: {', '.join(map(str, changed))}")
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description="Diff the mock masters workbook against a real Admin export")
    ap.add_argument("mock", type=Path)
    ap.add_argument("real", type=Path)
    args = ap.parse_args()
    found = diff(args.mock, args.real)
    try:
        counts = f"mock {len(_entries(args.mock))}, real {len(_entries(args.real))}"
    except (KeyError, IndexError, ValueError):
        counts = "not countable (see below)"
    print(f"entries: {counts}; differences: {len(found)}")
    print("\n".join(found) if found else "no differences")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
