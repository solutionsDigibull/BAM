"""E4.5 against the Compose stack: documents go through tusd, ClamAV, the type check and the parse
step of the real ingest worker, Temporal and the storage volume.

An upload goes in as in test_ocr_stack.py; the tests then watch the version row and read `doc.json`
from the storage volume. No model is involved, so the provider mode of Bifrost does not matter.
The 15 MB workbook is parsed twice, once for the memory and time limit (T08) and once with the
worker killed in the middle (T06). The T06 test is last and starts the worker again.
"""

import asyncio
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from bigxlsx import COLUMNS, ROWS, big_workbook
from officefiles import sample_docx, xlsx_bytes
from openpyxl import Workbook
from pdfs import encrypted, make_pdf
from sbam_api.ingest.models import DocumentVersion
from sbam_api.temporal.client import connect
from sbam_contracts.documents import PARSE_MEMORY_LIMIT_BYTES, PARSE_TIMEOUT_SECONDS

from .conftest import Compose
from .test_ocr_stack import WAIT, document, finished
from .test_scan_stack import listing, upload
from .test_uploads_stack import TEMPORAL, Scope

pytestmark = pytest.mark.integration

LINE = "Quote line {n} with the parts and the prices"
BIG_WAIT = 900  # seconds: a 15 MB upload, its scan and its parse


@pytest.fixture
def scope() -> Iterator[Scope]:
    s = Scope()
    try:
        yield s
    finally:
        s.drop()


def blocks(doc: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = doc["blocks"]
    return found


async def attempts(version_id: uuid.UUID) -> dict[str, int]:
    """For each activity of `ingest-<version>`, the attempt that is in its history. Temporal
    records an activity's started event when it ends, so a retried activity shows attempt 2 or more.
    """
    client = await connect(TEMPORAL, "sbam")
    history = await client.get_workflow_handle(f"ingest-{version_id}").fetch_history()
    names: dict[int, str] = {}
    best: dict[str, int] = {}
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            names[event.event_id] = (
                event.activity_task_scheduled_event_attributes.activity_type.name
            )
        elif event.HasField("activity_task_started_event_attributes"):
            started = event.activity_task_started_event_attributes
            name = names.get(started.scheduled_event_id, "?")
            best[name] = max(best.get(name, 0), started.attempt)
    return best


def parsed(scope: Scope, name: str, data: bytes, seconds: int = WAIT) -> DocumentVersion:
    version = finished(scope, upload(scope, name, data), seconds)
    assert version.state == "PARSED", (version.state, version.parse_code, version.parse_report)
    return version


# --- the six document types ---------------------------------------------------------------------


def test_e4_5_t01_a_ten_page_text_pdf_is_parsed_with_a_block_for_every_page(
    scope: Scope, compose: Compose
) -> None:
    pdf = make_pdf([LINE.format(n=n) for n in range(1, 11)])
    version = parsed(scope, "offer.pdf", pdf)
    found = blocks(document(compose, version))
    assert [b["page"] for b in found] == list(range(1, 11))
    assert all(b.get("ocr") is None for b in found)  # a text layer: the model is not involved
    assert version.parse_report is not None and version.parse_report["pages"] == 10
    assert version.parse_report["ocr_needed_pages"] == []


def test_e4_5_t02_a_two_sheet_workbook_is_parsed_with_sheet_and_cell_provenance(
    scope: Scope, compose: Compose
) -> None:
    def build(book: Workbook) -> None:
        first = book.active
        assert first is not None
        first.title = "Sheet1"
        first.append(["MPN", "Qty"])
        first.append(["STM32", 12])
        first["B4"] = 99
        book.create_sheet("Prices").append(["Item", "EUR"])

    version = parsed(scope, "rates.xlsx", xlsx_bytes(build))
    doc = document(compose, version)
    cells = {(b["sheet"], b["cell"]): b["text"] for b in blocks(doc) if b["type"] == "cell"}
    assert cells[("Sheet1", "B4")] == "99" and cells[("Sheet1", "A2")] == "STM32"
    assert [t["sheet"] for t in doc["tables"]] == ["Sheet1", "Prices"]


def test_e4_5_t03_a_semicolon_csv_with_a_bom_is_parsed_as_a_table_with_line_provenance(
    scope: Scope, compose: Compose
) -> None:
    csv = "﻿MPN;Qty;Preis\nSTM32;12;1,50\nRC0603;85;0,01\n".encode()
    version = parsed(scope, "parts.csv", csv)
    doc = document(compose, version)
    [table] = doc["tables"]
    assert table["header"] == ["MPN", "Qty", "Preis"] and table["rows"][1] == [
        "RC0603",
        "85",
        "0,01",
    ]
    assert [b["line"] for b in blocks(doc) if b["type"] == "row"] == [1, 2, 3]


def test_e4_5_t04_a_docx_keeps_its_headings_and_gets_a_table(
    scope: Scope, compose: Compose
) -> None:
    version = parsed(scope, "terms.docx", sample_docx())
    doc = document(compose, version)
    headings = [(b["level"], b["text"]) for b in blocks(doc) if b["type"] == "heading"]
    assert headings[:2] == [(1, "Quote terms"), (1, "Scope")]
    assert doc["tables"][0]["header"] == ["MPN", "Qty"]


def test_e4_5_t05_a_password_protected_pdf_is_parse_failed_encrypted_and_not_retried(
    scope: Scope,
) -> None:
    locked = encrypted(make_pdf([LINE.format(n=1)]), user="secret", owner="secret")
    version_id = upload(scope, "locked.pdf", locked)
    version = finished(scope, version_id)
    assert version.state == "PARSE_FAILED" and version.parse_code == "ENCRYPTED"
    assert version.derived_json_key is None and version.reject_code is None
    history = asyncio.run(attempts(version_id))
    assert history["parse_document"] == 1  # its own verdict: Temporal had nothing to retry


# --- the 15 MB workbook ---------------------------------------------------------------------------


def container(compose: Compose, service: str) -> str:
    return compose("ps", "-q", service).stdout.strip()


def docker(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed docker argv, no shell
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.stdout.strip()


def megabytes(usage: str) -> float:
    """`123.4MiB / 1GiB` to 123.4."""
    used = usage.split("/")[0].strip()
    for suffix, factor in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024), ("B", 1 / 1048576)):
        if used.endswith(suffix):
            return float(used.removesuffix(suffix)) * factor
    raise AssertionError(f"unreadable memory figure {usage!r}")


