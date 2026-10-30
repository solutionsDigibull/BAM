"""A-10 (#437): `scripts/dependency_audit.py` blocks an unreviewed advisory and nothing else."""

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "dependency_audit.py"
spec = importlib.util.spec_from_file_location("dependency_audit", SCRIPT)
assert spec and spec.loader
audit = importlib.util.module_from_spec(spec)
sys.modules["dependency_audit"] = audit
spec.loader.exec_module(audit)

TODAY = date(2026, 11, 1)
Doc = dict[str, Any]


def entry(
    package: str = "filelock", ids: list[str] | None = None, expires: str = "2027-01-01"
) -> Doc:
    return {
        "package": package,
        "ids": ids or ["CVE-1"],
        "reason": "unreachable",
        "reviewed": "2026-10-30",
        "expires": expires,
    }


def py_report(*vulns: tuple[str, str, str, list[str]]) -> Doc:
    deps: dict[str, Doc] = {}
    for name, version, vuln_id, aliases in vulns:
        dep = deps.setdefault(name, {"name": name, "version": version, "vulns": []})
        dep["vulns"].append({"id": vuln_id, "aliases": aliases, "fix_versions": ["9.9"]})
    return {"dependencies": list(deps.values())}


def node_report(module: str, severity: str, ghsa: str, cves: list[str] | None = None) -> Doc:
    return {
        "metadata": {},
        "advisories": {
            "1": {
                "module_name": module,
                "severity": severity,
                "github_advisory_id": ghsa,
                "cves": cves or [],
                "patched_versions": ">=2",
                "findings": [{"version": "1.0.0"}],
            }
        },
    }


def run(found: list[Any], entries: list[Doc], today: date = TODAY) -> Any:
    return audit.judge(found, entries, today)


def test_a10_t01_an_unreviewed_python_advisory_blocks() -> None:
    found = audit.parse_python(py_report(("requests", "2.0", "CVE-9", [])))
    verdict = run(found, [entry()])
    assert verdict.failed and [a.package for a in verdict.blocking] == ["requests"]


def test_a10_t02_a_reviewed_advisory_is_allowed_by_id_or_alias() -> None:
    found = audit.parse_python(py_report(("filelock", "3.0", "PYSEC-1", ["CVE-1"])))
    verdict = run(found, [entry()])
    assert not verdict.failed and len(verdict.allowed) == 1


def test_a10_t03_a_new_advisory_on_an_allowed_package_still_blocks() -> None:
    found = audit.parse_python(
        py_report(("filelock", "3.0", "CVE-1", []), ("filelock", "3.0", "CVE-2", []))
    )
    verdict = run(found, [entry()])
    assert verdict.failed and [sorted(a.ids) for a in verdict.blocking] == [["CVE-2"]]


def test_a10_t04_the_same_id_on_another_package_is_not_allowed() -> None:
    found = audit.parse_python(py_report(("other", "1.0", "CVE-1", [])))
    assert run(found, [entry()]).failed


def test_a10_t05_an_expired_entry_blocks_and_allows_nothing() -> None:
    found = audit.parse_python(py_report(("filelock", "3.0", "CVE-1", [])))
    verdict = run(found, [entry(expires="2026-10-31")])
    assert verdict.failed and verdict.expired and len(verdict.blocking) == 1
    assert verdict.allowed == []


def test_a10_t06_an_expired_entry_blocks_even_when_it_matches_nothing() -> None:
    assert run([], [entry(expires="2026-10-31")]).failed


def test_a10_t07_node_high_and_critical_block_lower_is_reported() -> None:
    for severity, blocks in [
        ("critical", True),
        ("high", True),
        ("moderate", False),
        ("low", False),
    ]:
        found = audit.parse_node(node_report("qs", severity, "GHSA-aaaa-bbbb-cccc"))
        verdict = run(found, [])
        assert verdict.failed is blocks, severity
        assert bool(verdict.reported) is (not blocks)


def test_a10_t08_a_node_advisory_is_allowed_by_ghsa_or_cve() -> None:
    found = audit.parse_node(node_report("qs", "high", "GHSA-1", ["CVE-5"]))
    assert not run(found, [entry("qs", ["CVE-5"])]).failed
    assert not run(found, [entry("qs", ["GHSA-1"])]).failed


def test_a10_t09_an_entry_that_matches_nothing_is_reported_not_blocking() -> None:
    verdict = run([], [entry()])
    assert not verdict.failed and verdict.unused


def test_a10_t10_an_entry_without_a_reason_is_refused(tmp_path: Path) -> None:
    bad = entry()
    del bad["reason"]
    path = tmp_path / "allow.json"
    path.write_text(json.dumps({"entries": [bad]}))
    with pytest.raises(SystemExit):
        audit.load_allowlist(path)


def test_a10_t11_the_committed_allowlist_is_well_formed_and_reviewed() -> None:
    entries = audit.load_allowlist(audit.ALLOWLIST)
    assert entries
    for e in entries:
        assert date.fromisoformat(e["reviewed"]) <= date.fromisoformat(e["expires"])
        assert len(e["reason"]) > 40 and e["ids"]
    assert len({e["package"] for e in entries}) == len(entries)


def test_a10_t12_main_exit_codes(tmp_path: Path) -> None:
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps({"entries": [entry()]}))
    ok, bad = tmp_path / "ok.json", tmp_path / "bad.json"
    ok.write_text(json.dumps(py_report(("filelock", "3.0", "CVE-1", []))))
    bad.write_text(json.dumps(py_report(("requests", "2.0", "CVE-9", []))))
    common = ["--allowlist", str(allow), "--today", "2026-11-01"]
    assert audit.main(["--python", str(ok), *common]) == 0
    assert audit.main(["--python", str(bad), *common]) == 1
    with pytest.raises(SystemExit):
        audit.main(common)  # no report given


@pytest.mark.parametrize("report", [{}, {"error": {"code": "ENOTFOUND"}}, {"dependencies": None}])
def test_a10_t13_a_scanner_that_did_not_run_fails_instead_of_passing(
    report: Doc,
) -> None:
    """A scanner can exit non-zero with an error document: that is not 'no advisories'."""
    with pytest.raises(SystemExit):
        audit.parse_python(report)
    with pytest.raises(SystemExit):
        audit.parse_node(report)


def test_a10_t14_an_empty_but_real_report_is_clean() -> None:
    assert audit.parse_python({"dependencies": []}) == []
    assert audit.parse_node({"metadata": {}, "advisories": {}}) == []
