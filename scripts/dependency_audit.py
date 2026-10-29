#!/usr/bin/env python3
"""Fail CI on a runtime advisory nobody has reviewed (#437, audit A-10).

Reads the two scanners' JSON reports and the reviewed allowlist, and exits 1 when something is left:

  server:  uv export --frozen --no-dev --no-hashes --all-packages --no-emit-workspace -o req.txt
           uvx --from pip-audit==2.10.1 pip-audit -r req.txt --no-deps --disable-pip -f json -o py.json
  client:  (cd client && pnpm audit --prod --json > ../node.json)
  check:   python3 scripts/dependency_audit.py --python py.json --node node.json

* pip-audit gives no severity, so every Python advisory with no allowlist entry blocks.
* pnpm audit blocks on high and critical; moderate and low are printed, not blocking.
* An allowlist entry (`scripts/dependency-audit-allowlist.json`) names the package, the advisory ids
  it covers, why it cannot be reached or fixed, who reviewed it and when it expires. An advisory is
  allowed only by an entry that lists its id (or one of its aliases): a NEW advisory on the same
  package still blocks. An entry past its `expires` date blocks again, so the review is repeated.
  An entry that matches nothing is reported (the fix landed: delete it) but does not block.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ALLOWLIST = Path(__file__).with_name("dependency-audit-allowlist.json")
NODE_BLOCKING = frozenset({"high", "critical"})
REQUIRED = ("package", "ids", "reason", "reviewed", "expires")


@dataclass(frozen=True)
class Advisory:
    ecosystem: str  # "python" | "node"
    package: str
    version: str
    ids: frozenset[str]  # the advisory id and its aliases (CVE, GHSA, PYSEC)
    fix: str
    severity: str = ""  # node only

    def label(self) -> str:
        first = min(self.ids) if self.ids else "?"
        fix = f"fix {self.fix}" if self.fix else "no fix"
        return f"{self.ecosystem}:{self.package} {self.version} {first} ({fix})"


@dataclass
class Verdict:
    blocking: list[Advisory] = field(default_factory=list)
    reported: list[Advisory] = field(
        default_factory=list
    )  # below the node severity bar
    allowed: list[tuple[Advisory, str]] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.blocking or self.expired)


def parse_python(report: dict[str, Any]) -> list[Advisory]:
    if not isinstance(
        report.get("dependencies"), list
    ):  # a failed scan is not a clean one
        raise SystemExit("pip-audit report has no `dependencies`: the scan did not run")
    found: list[Advisory] = []
    for dep in report.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            ids = frozenset({vuln["id"], *vuln.get("aliases", [])})
            fix = ", ".join(vuln.get("fix_versions") or [])
            found.append(
                Advisory("python", dep["name"].lower(), dep["version"], ids, fix)
            )
    return found


def parse_node(report: dict[str, Any]) -> list[Advisory]:
    if not isinstance(report.get("advisories"), dict) or "metadata" not in report:
        detail = str(report.get("error") or report)[
            :200
        ]  # e.g. the registry's audit endpoint is down
        raise SystemExit(f"pnpm audit report is not a result: {detail}")
    found: list[Advisory] = []
    for adv in report["advisories"].values():
        ids = {str(i) for i in (adv.get("cves") or [])}
        if adv.get("github_advisory_id"):
            ids.add(str(adv["github_advisory_id"]))
        versions = sorted({f.get("version", "") for f in adv.get("findings", [])})
        found.append(
            Advisory(
                "node",
                adv["module_name"],
                ", ".join(v for v in versions if v),
                frozenset(ids),
                str(adv.get("patched_versions") or ""),
                str(adv.get("severity", "")).lower(),
            )
        )
    return found


def load_allowlist(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = json.loads(path.read_text())["entries"]
    for entry in entries:
        missing = [k for k in REQUIRED if not entry.get(k)]
        if missing:
            raise SystemExit(
                f"{path.name}: entry {entry.get('package')!r} lacks {missing}"
            )
        date.fromisoformat(entry["reviewed"])
        date.fromisoformat(entry["expires"])
    return entries


def judge(found: list[Advisory], entries: list[dict[str, Any]], today: date) -> Verdict:
    verdict = Verdict()
    used: set[int] = set()
    for i, entry in enumerate(entries):
        if date.fromisoformat(entry["expires"]) < today:
            verdict.expired.append(
                f"{entry['package']}: review expired {entry['expires']}"
            )
    for adv in found:
        match = next(
            (
                i
                for i, e in enumerate(entries)
                if e["package"].lower() == adv.package.lower()
                and adv.ids & set(e["ids"])
                and date.fromisoformat(e["expires"]) >= today
            ),
            None,
        )
        if match is not None:
            used.add(match)
            verdict.allowed.append((adv, entries[match]["reason"]))
        elif adv.ecosystem == "node" and adv.severity not in NODE_BLOCKING:
            verdict.reported.append(adv)
        else:
            verdict.blocking.append(adv)
    for i, entry in enumerate(entries):
        if i not in used and date.fromisoformat(entry["expires"]) >= today:
            verdict.unused.append(f"{entry['package']}: {', '.join(entry['ids'])}")
    return verdict


def report(verdict: Verdict) -> str:
    lines: list[str] = []
    for adv in verdict.blocking:
        lines.append(f"BLOCKING  {adv.label()}")
    for text in verdict.expired:
        lines.append(f"EXPIRED   allowlist {text}")
    for adv in verdict.reported:
        lines.append(f"reported  {adv.label()} [{adv.severity}]")
    for adv, reason in verdict.allowed:
        lines.append(f"allowed   {adv.label()}: {reason[:90]}")
    for text in verdict.unused:
        lines.append(f"unused    allowlist {text} matches nothing: delete it")
    return "\n".join(lines) or "no advisories"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--python", type=Path, help="pip-audit -f json output")
    parser.add_argument("--node", type=Path, help="pnpm audit --json output")
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    parser.add_argument(
        "--today", type=date.fromisoformat, default=datetime.now(UTC).date()
    )
    args = parser.parse_args(argv)
    if not (args.python or args.node):
        parser.error("give at least one report")
    found: list[Advisory] = []
    if args.python:
        found += parse_python(json.loads(args.python.read_text()))
    if args.node:
        found += parse_node(json.loads(args.node.read_text()))
    verdict = judge(found, load_allowlist(args.allowlist), args.today)
    print(report(verdict))
    return 1 if verdict.failed else 0


if __name__ == "__main__":
    sys.exit(main())
