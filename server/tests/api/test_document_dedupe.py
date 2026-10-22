"""E4.4 dedupe: identical content in one project is linked, never across projects.

The linking itself happens when the upload is registered (E4.2, `ingest/service.py`); these tests
pin the E4.4 test cases T05 and T06 against it. Same stand-ins as the upload tests.
"""

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sbam_api.ingest.models import Document, DocumentVersion, ReservationStatus
from test_auth import FDE_A1
from test_uploads import PDF
from test_uploads import Harness as UploadHarness


@pytest.fixture
def h(tmp_path: Path) -> Iterator[UploadHarness]:
    yield UploadHarness(tmp_path)


def test_e4_4_t05_the_same_pdf_twice_in_one_project_is_linked_to_the_first_version(
    h: UploadHarness,
) -> None:
    first, _ = h.upload("quote.pdf")
    second, _ = h.upload("quote-copy.pdf")
    (version,) = h.versions()
    assert str(version.id) == first
    linked = next(r for r in h.reservations() if str(r.id) == second)
    assert linked.status == ReservationStatus.DUPLICATE
    assert str(linked.document_version_id) == first
    assert h.count(Document) == 1  # no second document, so no second derivative to produce
    assert len(h.temporal.calls) == 1  # and no second ingestion


def test_e4_4_t06_the_same_pdf_in_two_projects_is_two_independent_versions(
    h: UploadHarness,
) -> None:
    project_a = h.project
    h.upload("quote.pdf")
    project_b = h.create("Another project")["id"]
    h.members.assigned[FDE_A1] = {uuid.UUID(project_a), uuid.UUID(project_b)}
    h.project = project_b
    h.upload("quote.pdf")

    versions = h.versions()
    assert len(versions) == 2 and h.count(Document) == 2
    assert {str(v.project_id) for v in versions} == {project_a, project_b}
    assert len({v.id for v in versions}) == 2
    sha = hashlib.sha256(PDF).hexdigest()
    assert {v.sha256 for v in versions} == {sha}  # the same bytes, kept twice
    assert len({v.storage_key for v in versions}) == 2  # each project has its own stored object
    assert all(isinstance(v, DocumentVersion) for v in versions)
    assert len(h.temporal.calls) == 2  # each is ingested on its own
