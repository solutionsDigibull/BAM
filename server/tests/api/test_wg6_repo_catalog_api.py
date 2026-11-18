"""WG-6 (#754): GET /projects/{id}/repo-catalog through the real app, auth chain and database."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_projects
from sbam_api.masters.models import MasterEntry
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_v03 import STRUCTURE_VERSION
from sbam_contracts.masters_workbook import PAYLOAD_MODELS
from test_auth import FDE_A2, TENANT_B, problem
from test_masters_api import engine_with_masters
from test_project_workspace import Harness


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_masters)
    yield Harness(tmp_path)


def add(h: Harness, master_type: str, payload: dict[str, Any], **body: Any) -> dict[str, Any]:
    res = h.call(
        "POST", f"/admin/masters/{master_type}", h.admin_a(), json={"payload": payload, **body}
    )
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def seed(h: Harness) -> dict[str, dict[str, Any]]:
    agent = add(
        h,
        "AGENTS",
        {
            "agent_id": "AG-INTAKE",
            "agent_name": "Intake",
            "reference": "https://github.com/acme/intake, http://old.example.com/intake",
        },
    )
    tool = add(
        h,
        "TOOLING",
        {
            "tooling_id": "TOOL-MFG-12",
            "category": "Costing",
            "links_path": "https://gitlab.com/acme/costing#v2 and s3://bucket/x",
        },
        row_label="TOOL-MFG-12",
    )
    return {"agent": agent, "tool": tool}


def catalog(h: Harness, token: str | None = None) -> Any:
    return h.call("GET", f"/projects/{h.project}/repo-catalog", token or h.fde())


def test_wg6_t01_candidates_carry_the_row_they_came_from(h: Harness) -> None:
    rows = seed(h)
    res = catalog(h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["structure_version"] == STRUCTURE_VERSION
    by_url = {c["url"]: c for c in body["candidates"]}
    assert set(by_url) == {"https://github.com/acme/intake", "https://gitlab.com/acme/costing"}
    agent = by_url["https://github.com/acme/intake"]
    assert (agent["source"], agent["master_entry_id"]) == ("ADMIN_AGENTS", rows["agent"]["id"])
    assert (agent["stable_id"], agent["agent_name"]) == ("AG-INTAKE", "Intake")
    tool = by_url["https://gitlab.com/acme/costing"]
    assert (tool["source"], tool["ref"], tool["row_label"]) == (
        "ADMIN_TOOLING",
        "v2",
        "TOOL-MFG-12",
    )
    assert tool["master_entry_id"] == rows["tool"]["id"] and tool["agent_name"] is None


def test_wg6_t01_ids_are_the_same_on_every_read(h: Harness) -> None:
    seed(h)
    first = [c["id"] for c in catalog(h).json()["candidates"]]
    assert first and first == [c["id"] for c in catalog(h).json()["candidates"]]
    assert len(set(first)) == len(first)


def test_wg6_t02_unsafe_entries_are_reported_not_offered(h: Harness) -> None:
    seed(h)
    body = catalog(h).json()
    assert [(r["value"], r["reason"], r["source"]) for r in body["rejected"]] == [
        ("http://old.example.com/intake", "NOT_HTTPS", "ADMIN_AGENTS"),
        ("s3://bucket/x", "NOT_HTTPS", "ADMIN_TOOLING"),
    ]
    assert all(c["url"].startswith("https://") for c in body["candidates"])


def test_wg6_t03_another_tenant_and_an_unassigned_fde_get_404(h: Harness) -> None:
    seed(h)
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(catalog(h, other_tenant), 404, "NOT_FOUND")
    problem(catalog(h, h.realm.token(FDE_A2)), 404, "NOT_FOUND")


def test_wg6_an_admin_cannot_read_the_project_catalog(h: Harness) -> None:
    assert catalog(h, h.admin_a()).status_code in (403, 404)


def test_wg6_retired_rows_are_never_offered(h: Harness) -> None:
    rows = seed(h)
    with h.sessions() as s:
        entry = s.get(MasterEntry, uuid.UUID(rows["agent"]["id"]))
        assert entry is not None
        entry.status = "RETIRED"
        s.commit()
    urls = {c["url"] for c in catalog(h).json()["candidates"]}
    assert urls == {"https://gitlab.com/acme/costing"}


def test_wg6_t04_the_catalog_reads_the_masters_0_3_columns() -> None:
    assert STRUCTURE_VERSION == "Masters 0.3"  # #781: Agents "Reference", Tooling "Links / Path"
    assert "reference" in PAYLOAD_MODELS[MasterType.AGENTS].model_fields
    assert "links_path" in PAYLOAD_MODELS[MasterType.TOOLING].model_fields
