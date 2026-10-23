"""E4.8 against the Compose stack: a scanned file goes through tusd, ClamAV, the type check, the
parse step and the OCR step of the real ingest worker, with Bifrost on the mock provider.

Needs `make up` with the default LLM_PROVIDER=mock. The mock cannot read an image: it answers the
page the request names with `Mock OCR text of page N.`, so what these tests prove is the plumbing
(the worker, Temporal, Bifrost, storage, the state machine), not the quality of the reading (that
is the live test, test_ocr_live.py). The test that stops Bifrost is last and starts it again.
"""

import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fixtures import fixture_path
from pdfs import make_pdf
from sbam_api.audit.models import NotificationOutbox
from sbam_api.ingest.models import DocumentVersion
from sqlalchemy import select

from .conftest import Compose
from .test_scan_stack import upload
from .test_uploads_stack import Scope

pytestmark = pytest.mark.integration

WAIT = 300  # seconds: workflow start, scan, detection, parse and OCR
DONE = {"PARSED", "PARSE_FAILED", "REJECTED", "SCAN_FAILED"}
LONG = "This page has a real text layer that is long enough"


@pytest.fixture
def scope() -> Iterator[Scope]:
    s = Scope()
    try:
        yield s
    finally:
        s.drop()


def finished(scope: Scope, version_id: uuid.UUID, seconds: int = WAIT) -> DocumentVersion:
    """The version once its ingestion is over (or when the wait is over)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for version in scope.versions():
            if version.id == version_id and version.state in DONE:
                return version
        time.sleep(2)
    return next(v for v in scope.versions() if v.id == version_id)


def document(compose: Compose, version: DocumentVersion) -> dict[str, Any]:
    """`doc.json`, read from the storage volume the way the API container sees it."""
    assert version.derived_json_key, f"no derivative: {version.state} {version.parse_code}"
    result = compose("exec", "-T", "sbam-api", "cat", f"/data/storage/{version.derived_json_key}")
    assert result.returncode == 0, result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def summary(doc: dict[str, Any]) -> list[tuple[int, str, str | None]]:
    return [(b["page"], b["text"], b.get("ocr")) for b in doc["blocks"]]


def test_e4_8_t01_a_three_page_scanned_pdf_ends_parsed_with_three_ocr_blocks(
    scope: Scope, compose: Compose
) -> None:
    version = finished(scope, upload(scope, "scan.pdf", make_pdf(["", "", ""])))
    assert version.state == "PARSED", (version.state, version.parse_code, version.parse_report)
    assert version.parse_report is not None and version.parse_report["ocr_pages"] == [1, 2, 3]
    assert version.parse_report["unreadable_pages"] == []
    assert summary(document(compose, version)) == [
        (n, f"Mock OCR text of page {n}.", "llm") for n in (1, 2, 3)
    ]


def test_e4_8_t02_in_a_mixed_pdf_only_the_scanned_page_is_read_by_the_model(
    scope: Scope, compose: Compose
) -> None:
    pdf = make_pdf([LONG, LONG + " two", ""])
    version = finished(scope, upload(scope, "mixed.pdf", pdf))
    assert version.state == "PARSED" and version.parse_report is not None
    assert version.parse_report["ocr_needed_pages"] == [3] and version.parse_report[
        "ocr_pages"
    ] == [3]
    assert summary(document(compose, version)) == [
        (1, LONG, None),
        (2, LONG + " two", None),
        (3, "Mock OCR text of page 3.", "llm"),
    ]


def test_e4_8_t08_a_scanned_image_file_ends_parsed_with_ocr_text(
    scope: Scope, compose: Compose
) -> None:
    png = fixture_path("documents", "scan.png").read_bytes()
    version = finished(scope, upload(scope, "scan.png", png))
    assert version.state == "PARSED", (version.state, version.parse_code)
    assert summary(document(compose, version)) == [(1, "Mock OCR text of page 1.", "llm")]


def bifrost_health(compose: Compose) -> str:
    return compose("ps", "bifrost", "--format", "{{.Health}}").stdout.strip()


def test_e4_8_t05_with_bifrost_down_the_pages_are_unreadable_the_owner_is_told_and_others_go_on(
    scope: Scope, compose: Compose
) -> None:
    assert compose("stop", "bifrost").returncode == 0
    try:
        text_id = upload(scope, "notes.md", b"# Notes\n\nA text document needs no OCR.\n")
        scan_id = upload(scope, "scan.pdf", make_pdf(["", ""]))
        scan = finished(scope, scan_id, seconds=420)  # 3 attempts, 10 s then 20 s apart
        text = finished(scope, text_id, seconds=60)
        assert text.state == "PARSED"  # another document is not affected
        assert scan.state == "PARSE_FAILED" and scan.parse_code == "OCR_UNAVAILABLE"
        assert scan.parse_report is not None and scan.parse_report["unreadable_pages"] == [1, 2]
        with scope.sessions() as s:
            events = [
                row.payload.get("entity_id")
                for row in s.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.tenant_id == scope.tenant,
                        NotificationOutbox.type == "document_version.parse_failed",
                    )
                )
            ]
        assert str(scan_id) in events  # the "document rejected" notification was queued
    finally:
        compose("start", "bifrost")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and bifrost_health(compose) != "healthy":
            time.sleep(3)
    assert bifrost_health(compose) == "healthy"  # the stack is whole again for whatever runs next
