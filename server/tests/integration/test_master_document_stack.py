"""E3.8b against the Compose stack: a master document attached through the real sbam-api is scanned
by ClamAV, handed to Temporal, and parsed by the real ingest worker into `doc.md` and `doc.json`.

The attach answers while the document is still PENDING; the tests then read it back until the
workflow has ended. No model is involved, so the provider mode of Bifrost does not matter.
"""

import time
import uuid
from collections.abc import Iterator

import pytest
from fixtures import fixture_path

from .conftest import Compose
from .test_identity import admin_secret, sql
from .test_keycloak_realm import login
from .test_master_documents_stack import call

pytestmark = pytest.mark.integration

WAIT = 180  # seconds: workflow start, scan result already known, detection and parse


@pytest.fixture
def admin() -> str:
    admin_secret()  # the stack must have sbam-api-admin, or the fixture skips
    token: str = login("sbam-admin", "admin-a")["access_token"]
    return token


@pytest.fixture
def cleanup() -> Iterator[list[str]]:
    """Master-document ids to remove from the seed tenant afterwards, pass or fail."""
    ids: list[str] = []
    try:
        yield ids
    finally:
        for doc_id in ids:
            sql("DELETE FROM sbam.master_documents WHERE id = :id", id=doc_id)


def unique(data: bytes) -> bytes:
    """The same content with a line that is different on every run: always a fresh attach."""
    return data + f"\n{uuid.uuid4()}\n".encode()


def attach(admin: str, name: str, data: bytes, cleanup: list[str]) -> dict[str, object]:
    res = call("POST", "/admin/master-documents", admin, files={"file": (name, data, "text/plain")})
    assert res.status_code == 201, res.text
    body: dict[str, object] = res.json()
    cleanup.append(str(body["id"]))
    return body


def settled(admin: str, document_id: str, seconds: int = WAIT) -> dict[str, object]:
    """The document once its parse is over (PARSED or FAILED), or when the wait is over."""
    deadline = time.monotonic() + seconds
    while True:
        res = call("GET", f"/admin/master-documents/{document_id}", admin)
        assert res.status_code == 200, res.text
        body: dict[str, object] = res.json()
        if body["parse_status"] in ("PARSED", "FAILED") or time.monotonic() > deadline:
            return body
        time.sleep(2)


def test_e3_8b_a_master_document_is_parsed_by_the_real_worker(
    admin: str, compose: Compose, cleanup: list[str]
) -> None:
    data = unique(fixture_path("documents", "sample.md").read_bytes())
    attached = attach(admin, f"rules-{uuid.uuid4().hex}.md", data, cleanup)
    assert attached["scan_status"] == "CLEAN"
    body = settled(admin, str(attached["id"]))
    assert (body["parse_status"], body["parse_code"]) == ("PARSED", None)
    [row] = sql(
        "SELECT derived_md_key, derived_json_key, parse_report FROM sbam.master_documents"
        " WHERE id = :id",
        id=attached["id"],
    )
    md_key, json_key, report = row
    assert md_key and json_key and report is not None
    shown = compose("exec", "-T", "sbam-api", "cat", f"/data/storage/{md_key}")
    assert shown.returncode == 0 and "# NAV-9 RFQ summary" in shown.stdout
    events = sql(
        "SELECT action FROM sbam.audit_log WHERE entity_id = :id ORDER BY at",
        id=attached["id"],
    )
    assert [e[0] for e in events][-1] == "MASTER_DOCUMENT_PARSED"


def test_e3_8b_a_file_that_is_not_its_type_is_parse_failed_with_its_code(
    admin: str, cleanup: list[str]
) -> None:
    png = fixture_path("documents", "scan.png").read_bytes()
    attached = attach(admin, f"costing-{uuid.uuid4().hex}.pdf", unique(png), cleanup)
    body = settled(admin, str(attached["id"]))
    assert (body["parse_status"], body["parse_code"]) == ("FAILED", "TYPE_MISMATCH")
    [(md_key,)] = sql(
        "SELECT derived_md_key FROM sbam.master_documents WHERE id = :id", id=attached["id"]
    )
    assert md_key is None  # nothing was stored for it
