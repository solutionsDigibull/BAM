"""E3.8b: a snapshot pins the parsed master documents, and the context is outdated when they change.

Through the real masters service on SQLite (the same stand-ins as the other masters API tests).
Only a document that is scanned CLEAN and PARSED, not deleted, in the tenant's own scope or the
project's, is pinned; the pins are part of the snapshot's hash."""

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import test_projects
from sbam_api.core.conventions import ApiError
from sbam_api.masters import service
from sbam_api.masters.models import MasterDocument
from sbam_api.masters.snapshot import snapshot_hash
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.masters_api import SnapshotDocument, SnapshotEntry
from test_auth import TENANT_A, TENANT_B
from test_masters_api import MastersHarness, engine_with_masters


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MastersHarness]:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_masters)
    yield MastersHarness(tmp_path)


def document(h: MastersHarness, title: str = "costing.pdf", **fields: Any) -> uuid.UUID:
    sha = uuid.uuid4().hex * 2
    values: dict[str, Any] = {
        "tenant_id": TENANT_A,
        "project_id": None,
        "title": title,
        "filetype_row_label": "Filetype #21",
        "intent_class": "Policy / rules",
        "master_type": "RULES",
        "route_source": "DEFAULT_BY_FILETYPE",
        "storage_key": f"{TENANT_A}/{uuid.UUID(int=0)}/uploads/{sha}",
        "sha256": sha,
        "size": 10,
        "mime": "application/pdf",
        "scan_status": "CLEAN",
        "parse_status": "PARSED",
        "derived_md_key": f"{TENANT_A}/{uuid.UUID(int=0)}/derived/{uuid.uuid4().hex * 2}",
        **fields,
    }
    with h.sessions() as s:
        row = MasterDocument(**values)
        s.add(row)
        s.commit()
        return row.id


def pinned(h: MastersHarness, project: str) -> list[SnapshotDocument]:
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), uuid.UUID(project))
        return service.snapshot_documents(s, h.user(), snap.id)


def test_e3_8b_a_snapshot_pins_the_tenants_and_the_projects_parsed_documents(
    h: MastersHarness,
) -> None:
    project = h.create("Costing")["id"]
    tenant_wide = document(h, "standard.pdf")
    mine = document(h, "agents.yaml", project_id=uuid.UUID(project), master_type="AGENTS")
    docs = pinned(h, project)
    assert sorted(d.document_id for d in docs) == sorted([tenant_wide, mine])
    by_title = {d.title: d for d in docs}
    assert by_title["agents.yaml"].master_type.value == "AGENTS"
    assert by_title["standard.pdf"].intent_class == "Policy / rules"
    assert all(len(d.sha256) == 64 and "/derived/" in d.derived_md_key for d in docs)


@pytest.mark.parametrize(
    "fields",
    [
        {"parse_status": "PENDING"},
        {"parse_status": "PARSING"},
        {"parse_status": "FAILED", "derived_md_key": None},
        {"scan_status": "SCAN_FAILED"},
        {"scan_status": "INFECTED"},
        {"scan_status": None},
        {"derived_md_key": None},
        {"deleted_at": datetime(2026, 10, 1, tzinfo=UTC)},
        {"tenant_id": TENANT_B},
    ],
)
def test_e3_8b_a_document_that_is_not_clean_parsed_and_the_tenants_is_not_pinned(
    h: MastersHarness, fields: dict[str, Any]
) -> None:
    project = h.create("Costing")["id"]
    document(h, **fields)
    assert pinned(h, project) == []


def test_e3_8b_another_projects_document_is_not_pinned(h: MastersHarness) -> None:
    mine, other = h.create("Mine")["id"], h.create("Other")["id"]
    document(h, "theirs.pdf", project_id=uuid.UUID(other))
    assert pinned(h, mine) == []
    assert [d.title for d in pinned(h, other)] == ["theirs.pdf"]


def test_e3_8b_the_pins_are_part_of_the_hash_and_the_export_still_verifies(
    h: MastersHarness, tmp_path: Path
) -> None:
    project = uuid.UUID(h.create("Costing")["id"])
    document(h, "standard.pdf")
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), project)
        entries = [SnapshotEntry.model_validate(e) for e in snap.entries]
        docs = [SnapshotDocument.model_validate(d) for d in snap.documents]
        assert snap.sha256 == snapshot_hash(project, entries, docs)
        assert snap.sha256 != snapshot_hash(project, entries)  # the document is in it
        now = datetime(2026, 10, 27, tzinfo=UTC)
        report = service.export_master_context(
            s, h.user(), snap.id, project, LocalDiskBackend(tmp_path), now
        )  # the integrity check recomputes the hash with the pinned documents
        assert report.snapshot_id == snap.id


def test_e3_8b_a_snapshot_with_no_documents_has_the_hash_it_always_had(h: MastersHarness) -> None:
    project = uuid.UUID(h.create("Costing")["id"])
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), project)
        entries = [SnapshotEntry.model_validate(e) for e in snap.entries]
        assert snap.documents == [] and snap.sha256 == snapshot_hash(project, entries)


def test_e3_8b_the_snapshot_api_lists_the_pinned_documents(h: MastersHarness) -> None:
    project = h.create("Costing")["id"]
    document(h, "standard.pdf")
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), uuid.UUID(project))
    body = h.call("GET", f"/admin/masters/snapshots/{snap.id}", h.admin_a()).json()
    assert [d["title"] for d in body["documents"]] == ["standard.pdf"]


def test_e3_8b_another_tenants_snapshot_documents_are_not_found(h: MastersHarness) -> None:
    project = uuid.UUID(h.create("Costing")["id"])
    document(h, "standard.pdf")
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), project)
        stranger = replace(h.user(), tenant_id=TENANT_B)
        with pytest.raises(ApiError) as caught:
            service.snapshot_documents(s, stranger, snap.id)
    assert caught.value.status == 404


# --- the context is outdated when the master documents change ----------------------------


def outdated(h: MastersHarness, snapshot_id: uuid.UUID) -> bool:
    with h.sessions() as s:
        return service.context_outdated(s, h.user(), snapshot_id)


def test_e3_8b_a_newly_parsed_document_makes_the_context_outdated(h: MastersHarness) -> None:
    project = uuid.UUID(h.create("Costing")["id"])
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), project)
    assert outdated(h, snap.id) is False
    document(h, "pending.pdf", parse_status="PENDING", derived_md_key=None)
    assert outdated(h, snap.id) is False  # not parsed yet: not context yet
    document(h, "standard.pdf")
    assert outdated(h, snap.id) is True


def test_e3_8b_a_deleted_pinned_document_makes_the_context_outdated(h: MastersHarness) -> None:
    project = uuid.UUID(h.create("Costing")["id"])
    doc = document(h, "standard.pdf")
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), project)
    assert outdated(h, snap.id) is False
    with h.sessions() as s:
        row = s.get(MasterDocument, doc)
        assert row is not None
        row.deleted_at = datetime(2026, 10, 27, tzinfo=UTC)
        s.commit()
    assert outdated(h, snap.id) is True


def test_e3_8b_a_snapshot_taken_before_documents_existed_is_outdated_once_one_is_parsed(
    h: MastersHarness,
) -> None:
    project = uuid.UUID(h.create("Costing")["id"])
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), project)
        assert snap.documents == []
    document(h, "standard.pdf")
    assert outdated(h, snap.id) is True
