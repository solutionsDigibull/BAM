"""E4.13 retry a document whose scan or parse failed (API half).

Same stand-ins as the retention tests (SQLite with schema `sbam` mapped away, a local-disk storage
root, a fake Temporal client). A document is put into a state by writing it to the version row, the
way the scan and parse activities would leave it.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.ingest.models import DocumentVersion
from sbam_api.projects.models import Project
from sqlalchemy import select
from test_auth import FDE_A1, problem
from test_document_retention import Frozen
from test_document_retention import Harness as RetentionHarness

DOCUMENT_INGESTION = "DocumentIngestion"


class Harness(RetentionHarness):
    def retry(
        self, document_id: uuid.UUID | str, project: str | None = None, key: str | None = None
    ) -> Any:
        headers = {"Idempotency-Key": key} if key else {}
        return self.call(
            "POST", f"{self.docs(project)}/{document_id}/retry", self.fde(), headers=headers
        )

    def set_state(self, document_id: uuid.UUID | str, state: str) -> None:
        with self.sessions() as s:
            query = select(DocumentVersion).where(
                DocumentVersion.document_id == uuid.UUID(str(document_id))
            )
            for version in s.scalars(query):
                version.state = state
            s.commit()

    def started(self) -> int:
        return len([c for c in self.temporal.calls if c["workflow"] == DOCUMENT_INGESTION])

    def audit_for(self, action: str) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def failed(h: Harness, state: str = "SCAN_FAILED") -> uuid.UUID:
    document_id = h.uploaded()
    h.set_state(document_id, state)
    return document_id


def test_e4_13_t01_retrying_a_scan_failed_document_starts_ingestion_again(h: Harness) -> None:
    document_id = failed(h, "SCAN_FAILED")
    before = h.started()
    res = h.retry(document_id)
    assert res.status_code == 202, res.text
    run = res.json()["run"]
    assert run["type"] == DOCUMENT_INGESTION and run["state"] == "RUNNING"
    assert h.version_state(document_id) == "SCANNING"
    assert h.started() == before + 1
    call = h.temporal.calls[-1]
    assert call["id"] == f"ingest-{h.versions()[0].id}"  # the same workflow id, a fresh execution
    assert "DOCUMENT_RETRIED" in h.actions(document_id)
    assert any(a.action == "STATE_CHANGED" or a.after for a in h.audit_for("DOCUMENT_RETRIED"))
    assert [d["state"] for d in h.listing()] == ["SCANNING"]


def test_e4_13_t02_retrying_a_parse_failed_document_runs_scan_and_parse_again(h: Harness) -> None:
    document_id = failed(h, "PARSE_FAILED")
    res = h.retry(document_id)
    assert res.status_code == 202, res.text
    assert h.version_state(document_id) == "SCANNING"
    assert h.temporal.calls[-1]["workflow"] == DOCUMENT_INGESTION


@pytest.mark.parametrize("state", ["UPLOADED", "SCANNING", "PARSING", "PARSED"])
def test_e4_13_t03_a_document_that_has_not_failed_is_409_and_starts_nothing(
    h: Harness, state: str
) -> None:
    document_id = failed(h, state)
    before = h.started()
    body = problem(h.retry(document_id), 409, "CONFLICT")
    assert "Only failed documents can be retried" in body["detail"]
    assert h.version_state(document_id) == state and h.started() == before


def test_e4_13_t04_an_infected_document_can_never_be_retried(h: Harness) -> None:
    document_id = failed(h, "REJECTED")
    before = h.started()
    problem(h.retry(document_id), 409, "CONFLICT")
    assert h.version_state(document_id) == "REJECTED" and h.started() == before
    assert h.audit_for("DOCUMENT_RETRIED") == []


def test_e4_13_t05_another_projects_document_is_404(h: Harness) -> None:
    project_a2, foreign = h.other_project()
    h.set_state(foreign, "SCAN_FAILED")
    problem(h.retry(foreign, project_a2), 404, "NOT_FOUND")  # a project the FDE is not assigned to
    problem(h.retry(foreign), 404, "NOT_FOUND")  # my project, someone else's document id
    assert h.version_state(foreign) == "SCAN_FAILED"


def test_e4_13_t05_another_tenants_document_is_404_even_when_assigned(h: Harness) -> None:
    project_b, foreign = h.other_project(tenant_b=True)
    h.members.assigned[FDE_A1] = {uuid.UUID(h.project), uuid.UUID(project_b)}
    h.set_state(foreign, "SCAN_FAILED")
    problem(h.retry(foreign, project_b), 404, "NOT_FOUND")
    problem(h.retry(foreign), 404, "NOT_FOUND")
    assert h.version_state(foreign) == "SCAN_FAILED" and h.started() == 0


def test_e4_13_unknown_and_deleted_documents_are_404(h: Harness) -> None:
    problem(h.retry(uuid.uuid4()), 404, "NOT_FOUND")
    document_id = failed(h, "SCAN_FAILED")
    assert h.delete(document_id).status_code == 204
    problem(h.retry(document_id), 404, "NOT_FOUND")


def test_e4_13_t06_the_same_idempotency_key_answers_the_same_run_and_starts_once(
    h: Harness,
) -> None:
    document_id = failed(h, "SCAN_FAILED")
    key = "retry-key-0001"
    first = h.retry(document_id, key=key)
    assert first.status_code == 202, first.text
    started = h.started()
    again = h.retry(document_id, key=key)
    assert again.status_code == 202, again.text
    assert again.json()["run"]["id"] == first.json()["run"]["id"]
    assert h.started() == started  # nothing new was started
    assert len(h.audit_for("DOCUMENT_RETRIED")) == 1
    # a different key is a new request: the document is not failed any more
    problem(h.retry(document_id, key="retry-key-0002"), 409, "CONFLICT")


def test_e4_13_t07_temporal_down_keeps_the_document_failed_and_answers_503(h: Harness) -> None:
    document_id = failed(h, "PARSE_FAILED")
    h.temporal.down = True
    problem(h.retry(document_id), 503, "ORCHESTRATOR_UNAVAILABLE")
    # back to a failed, retryable state (a parse failure comes back as a scan failure)
    assert h.version_state(document_id) in ("SCAN_FAILED", "PARSE_FAILED")
    assert next(d["state"] for d in h.listing()) in ("SCAN_FAILED", "PARSE_FAILED")
    h.temporal.down = False
    res = h.retry(document_id)  # and it can be retried again
    assert res.status_code == 202, res.text
    assert h.version_state(document_id) == "SCANNING"


def test_e4_13_a_document_used_by_a_frozen_version_is_423(h: Harness) -> None:
    document_id = failed(h, "SCAN_FAILED")
    before = h.started()
    h.client.app.state.document_usage = Frozen()  # type: ignore[attr-defined]
    problem(h.retry(document_id), 423, "RESOURCE_LOCKED")
    assert h.version_state(document_id) == "SCAN_FAILED" and h.started() == before


def test_e4_13_an_archived_project_takes_no_retries(h: Harness) -> None:
    document_id = failed(h, "SCAN_FAILED")
    with h.sessions() as s:
        project = s.get(Project, uuid.UUID(h.project))
        assert project is not None
        project.status = "ARCHIVED"
        s.commit()
    problem(h.retry(document_id), 409, "PROJECT_ARCHIVED")
    assert h.version_state(document_id) == "SCAN_FAILED"


def test_e4_13_admin_token_is_the_wrong_portal(h: Harness) -> None:
    document_id = failed(h, "SCAN_FAILED")
    res = h.call("POST", f"{h.docs()}/{document_id}/retry", h.admin_a())
    problem(res, 403, "WRONG_PORTAL")
    assert h.version_state(document_id) == "SCAN_FAILED"


def test_e4_13_without_a_token_it_is_401(h: Harness) -> None:
    document_id = failed(h, "SCAN_FAILED")
    res = h.client.post(f"/api/v1{h.docs()}/{document_id}/retry")
    assert res.status_code == 401


# --- E4.5: why a parse failed is shown, and a retry starts from a clean slate -------------------


def set_parse_result(h: Harness, document_id: uuid.UUID | str, code: str) -> None:
    with h.sessions() as s:
        query = select(DocumentVersion).where(
            DocumentVersion.document_id == uuid.UUID(str(document_id))
        )
        for version in s.scalars(query):
            version.parse_code = code
            version.parse_report = {
                "pages": 0,
                "sheets": 0,
                "tables": 0,
                "warnings": ["the file has no text"],
                "ocr_needed_pages": [],
            }
        s.commit()


def test_e4_5_a_parse_failed_document_says_why_in_the_listing(h: Harness) -> None:
    document_id = failed(h, "PARSE_FAILED")
    set_parse_result(h, document_id, "EMPTY")
    [item] = h.listing()
    assert item["state"] == "PARSE_FAILED" and item["parse_code"] == "EMPTY"
    assert item["parse_report"]["warnings"] == ["the file has no text"]


def test_e4_5_a_document_that_was_never_parsed_has_no_parse_fields(h: Harness) -> None:
    h.uploaded()
    [item] = h.listing()
    assert item.get("parse_code") is None and item.get("parse_report") is None


def test_e4_5_a_retry_clears_the_old_verdict_and_report(h: Harness) -> None:
    document_id = failed(h, "PARSE_FAILED")
    set_parse_result(h, document_id, "CORRUPT")
    assert h.retry(document_id).status_code == 202
    [item] = h.listing()
    assert item["state"] == "SCANNING"
    assert item.get("parse_code") is None and item.get("parse_report") is None
    [version] = h.versions()
    assert version.parse_code is None and version.parse_report is None
