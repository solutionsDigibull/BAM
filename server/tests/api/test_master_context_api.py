"""E3.6 masters.jsonl export through the real service, database and storage."""

import json
import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import test_projects
from sbam_api.core.conventions import ApiError
from sbam_api.masters import service
from sbam_api.storage.local import LocalDiskBackend
from test_masters_api import MastersHarness, engine_with_masters

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
LINK = "s3://domain-rules/pcba_costing_v1.json"


def rag(**keys: str) -> dict[str, str]:
    base = dict.fromkeys(
        [
            "Example",
            "Links / Path",
            "Entity / Node Type",
            "Security / Access Level",
            "Refresh Frequency",
            "ID Field",
            "Parent Entity",
        ],
        "",
    )
    return {**base, **keys}


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MastersHarness]:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_masters)
    yield MastersHarness(tmp_path)


def seed(h: MastersHarness, project_id: str) -> None:
    h.entry("ORGANIZATION", payload={"code": "ORG-001", "org_name": "Rocketreach"})
    h.entry(
        payload={
            "domain_rule": "RULE-BOM: applies to BOM.",
            "rag_reference": rag(
                **{"ID Field": "DOM-PCBA-01", "Entity / Node Type": "DomainKnowledge"},
                **{"Parent Entity": "ORG-001", "Links / Path": LINK},
            ),
        }
    )
    h.entry(
        payload={
            "domain_rule": "RULE-COST: margins.",
            "rag_reference": rag(**{"Security / Access Level": "Restricted"}),
        }
    )
    h.entry(payload={"domain_rule": "Engineering rules"}, row_label="Rule #1")  # sample row


def export(h: MastersHarness, tmp_path: Path, project_id: str, snapshot_project: str) -> Any:
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), uuid.UUID(snapshot_project))
        return service.export_master_context(
            s, h.user(), snap.id, uuid.UUID(project_id), LocalDiskBackend(tmp_path / "store"), NOW
        )


def read(tmp_path: Path, report: Any) -> list[dict[str, Any]]:
    data = LocalDiskBackend(tmp_path / "store").get(report.storage_key, verify=True)
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def test_e3_6_export_writes_typed_nodes_and_a_complete_report(
    h: MastersHarness, tmp_path: Path
) -> None:
    project = h.create("EMS RFQ costing")
    seed(h, project["id"])
    report = export(h, tmp_path, project["id"], project["id"])
    assert report.dataset == project["id"]
    nodes = {n["node_id"]: n for n in read(tmp_path, report)}
    assert set(nodes) == {"ORG-001", "DOM-PCBA-01"}
    assert (nodes["DOM-PCBA-01"]["node_type"], nodes["DOM-PCBA-01"]["parent_id"]) == (
        "DomainKnowledge",
        "ORG-001",
    )
    assert sorted((x.stable_id, x.reason) for x in report.excluded) == [
        ("RULE-COST", "RESTRICTED"),
        ("Rule #1", "ILLUSTRATIVE"),
    ]
    assert len(report.included) + len(report.excluded) == 4  # the snapshot size


def test_e3_6_t04_admin_allows_restricted_then_it_is_included(
    h: MastersHarness, tmp_path: Path
) -> None:
    project = h.create("Costing")
    seed(h, project["id"])
    res = h.call(
        "PATCH",
        f"/admin/projects/{project['id']}",
        h.admin_a(),
        json={"allow_restricted": True},
        headers={"if-match": f'"{project["row_version"]}"'},
    )
    assert res.status_code == 200, res.text
    assert res.json()["allow_restricted"] is True
    report = export(h, tmp_path, project["id"], project["id"])
    assert "RULE-COST" in report.included
    assert [x.reason for x in report.excluded] == ["ILLUSTRATIVE"]


def test_e3_6_allow_restricted_defaults_false_and_cannot_be_null(h: MastersHarness) -> None:
    project = h.create("Defaults")
    assert project["allow_restricted"] is False
    res = h.call(
        "PATCH",
        f"/admin/projects/{project['id']}",
        h.admin_a(),
        json={"allow_restricted": None},
        headers={"if-match": '"1"'},
    )
    assert res.status_code == 422


def test_e3_6_t06_two_exports_of_one_snapshot_are_identical(
    h: MastersHarness, tmp_path: Path
) -> None:
    project = h.create("Twice")
    seed(h, project["id"])
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), uuid.UUID(project["id"]))
        store = LocalDiskBackend(tmp_path / "store")
        one = service.export_master_context(s, h.user(), snap.id, snap.project_id, store, NOW)
        two = service.export_master_context(s, h.user(), snap.id, snap.project_id, store, NOW)
    assert (one.sha256, one.storage_key) == (two.sha256, two.storage_key)


def test_e3_6_t07_pointers_are_never_fetched(
    h: MastersHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = h.create("No fetch")
    seed(h, project["id"])
    calls: list[Any] = []

    def no_network(self: socket.socket, address: Any) -> None:
        calls.append(address)
        raise AssertionError(f"network call to {address}")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: no_network(None, a))  # type: ignore[arg-type]
    report = export(h, tmp_path, project["id"], project["id"])
    assert calls == []
    rule = next(n for n in read(tmp_path, report) if n["node_id"] == "DOM-PCBA-01")
    assert rule["payload"]["rag_reference"]["Links / Path"] == LINK  # text only


def test_e3_6_t08_snapshot_of_another_project_is_rejected(
    h: MastersHarness, tmp_path: Path
) -> None:
    a, b = h.create("Project A"), h.create("Project B")
    seed(h, a["id"])
    with pytest.raises(ApiError) as raised:
        export(h, tmp_path, b["id"], a["id"])  # A's snapshot for B's dataset
    assert (raised.value.status, raised.value.code) == (409, "SNAPSHOT_PROJECT_MISMATCH")
    stored = [p for p in (tmp_path / "store").rglob("*") if p.is_file()]
    assert stored == []  # nothing written for a mismatched snapshot


def test_e3_6_snapshot_of_another_tenant_is_not_found(h: MastersHarness, tmp_path: Path) -> None:
    project = h.create("Mine")
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), uuid.UUID(project["id"]))
        other = h.user()
        object.__setattr__(other, "tenant_id", uuid.uuid4())
        with pytest.raises(ApiError) as raised:
            service.export_master_context(
                s, other, snap.id, snap.project_id, LocalDiskBackend(tmp_path / "x"), NOW
            )
    assert raised.value.status == 404
