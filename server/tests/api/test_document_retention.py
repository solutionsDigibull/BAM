"""E4.9 soft delete, restore and the purge of one document (API half).

Same stand-ins as the upload tests (SQLite with schema `sbam` mapped away, a local-disk storage
root, a fake Temporal client). The 90-day clock is moved by ageing the stored timestamps. The daily
`PurgeDeleted` schedule itself (T05, T06, T09) is in tests/temporal/test_purge_deleted.py.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.ingest import retention
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.ingest.retention import NoCleanup, PurgeContext
from sbam_api.projects.models import Project
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.documents import (
    MAX_ACTIVE_FILES,
    PURGE_RETENTION_DAYS,
    PurgeOutcome,
    PurgeTarget,
)
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy import select
from test_auth import FDE_A1, TENANT_A, TENANT_B, problem
from test_uploads import PDF
from test_uploads import Harness as UploadsHarness

OTHER_PDF = b"%PDF-1.7 another document\n" * 40


class Frozen:
    """A DocumentUsage that says every document feeds a frozen workflow version."""

    def is_frozen(self, session: Any, ctx: Any, document_id: uuid.UUID) -> bool:
        return True


class Harness(UploadsHarness):
    def docs(self, project: str | None = None) -> str:
        return f"/projects/{project or self.project}/documents"

    def delete(self, document_id: uuid.UUID | str, project: str | None = None) -> Any:
        return self.call("DELETE", f"{self.docs(project)}/{document_id}", self.fde())

    def restore(self, document_id: uuid.UUID | str, project: str | None = None) -> Any:
        return self.call("POST", f"{self.docs(project)}/{document_id}/restore", self.fde())

    def listing(self, **query: str) -> list[dict[str, Any]]:
        res = self.call("GET", self.docs(), self.fde(), params=query)
        assert res.status_code == 200, res.text
        items: list[dict[str, Any]] = res.json()["items"]
        return items

    def document_ids(self) -> list[uuid.UUID]:
        with self.sessions() as s:
            return list(s.scalars(select(Document.id).order_by(Document.id)))

    def row(self, document_id: uuid.UUID | str) -> Document:
        with self.sessions() as s:
            document = s.get(Document, uuid.UUID(str(document_id)))
            assert document is not None
            s.expunge(document)
            return document

    def version_state(self, document_id: uuid.UUID | str) -> str:
        with self.sessions() as s:
            query = select(DocumentVersion.state).where(
                DocumentVersion.document_id == uuid.UUID(str(document_id))
            )
            return str(s.scalars(query).one())

    def uploaded(self, name: str = "spec.pdf", data: bytes = PDF) -> uuid.UUID:
        """Upload one file and return its document id."""
        self.upload(name, data)
        return self.document_ids()[-1]

    def age(self, document_id: uuid.UUID | str, days: float) -> None:
        """Pretend the document was deleted `days` earlier."""
        with self.sessions() as s:
            document = s.get(Document, uuid.UUID(str(document_id)))
            assert document is not None and document.deleted_at is not None
            assert document.purge_after is not None
            shift = timedelta(days=days)
            document.deleted_at = document.deleted_at - shift
            document.purge_after = document.purge_after - shift
            s.commit()

    def purge(self, document_id: uuid.UUID | str, as_of: datetime | None = None) -> PurgeOutcome:
        target = PurgeTarget(
            tenant_id=TENANT_A,
            project_id=uuid.UUID(self.project),
            document_id=uuid.UUID(str(document_id)),
        )
        context = PurgeContext(LocalDiskBackend(self.tmp), NoCleanup(), "test-purge")
        with self.sessions() as s:
            return retention.purge_document(s, target, as_of or datetime.now(UTC), context)

    def actions(self, document_id: uuid.UUID | str) -> list[str]:
        return self.audit_actions(str(document_id))

    def other_project(self, *, tenant_b: bool = False) -> tuple[str, uuid.UUID]:
        """A second project, with a document in it, that FDE_A1 is not assigned to."""
        token = self.admin_b() if tenant_b else self.admin_a()
        res = self.call("POST", "/admin/projects", token, json={"name": "Elsewhere"})
        assert res.status_code == 201, res.text
        project_id = res.json()["id"]
        tenant = TENANT_B if tenant_b else TENANT_A
        with self.sessions() as s:
            document = Document(tenant_id=tenant, project_id=uuid.UUID(project_id), title="x.pdf")
            s.add(document)
            s.flush()
            s.add(
                DocumentVersion(
                    tenant_id=tenant,
                    project_id=uuid.UUID(project_id),
                    document_id=document.id,
                    storage_key="k",
                    sha256="f" * 64,
                    size=1,
                    mime="application/pdf",
                    state=S.UPLOADED.value,
                )
            )
            s.commit()
            return project_id, document.id


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


# --- T01 soft delete ----------------------------------------------------------------------------


def test_e4_9_t01_soft_delete_sets_the_90_day_purge_date(h: Harness) -> None:
    document_id = h.uploaded()
    assert h.delete(document_id).status_code == 204

    row = h.row(document_id)
    assert row.deleted_at is not None and row.purge_after is not None
    assert (
        row.purge_after - row.deleted_at
        == timedelta(days=PURGE_RETENTION_DAYS)
        == timedelta(days=90)
    )
    assert row.deleted_by == str(FDE_A1)
    assert h.version_state(document_id) == "DELETED"
    assert "DOCUMENT_DELETED" in h.actions(document_id)
    assert "delete" in h.audit_actions(str(h.versions()[0].id))


def test_e4_9_t01_deleted_document_is_hidden_unless_asked_for(h: Harness) -> None:
    keep = h.uploaded("keep.pdf", PDF)
    gone = h.uploaded("gone.pdf", OTHER_PDF)
    h.delete(gone)

    assert [d["id"] for d in h.listing()] == [str(keep)]
    everything = {d["id"]: d for d in h.listing(include_deleted="true")}
    assert set(everything) == {str(keep), str(gone)}
    assert everything[str(gone)]["state"] == "DELETED"
    assert everything[str(gone)]["deleted_by"] == str(FDE_A1)
    assert everything[str(gone)]["purge_after"] is not None
    assert everything[str(keep)]["deleted_at"] is None


def test_e4_9_deleting_twice_or_a_missing_document_is_404(h: Harness) -> None:
    document_id = h.uploaded()
    assert h.delete(document_id).status_code == 204
    problem(h.delete(document_id), 404, "NOT_FOUND")
    problem(h.delete(uuid7()), 404, "NOT_FOUND")


def test_e4_9_delete_waits_for_a_file_that_is_being_scanned(h: Harness) -> None:
    h.seed_versions(1, S.SCANNING.value)
    (document_id,) = h.document_ids()
    problem(h.delete(document_id), 409, "INVALID_TRANSITION")
    assert h.row(document_id).deleted_at is None


def test_e4_9_admin_token_is_the_wrong_portal(h: Harness) -> None:
    document_id = h.uploaded()
    res = h.call("DELETE", f"{h.docs()}/{document_id}", h.admin_a())
    problem(res, 403, "WRONG_PORTAL")
    assert h.row(document_id).deleted_at is None


def test_e4_9_delete_after_sow_approval_flags_the_context_change(h: Harness) -> None:
    before = h.uploaded("before.pdf", PDF)
    h.delete(before)
    assert _after(h, before)["workflow_context_changed"] is False

    h.set_state(h.project, "SOW_APPROVED")
    after = h.uploaded("after.pdf", OTHER_PDF)
    h.delete(after)
    assert _after(h, after)["workflow_context_changed"] is True
    assert h.row(after).deleted_at is not None  # the delete itself stands; nothing approved changes


def _after(h: Harness, document_id: uuid.UUID) -> dict[str, Any]:
    with h.sessions() as s:
        row = s.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == str(document_id), AuditLog.action == "DOCUMENT_DELETED"
            )
        ).one()
        assert row.after is not None
        return dict(row.after)


# --- T02 restore --------------------------------------------------------------------------------


def test_e4_9_t02_restore_ten_days_after_delete_brings_it_back(h: Harness) -> None:
    document_id = h.uploaded()
    h.delete(document_id)
    h.age(document_id, 10)
    calls_before = len(h.temporal.calls)

    res = h.restore(document_id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == str(document_id) and body["state"] == "UPLOADED"
    assert body["deleted_at"] is None and body["deleted_by"] is None and body["purge_after"] is None

    row = h.row(document_id)
    assert row.deleted_at is None and row.purge_after is None and row.deleted_by is None
    assert [d["id"] for d in h.listing()] == [str(document_id)]
    assert "DOCUMENT_RESTORED" in h.actions(document_id)
    with h.sessions() as s:
        restored = s.scalars(select(AuditLog).where(AuditLog.action == "DOCUMENT_RESTORED")).one()
        assert restored.after is not None and restored.after["knowledge_outdated"] is True
    # It is scanned and parsed again.
    assert len(h.temporal.calls) == calls_before + 1
    assert h.temporal.calls[-1]["workflow"] == "DocumentIngestion"


def test_e4_9_restoring_a_document_that_is_not_deleted_is_409(h: Harness) -> None:
    document_id = h.uploaded()
    problem(h.restore(document_id), 409, "CONFLICT")


def test_e4_9_restore_on_day_90_works_until_the_purge_date(h: Harness) -> None:
    document_id = h.uploaded()
    h.delete(document_id)
    h.age(
        document_id, PURGE_RETENTION_DAYS - 1 / 24
    )  # an hour before purge_after, purge not yet run
    assert h.restore(document_id).status_code == 200


def test_e4_9_restore_after_purge_date_is_gone_even_before_the_purge_ran(h: Harness) -> None:
    document_id = h.uploaded()
    h.delete(document_id)
    h.age(document_id, PURGE_RETENTION_DAYS + 1 / 24)
    problem(h.restore(document_id), 410, "GONE")
    assert h.version_state(document_id) == "DELETED"  # the purge job still owns the clean-up


# --- T03 limit ----------------------------------------------------------------------------------


def test_e4_9_t03_restore_over_the_file_limit_is_409(h: Harness) -> None:
    document_id = h.uploaded()
    h.delete(document_id)
    h.seed_versions(MAX_ACTIVE_FILES)

    problem(h.restore(document_id), 409, "FILE_LIMIT_REACHED")
    assert h.version_state(document_id) == "DELETED"
    assert h.row(document_id).deleted_at is not None
    assert "DOCUMENT_RESTORED" not in h.actions(document_id)


def test_e4_9_restore_into_the_last_free_slot_works(h: Harness) -> None:
    document_id = h.uploaded()
    h.delete(document_id)
    h.seed_versions(MAX_ACTIVE_FILES - 1)
    assert h.restore(document_id).status_code == 200


def test_e4_9_restore_counts_uploads_still_in_flight(h: Harness) -> None:
    document_id = h.uploaded()
    h.delete(document_id)
    h.seed_versions(MAX_ACTIVE_FILES - 1)
    assert (
        h.announce(h.files("pending.pdf")).status_code == 201
    )  # a reservation holds the last slot
    problem(h.restore(document_id), 409, "FILE_LIMIT_REACHED")


# --- T04 frozen ---------------------------------------------------------------------------------


def test_e4_9_t04_document_used_by_a_frozen_version_is_locked(h: Harness) -> None:
    document_id = h.uploaded()
    h.client.app.state.document_usage = Frozen()  # type: ignore[attr-defined]

    problem(h.delete(document_id), 423, "RESOURCE_LOCKED")
    assert h.row(document_id).deleted_at is None
    assert h.version_state(document_id) == "UPLOADED"
    assert "DOCUMENT_DELETED" not in h.actions(document_id)


# --- T07 restore after purge --------------------------------------------------------------------


def test_e4_9_t07_restore_after_purge_is_410_gone(h: Harness) -> None:
    document_id = h.uploaded()
    h.delete(document_id)
    h.age(document_id, PURGE_RETENTION_DAYS + 1)
    assert h.purge(document_id) is PurgeOutcome.PURGED

    res = h.restore(document_id)
    problem(res, 410, "GONE")
    assert h.version_state(document_id) == "PURGED"
    assert [d["id"] for d in h.listing()] == []  # the tombstone is not listed...
    tombstone = [d for d in h.listing(include_deleted="true") if d["id"] == str(document_id)]
    assert tombstone and tombstone[0]["state"] == "PURGED"  # ...unless asked for


# --- T08 security -------------------------------------------------------------------------------


def test_e4_9_t08_delete_in_a_project_the_fde_is_not_assigned_to_is_404(h: Harness) -> None:
    project_a2, foreign = h.other_project()
    problem(h.delete(foreign, project_a2), 404, "NOT_FOUND")
    problem(h.restore(foreign, project_a2), 404, "NOT_FOUND")
    problem(h.call("GET", h.docs(project_a2), h.fde()), 404, "NOT_FOUND")
    assert h.row(foreign).deleted_at is None and h.version_state(foreign) == "UPLOADED"


def test_e4_9_t08_a_document_of_another_project_is_404_through_my_project(h: Harness) -> None:
    _, foreign = h.other_project()
    problem(h.delete(foreign), 404, "NOT_FOUND")  # right project id, someone else's document id
    problem(h.restore(foreign), 404, "NOT_FOUND")
    assert h.row(foreign).deleted_at is None


def test_e4_9_t08_another_tenants_project_is_404_even_when_assigned(h: Harness) -> None:
    project_b, foreign = h.other_project(tenant_b=True)
    h.members.assigned[FDE_A1] = {uuid.UUID(h.project), uuid.UUID(project_b)}
    problem(h.delete(foreign, project_b), 404, "NOT_FOUND")
    problem(h.restore(foreign, project_b), 404, "NOT_FOUND")
    assert h.row(foreign).deleted_at is None


def test_e4_9_archived_project_takes_no_deletes(h: Harness) -> None:
    document_id = h.uploaded()
    with h.sessions() as s:
        project = s.get(Project, uuid.UUID(h.project))
        assert project is not None
        project.status = "ARCHIVED"
        s.commit()
    problem(h.delete(document_id), 409, "PROJECT_ARCHIVED")


# --- the purge of one document ------------------------------------------------------------------


def test_e4_9_purge_removes_the_file_and_keeps_a_tombstone(h: Harness) -> None:
    document_id = h.uploaded()
    key = h.versions()[0].storage_key
    h.delete(document_id)
    h.age(document_id, PURGE_RETENTION_DAYS)

    assert (h.tmp / key).exists()
    assert h.purge(document_id) is PurgeOutcome.PURGED
    assert not (h.tmp / key).exists()
    assert h.version_state(document_id) == "PURGED"
    assert h.row(document_id).title == "spec.pdf"  # DB tombstone stays
    assert h.actions(document_id).count("DOCUMENT_PURGED") == 1
    assert h.audit_actions(str(h.versions()[0].id)).count("purge") == 1


def test_e4_9_purge_runs_twice_without_a_second_audit_entry(h: Harness) -> None:
    document_id = h.uploaded()
    h.delete(document_id)
    h.age(document_id, PURGE_RETENTION_DAYS + 1)
    assert h.purge(document_id) is PurgeOutcome.PURGED
    assert h.purge(document_id) is PurgeOutcome.SKIPPED
    assert h.actions(document_id).count("DOCUMENT_PURGED") == 1


def test_e4_9_purge_leaves_a_document_that_is_not_due_yet(h: Harness) -> None:
    document_id = h.uploaded()
    key = h.versions()[0].storage_key
    h.delete(document_id)
    h.age(document_id, PURGE_RETENTION_DAYS - 1)
    assert h.purge(document_id) is PurgeOutcome.SKIPPED
    assert (h.tmp / key).exists() and h.version_state(document_id) == "DELETED"


def test_e4_9_purge_skips_a_document_restored_after_it_was_selected(h: Harness) -> None:
    document_id = h.uploaded()
    key = h.versions()[0].storage_key
    h.delete(document_id)
    h.age(document_id, PURGE_RETENTION_DAYS - 1)  # still restorable...
    assert h.restore(document_id).status_code == 200  # ...and restored before the job gets to it
    assert h.purge(document_id, datetime.now(UTC) + timedelta(days=365)) is PurgeOutcome.SKIPPED
    assert (h.tmp / key).exists() and h.version_state(document_id) == "UPLOADED"


def test_e4_9_same_content_uploaded_again_keeps_its_file_when_the_old_copy_is_purged(
    h: Harness,
) -> None:
    old = h.uploaded()
    h.delete(old)  # the bin no longer counts as "same content already in the project"
    new = h.uploaded()  # same bytes: a new document that shares the content-addressed file
    assert new != old and h.versions()[0].storage_key == h.versions()[1].storage_key
    key = h.versions()[0].storage_key

    h.age(old, PURGE_RETENTION_DAYS + 1)
    assert h.purge(old) is PurgeOutcome.PURGED
    assert (h.tmp / key).read_bytes() == PDF  # the live copy still has its bytes
    assert h.version_state(new) == "UPLOADED"
