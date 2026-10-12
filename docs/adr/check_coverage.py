"""Checks that the ADRs cover the plan's decisions (E0.1). Exit 0 = complete, 1 = problems listed.

- Every C-id in PLAN §3 is owned by exactly one ADR: a `| C-xx | ... |` row in its Decision
  table, with the decision text copied verbatim. Mentions in prose don't count as ownership, and
  a superseded ADR owns nothing (its text stays as written; the superseding ADR carries the rows).
- Every tracked open item from PLAN §30 has a `| O-xx | ... |` row carrying its default text
  (or "Resolved" for resolved items).
- Every ADR has a valid Status line and appears in the README index.
- Relative Markdown links in docs/adr/ and PLAN §30 resolve.

    python docs/adr/check_coverage.py [--adr-dir docs/adr] [--plan-dir docs/plan]
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPEN_ITEMS = [
    "O-03",
    "O-05",
    *(f"O-{n}" for n in range(15, 25)),
]  # the 12 tracked by E0.1
RESOLVED = {"O-18"}
STATUS = re.compile(r"^- Status: (Accepted|Accepted \(default\)|Proposed|Superseded by \d{4})$")
ADR_FILE = re.compile(r"^\d{4}-[a-z0-9-]+\.md$")
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def rows(text: str, prefix: str) -> dict[str, list[str]]:
    """`| <prefix>-NN | ... |` table rows by id (first occurrence)."""
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        if re.match(rf"^\| {prefix}-\d{{2}} \|", line):
            row = cells(line)
            found.setdefault(row[0], row)
    return found


def check(adr_dir: Path, plan_dir: Path) -> list[str]:
    problems: list[str] = []
    decisions = rows((plan_dir / "02-confirmed-decisions.md").read_text(encoding="utf-8"), "C")
    open_items_file = plan_dir / "07-risks-and-open-items.md"
    open_items = rows(open_items_file.read_text(encoding="utf-8"), "O")
    adrs = sorted(p for p in adr_dir.iterdir() if ADR_FILE.match(p.name))
    readme = (adr_dir / "README.md").read_text(encoding="utf-8")

    owners: dict[str, list[str]] = {}
    for adr in adrs:
        text = adr.read_text(encoding="utf-8")
        status = [line for line in text.splitlines() if line.startswith("- Status:")]
        if len(status) != 1 or not STATUS.match(status[0]):
            problems.append(f"{adr.name}: Status line missing or invalid: {status}")
        if f"]({adr.name})" not in readme:
            problems.append(f"{adr.name}: not listed in README.md index")
        if status and "Superseded by" in status[0]:
            continue  # its text stays as written, but the superseding ADR owns the ids now
        for cid, row in rows(text, "C").items():
            owners.setdefault(cid, []).append(adr.name)
            if cid in decisions and row[2] != decisions[cid][2]:
                problems.append(f"{adr.name}: {cid} decision text differs from PLAN §3")
        for oid, row in rows(text, "O").items():
            owners.setdefault(oid, []).append(adr.name)
            if oid in RESOLVED:
                if "resolved" not in " ".join(row).lower():
                    problems.append(f"{adr.name}: {oid} must be marked Resolved")
            elif oid in open_items and open_items[oid][4] not in " | ".join(row):
                problems.append(f"{adr.name}: {oid} default differs from PLAN §30")

    missing = sorted(i for i in [*decisions, *OPEN_ITEMS] if i not in owners)
    duplicated = sorted(i for i, files in owners.items() if len(files) > 1)
    if missing:
        problems.append(f"missing: {', '.join(missing)}")
    for item in duplicated:
        problems.append(f"duplicated: {item} in {', '.join(owners[item])}")

    for doc in [*adr_dir.glob("*.md"), open_items_file]:
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            if "://" not in target and not (doc.parent / target).resolve().exists():
                problems.append(f"{doc.name}: broken link {target}")

    c_found = len([i for i in decisions if i in owners])
    o_found = len([i for i in OPEN_ITEMS if i in owners])
    print(
        f"{c_found}/{len(decisions)} decisions, {o_found}/{len(OPEN_ITEMS)} open items, "
        f"{len(adrs)} ADRs"
    )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adr-dir", type=Path, default=ROOT / "docs/adr")
    parser.add_argument("--plan-dir", type=Path, default=ROOT / "docs/plan")
    args = parser.parse_args()
    problems = check(args.adr_dir, args.plan_dir)
    for problem in problems:
        print(f"FAIL  {problem}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
