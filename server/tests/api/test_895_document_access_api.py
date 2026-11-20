"""#895 signed access to an uploaded original (API half), cases UI2B-T01 .. T11.

Same stand-ins as the retention tests (SQLite with schema `sbam` mapped away, a local-disk storage
root, a fake Temporal client). A document is put into a state by writing it to the version row,
the way the scan and parse activities would leave it.
"""

import logging
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.ingest import access
from sbam_api.ingest.models import Document, DocumentVersion
from sqlalchemy import select
from test_auth import FDE_A1, TENANT_A, TENANT_B, problem
from test_document_retention import OTHER_PDF
from test_document_retention import Harness as RetentionHarness
from test_uploads import PDF

SIGNING_KEY = "k" * 40
NOT_RELEASED = "DOCUMENT_NOT_RELEASED"


class Harness(RetentionHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.client.app.state.settings.storage_signing_key = SecretStr(SIGNING_KEY)  # type: ignore[attr-defined]

    def release(self, document_id: uuid.UUID | str) -> None:
        self.mark(document_id, "PARSED", "CLEAN")

    def mark(self, document_id: uuid.UUID | str, state: str, scan: str | None) -> None:
        with self.sessions() as s:
            query = select(DocumentVersion).where(
                DocumentVersion.document_id == uuid.UUID(str(document_id))
            )
            for version in s.scalars(query):
                version.state = state
                version.scan_status = scan
            s.commit()

    def access(self, document_id: uuid.UUID | str, project: str | None = None) -> Any:
        path = f"{self.docs(project)}/{document_id}/access"
        return self.call("GET", path, self.fde())

    def released(self, name: str = "spec.pdf", data: bytes = PDF) -> uuid.UUID:
        document_id = self.uploaded(name, data)
        self.release(document_id)
        return document_id

    def link(self, document_id: uuid.UUID) -> str:
        res = self.access(document_id)
        assert res.status_code == 200, res.text
        url: str = res.json()["url"]
        return url

    def fetch(self, url: str) -> Any:
        return self.client.get(url)  # no bearer token: the signature is the credential

    def rows(self, action: str) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_ui2b_t01_a_released_file_gets_a_short_lived_link_that_serves_its_bytes(
    h: Harness,
) -> None:
    document_id = h.released("spec.pdf", PDF)
    before = int(time.time())
    res = h.access(document_id)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["filename"] == "spec.pdf" and body["mime"] == "application/pdf"
    q = query_of(body["url"])
    assert body["url"].startswith(f"/api/v1/document-access/{document_id}?")
    assert q["t"] == str(TENANT_A) and q["p"] == h.project
    assert before + access.ACCESS_TTL - 2 <= int(q["exp"]) <= before + access.ACCESS_TTL + 5
    assert access.ACCESS_TTL <= 900  # never longer than the other signed links

    file = h.fetch(body["url"])
    assert file.status_code == 200 and file.content == PDF


@pytest.mark.parametrize(
    ("state", "scan", "status"),
    [
        ("UPLOADED", None, 409),
        ("SCANNING", None, 409),
        ("SCAN_FAILED", "SCAN_FAILED", 409),
        ("REJECTED", "INFECTED", 409),
        ("PARSING", "CLEAN", 200),
        ("PARSE_FAILED", "CLEAN", 200),
    ],
)
def test_ui2b_t02_only_a_released_state_gets_a_link(
    h: Harness, state: str, scan: str | None, status: int
) -> None:
    document_id = h.uploaded()
    h.mark(document_id, state, scan)

    res = h.access(document_id)

    assert res.status_code == status, res.text
    if status == 409:
        problem(res, 409, NOT_RELEASED)
        assert h.rows("DOCUMENT_ACCESS_ISSUED") == []


def test_ui2b_t03_an_infected_file_is_never_served_even_with_a_valid_link(h: Harness) -> None:
    document_id = h.released()
    url = h.link(document_id)
    h.mark(document_id, "REJECTED", "INFECTED")  # quarantined after the link was issued

    res = h.fetch(url)

    problem(res, 409, NOT_RELEASED)
    assert PDF not in res.content


def test_ui2b_t04_a_deleted_file_is_refused_and_a_purged_one_is_gone(h: Harness) -> None:
    document_id = h.released()
    url = h.link(document_id)
    assert h.delete(document_id).status_code == 204

    problem(h.access(document_id), 409, NOT_RELEASED)
    problem(h.fetch(url), 409, NOT_RELEASED)

    h.mark(document_id, "PURGED", "CLEAN")
    problem(h.access(document_id), 410, "GONE")


def test_ui2b_t05_a_link_that_expired_is_refused(h: Harness) -> None:
    document_id = h.released()
    exp = int(time.time()) - 1
    sig = access.sign(SIGNING_KEY, TENANT_A, uuid.UUID(h.project), document_id, exp)
    url = f"/api/v1/document-access/{document_id}?t={TENANT_A}&p={h.project}&exp={exp}&sig={sig}"

    problem(h.fetch(url), 403, "FORBIDDEN")


def test_ui2b_t06_unknown_id_other_project_and_other_tenant_are_404(h: Harness) -> None:
    mine = h.released()
    problem(h.access(uuid7()), 404, "NOT_FOUND")

    other_project, other_doc = h.other_project()  # tenant A, FDE_A1 not assigned
    problem(h.access(other_doc, other_project), 404, "NOT_FOUND")
    problem(h.access(other_doc), 404, "NOT_FOUND")  # a foreign document id under my project

    tenant_b_project, tenant_b_doc = h.other_project(tenant_b=True)
    problem(h.access(tenant_b_doc, tenant_b_project), 404, "NOT_FOUND")
    problem(h.access(tenant_b_doc), 404, "NOT_FOUND")
    assert h.access(mine).status_code == 200


def test_ui2b_t06_a_link_for_one_document_does_not_open_another(h: Harness) -> None:
    first = h.released("a.pdf", PDF)
    second = h.released("b.pdf", OTHER_PDF)
    url = h.link(first)

    swapped = url.replace(str(first), str(second))
    assert h.fetch(swapped).status_code == 403
    assert h.fetch(url).content == PDF


@pytest.mark.parametrize("field", ["sig", "exp", "t", "p"])
def test_ui2b_t07_a_tampered_link_is_refused(h: Harness, field: str) -> None:
    document_id = h.released()
    q = query_of(h.link(document_id))
    q[field] = {
        "sig": q["sig"][:-2] + "00",
        "exp": str(int(q["exp"]) + 3600),
        "t": str(TENANT_B),
        "p": str(uuid7()),
    }[field]
    url = f"/api/v1/document-access/{document_id}?" + "&".join(f"{k}={v}" for k, v in q.items())

    res = h.fetch(url)

    problem(res, 403, "FORBIDDEN")
    assert PDF not in res.content


def test_ui2b_t07_without_a_configured_key_nothing_is_signed(h: Harness) -> None:
    document_id = h.released()
    h.client.app.state.settings.storage_signing_key = None  # type: ignore[attr-defined]

    assert h.access(document_id).status_code == 503


def test_ui2b_t08_the_audit_row_names_who_and_which_document_and_never_the_content(
    h: Harness,
) -> None:
    document_id = h.released("quote.pdf", PDF)
    url = h.link(document_id)
    assert h.fetch(url).status_code == 200

    (issued,) = h.rows("DOCUMENT_ACCESS_ISSUED")
    assert issued.entity_type == "document" and issued.entity_id == str(document_id)
    assert issued.actor_id == str(FDE_A1)
    assert issued.tenant_id == TENANT_A
    (downloaded,) = h.rows("DOCUMENT_DOWNLOADED")
    assert downloaded.entity_id == str(document_id)
    sig = query_of(url)["sig"]
    for row in (issued, downloaded):
        text = f"{row.before} {row.after} {row.actor_id}"
        assert "PDF-1" not in text and sig not in text


def test_ui2b_t09_nothing_secret_or_from_the_file_reaches_the_logs(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    document_id = h.released("quote.pdf", PDF)
    with caplog.at_level(logging.DEBUG):
        url = h.link(document_id)
        h.fetch(url)
        h.fetch(url.replace(query_of(url)["sig"], "0" * 64))

    sig = query_of(url)["sig"]
    # The server's own loggers; the test client's HTTP library logs the URL it was given.
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("sbam"))
    assert ours  # the requests were logged
    assert "PDF-1" not in ours and sig not in ours and SIGNING_KEY not in ours


def test_ui2b_t10_the_download_is_an_attachment_that_cannot_render_in_the_app_origin(
    h: Harness,
) -> None:
    document_id = h.released("page.pdf", PDF)
    with h.sessions() as s:  # the original claims to be HTML: it must still not be served as such
        s.scalars(select(DocumentVersion)).one().mime = "text/html"
        s.commit()

    res = h.fetch(h.link(document_id))

    assert res.status_code == 200 and res.content == PDF
    assert res.headers["content-type"] == "application/octet-stream"
    assert res.headers["content-disposition"].startswith("attachment;")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in res.headers["content-security-policy"]
    assert "no-store" in res.headers["cache-control"]


def test_ui2b_t10_a_hostile_file_name_cannot_break_the_header(h: Harness) -> None:
    document_id = h.released("a.pdf", PDF)
    with h.sessions() as s:
        s.scalars(select(Document)).one().title = 'x"; filename=evil.html'
        s.commit()

    res = h.fetch(h.link(document_id))

    assert res.status_code == 200
    assert res.headers["content-disposition"].startswith('attachment; filename="x_; filename=evil')
    assert res.headers["content-type"] == "application/octet-stream"


def test_ui2b_t11_an_admin_has_no_access(h: Harness) -> None:
    document_id = h.released()
    res = h.call("GET", f"{h.docs()}/{document_id}/access", h.admin_a())

    problem(res, 403, "WRONG_PORTAL")