class MemoryWatch:
    """The peak memory of one container, sampled with `docker stats` while a parse runs."""

    def __init__(self, compose: Compose, service: str) -> None:
        self.compose, self.service, self.peak = compose, service, 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            cid = container(self.compose, self.service)
            usage = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", cid) if cid else ""
            if usage:
                self.peak = max(self.peak, megabytes(usage))
            self._stop.wait(2)

    def __enter__(self) -> "MemoryWatch":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=35)


def test_e4_5_t08_a_15_mb_workbook_is_parsed_in_time_and_within_the_memory_limit(
    scope: Scope, compose: Compose
) -> None:
    data = big_workbook()
    assert 14 * 1024 * 1024 < len(data) <= 15 * 1024 * 1024  # a real 15 MB upload
    with MemoryWatch(compose, "worker-ingest") as watch:
        version = parsed(scope, "big.xlsx", data, BIG_WAIT)
    assert version.scanned_at is not None
    took = version.updated_at - version.scanned_at  # detection and parse, after the scan
    cid = container(compose, "worker-ingest")
    oom = docker("inspect", "-f", "{{.State.OOMKilled}}", cid) if cid else ""
    print(
        f"\nbig workbook: {len(data) // 1024} KB, parse took {took}, "
        f"worker peak {watch.peak:.0f} MiB"
    )
    assert took < timedelta(seconds=PARSE_TIMEOUT_SECONDS)
    assert 0 < watch.peak < PARSE_MEMORY_LIMIT_BYTES / 1048576  # sampled, and below the 1 GiB limit
    assert oom in ("", "false")  # the kernel did not kill the worker for its memory
    assert version.parse_report is not None and version.parse_report["sheets"] == 1
    cell_blocks = blocks(document(compose, version))
    assert sum(1 for b in cell_blocks if b["type"] == "cell") == ROWS * COLUMNS


def test_e4_5_t06_a_worker_killed_mid_parse_is_retried_and_stores_one_derivative(
    scope: Scope, compose: Compose
) -> None:
    data = big_workbook()
    version_id = upload(scope, "killed.xlsx", data)
    deadline = time.monotonic() + BIG_WAIT
    while time.monotonic() < deadline:  # the type is detected, so the parse starts now
        found = next((v for v in scope.versions() if v.id == version_id), None)
        if found is not None and found.detected_type:
            break
        time.sleep(1)
    else:
        pytest.fail("the version never reached the parse step")
    time.sleep(8)  # well inside a parse that takes a minute or more
    try:
        assert compose("kill", "worker-ingest").returncode == 0
        time.sleep(3)
    finally:
        compose("start", "worker-ingest")
    version = finished(scope, version_id, BIG_WAIT)  # heartbeat timeout (60 s), then the retry
    assert version.state == "PARSED", (version.state, version.parse_code)
    assert asyncio.run(attempts(version_id))["parse_document"] >= 2  # it was retried
    stored = listing(compose, scope, "derived").split()
    assert len(stored) == 2  # doc.md and doc.json, each stored once (content-addressed)
    health = compose("ps", "worker-ingest", "--format", "{{.Health}}").stdout.strip()
    assert health in ("healthy", "starting", "")  # the worker is back
