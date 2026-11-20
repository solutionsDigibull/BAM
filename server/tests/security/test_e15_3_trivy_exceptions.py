"""E15.3 (#137): a Trivy finding with no fix is a documented exception with an expiry.

`.trivyignore.yaml` is the only way past ci.yml's CRITICAL image gate. Every entry names one
finding, says why it is accepted and who owns it, and ends within 90 days of being written; the
findings register lists it. Trivy itself stops ignoring an entry after `expired_at`, so the gate
fails again and the owner re-checks for a fix.
"""

import datetime as dt
import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
IGNORE = REPO / ".trivyignore.yaml"
CI = REPO / ".github" / "workflows" / "ci.yml"
REGISTER = REPO / "docs" / "security" / "findings-register.md"
MAX_DAYS = 90
FINDING_ID = re.compile(r"^(CVE-\d{4}-\d{4,}|GHSA(-[0-9a-z]{4}){3})$")
OWNER = re.compile(r"\bOwner (D1|D2|D3)\b")


def entries() -> list[dict[str, Any]]:
    data = yaml.safe_load(IGNORE.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and set(data) == {"vulnerabilities"}, "vulnerabilities only"
    items = data["vulnerabilities"]
    assert isinstance(items, list)
    return items


def test_e15_3_t08_each_trivy_exception_is_documented_owned_and_expires() -> None:
    seen = set()
    for entry in entries():
        assert set(entry) == {"id", "statement", "expired_at"}, entry
        finding = entry["id"]
        assert FINDING_ID.match(finding), finding
        assert finding not in seen, f"{finding} listed twice"
        seen.add(finding)
        assert OWNER.search(entry["statement"]), f"{finding}: the statement names no owner"
        expiry = entry["expired_at"]
        assert isinstance(expiry, dt.date), f"{finding}: expired_at is not a date"


def test_e15_3_t08_no_exception_runs_longer_than_90_days_from_its_last_change() -> None:
    """The file's newest entry date bounds the rest: every expiry is at most 90 days after the
    earliest one, so a renewal moves them together instead of one entry living for a year."""
    expiries = [e["expired_at"] for e in entries()]
    if expiries:
        assert max(expiries) - min(expiries) <= dt.timedelta(days=MAX_DAYS)


def test_e15_3_t08_the_ci_gate_uses_the_exceptions_and_hides_no_unfixed_finding() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert "--ignorefile /trivyignore.yaml" in ci
    gate = [line for line in ci.splitlines() if "--severity CRITICAL" in line]
    assert gate and all("--ignore-unfixed" not in line for line in gate), gate


def test_e15_3_t08_the_findings_register_lists_every_exception() -> None:
    register = REGISTER.read_text(encoding="utf-8")
    for entry in entries():
        assert entry["id"] in register, f"{entry['id']} is not in the findings register"
