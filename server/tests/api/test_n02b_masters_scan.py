"""#609 (N-02b option (a)): the masters import and the master document upload as a Temporal run.

Through the real app and auth chain on SQLite, with a fake clamd on a real socket and a fake
Temporal client that records what the API starts. The worker side is run directly: the test calls
`scan_and_import` with the input the API handed to Temporal, as the activity does.

N02B-T01 a request answers 202 at once while the scan is slow (clamd is not called in the request)
N02B-T02 the status reports QUEUED, RUNNING, then DONE with the result
N02B-T03 an infected file ends FAILED with MALWARE and is quarantined
N02B-T04 the result equals the old synchronous result
N02B-T05 an idempotent replay returns the same scan and run
N02B-T06 another tenant gets 404
N02B-T07 a saturated clamd bulkhead does not stop a 202, and the bulkhead is never taken
N02B-T08 Temporal down is a clean 503 with no half state, and the key can be used again
N02B-T09 clamd down is retried, then FAILED with SCAN_UNAVAILABLE and nothing imported
N02B-T10 a master document goes through the same run
N02B-T11 the cheap checks still answer at once; no Idempotency-Key is 400
"""

import hashlib
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_projects
from clamd_fake import FakeClamd
from fixtures import eicar_bytes
from runs_db import FakeClient
from sbam_api.core.bulkhead import CLAMD
from sbam_api.masters import documents, scans
from sbam_api.masters.models import MasterDocument, MasterEntry
from sbam_api.storage.factory import make_backend
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.masters_scan import MastersScanInput, MastersScanState
from sbam_workers.ingest.masters_scan import (
    ClamdAt,
    ScanDeps,
    fail_for,
    scan_and_import,
)
from sqlalchemy import func, select
from temporalio.exceptions import ApplicationError
from test_auth import TENANT_A, problem
from test_master_documents import POLICY, XLSX, engine_with_documents
from test_masters_import import XLSX as XLSX_MIME
from test_masters_import import ImportHarness, workbook

PREFER = {"Prefer": "respond-async"}


class ScanHarness(ImportHarness):
    def __init__(self, tmp_path: Path, clamd: FakeClamd) -> None:
        super().__init__(tmp_path)
        self.tmp = tmp_path
        self.clamd = clamd
        self.temporal = FakeClient()
        state = self.client.app.state  # type: ignore[attr-defined]
        state.temporal = self.temporal
        TemporalRun.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]

    def go(
        self,
        data: bytes,
        key: str | None = "key-0000-0001",
        mode: str = "upsert",
        token: str | None = None,
    ) -> Any:
        headers = dict(PREFER)
        if key is not None:
            headers["Idempotency-Key"] = key
        return self.call(
            "POST",
            "/admin/masters/import",
            token or self.admin_a(),
            files={"file": ("masters.xlsx", data, XLSX_MIME)},
            data={"mode": mode},
            headers=headers,
        )

    def go_document(
        self, name: str, data: bytes, key: str | None = "doc-0000-0001", **form: str
    ) -> Any:
        headers = dict(PREFER)
        if key is not None:
            headers["Idempotency-Key"] = key
        return self.call(
            "POST",
            "/admin/master-documents",
            self.admin_a(),
            files={"file": (name, data, "application/octet-stream")},
            data=form,
            headers=headers,
        )

    def status(self, scan_id: str, token: str | None = None) -> Any:
        return self.call("GET", f"/admin/masters/scans/{scan_id}", token or self.admin_a())

    def started(self) -> MastersScanInput:
        """The input of the last workflow the API started."""
        arg = self.temporal.calls[-1]["arg"]
        assert isinstance(arg, MastersScanInput)
        return arg

    def deps(self) -> ScanDeps:
        settings = self.client.app.state.settings  # type: ignore[attr-defined]
        return ScanDeps(
            sessions=self.sessions,
            storage=make_backend(settings),
            clamd=ClamdAt(self.clamd.host, self.clamd.port, 5.0),
        )

    def work(self) -> Any:
        """What the worker does with the last started scan."""
        return scan_and_import(self.deps(), self.started())

    def stage(self, stage: str) -> None:
        with self.sessions() as s:
            for row in s.scalars(select(TemporalRun)):
                row.current_stage = stage
            s.commit()

    def pending(self, inp: MastersScanInput) -> Path:
        return self.tmp / inp.pending_key

    def entries(self) -> int:
        with self.sessions() as s:
            return s.scalar(select(func.count()).select_from(MasterEntry)) or 0

    def runs(self) -> int:
        with self.sessions() as s:
            return s.scalar(select(func.count()).select_from(TemporalRun)) or 0


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    with FakeClamd() as fake:
        yield fake


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clamd: FakeClamd) -> ScanHarness:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_documents)
    monkeypatch.setattr(documents, "SCAN_BACKOFF", 0.0)
    harness = ScanHarness(tmp_path, clamd)
    state = harness.client.app.state  # type: ignore[attr-defined]
    state.settings = state.settings.model_copy(
        update={"clamav_host": clamd.host, "clamav_port": clamd.port, "clamav_timeout": 5.0}
    )
    return harness


# --- T01: 202 at once ---------------------------------------------------------------------------


def test_n02b_t01_the_request_answers_202_while_the_scan_would_take_seconds(
    h: ScanHarness, clamd: FakeClamd
) -> None:
    clamd.delay = 3.0
    started = time.monotonic()
    res = h.go(workbook())
    assert time.monotonic() - started < 2.0 and res.status_code == 202, res.text
    body = res.json()
    assert res.headers["preference-applied"] == "respond-async"
    assert body["kind"] == "IMPORT" and body["state"] == "QUEUED"
    assert body["run"]["type"] == "MastersScanImport" and body["run"]["state"] == "RUNNING"
    assert body["run"]["temporal_workflow_id"] == f"masters-scan-{body['scan_id']}"
    assert clamd.scans == 0 and h.entries() == 0  # nothing was scanned or written by the request
    inp = h.started()
    assert str(inp.scan_id) == body["scan_id"] and inp.tenant_id == TENANT_A
    assert h.pending(inp).read_bytes() == workbook()  # waits in the scan's own scope
    assert f"/{inp.scan_id}/uploads/" in inp.pending_key
    [accepted] = h.actions("MASTERS_SCAN_ACCEPTED")
    assert accepted.after is not None and accepted.after["sha256"] == inp.sha256


# --- T02: the status ----------------------------------------------------------------------------


def test_n02b_t02_the_status_goes_queued_running_done(h: ScanHarness) -> None:
    scan_id = h.go(workbook()).json()["scan_id"]
    queued = h.status(scan_id).json()
    assert queued["state"] == "QUEUED" and queued["import_report"] is None
    h.stage("scanning")
    assert h.status(scan_id).json()["state"] == "RUNNING"
    result = h.work()
    assert result.state is MastersScanState.DONE
    done = h.status(scan_id).json()
    assert done["state"] == "DONE" and done["error"] is None
    assert done["import_report"]["applied"] is True and done["run"]["type"] == "MastersScanImport"
    assert h.entries() > 0 and not h.pending(h.started()).exists()
    assert len(h.actions("MASTERS_IMPORTED")) == 1 and len(h.actions("MASTERS_SCAN_DONE")) == 1


def test_n02b_t02_a_retried_activity_returns_the_stored_outcome(h: ScanHarness) -> None:
    h.go(workbook())
    first = h.work()
    again = h.work()  # the bytes are gone and the outcome is stored: the same answer, no rerun
    assert again == first and len(h.actions("MASTERS_SCAN_DONE")) == 1
    assert len(h.actions("MASTERS_IMPORTED")) == 1


# --- T03: infected ------------------------------------------------------------------------------


def test_n02b_t03_an_infected_workbook_ends_failed_with_malware(h: ScanHarness) -> None:
    scan_id = h.go(eicar_bytes()).json()["scan_id"]
    result = h.work()
    assert result.state is MastersScanState.FAILED and result.code is not None
    body = h.status(scan_id).json()
    assert body["state"] == "FAILED" and body["import_report"] is None
    assert (body["error"]["status"], body["error"]["code"]) == (422, "MALWARE")
    assert body["error"]["correlation_id"]
    assert h.entries() == 0 and not h.pending(h.started()).exists()
    [rejected] = h.actions("MASTERS_IMPORT_REJECTED")  # the audit row of today, unchanged
    assert rejected.after is not None and rejected.after["reason"] == "MALWARE"
    quarantined = h.tmp / rejected.after["quarantine_key"]
    assert quarantined.read_bytes() == eicar_bytes()


def test_n02b_t03_a_structure_mismatch_keeps_its_diff(h: ScanHarness) -> None:
    import io

    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(workbook()))
    del book["Rules"]
    out = io.BytesIO()
    book.save(out)
    scan_id = h.go(out.getvalue()).json()["scan_id"]
    assert h.work().state is MastersScanState.FAILED
    error = h.status(scan_id).json()["error"]
    assert (error["status"], error["code"]) == (422, "STRUCTURE_MISMATCH") and error["errors"]
    assert h.entries() == 0


# --- T04: the same result -----------------------------------------------------------------------


def test_n02b_t04_the_result_equals_the_synchronous_report(h: ScanHarness) -> None:
    sync = h.upload(workbook(), mode="validate-only")
    assert sync.status_code == 200, sync.text
    scan_id = h.go(workbook(), mode="validate-only").json()["scan_id"]
    h.work()
    assert h.status(scan_id).json()["import_report"] == sync.json()


def test_n02b_t04_without_prefer_the_route_still_answers_the_report(h: ScanHarness) -> None:
    assert h.upload(workbook(), mode="validate-only").status_code == 200
    assert h.temporal.calls == []  # nothing was started


# --- T05: idempotent replay ---------------------------------------------------------------------


def test_n02b_t05_the_same_key_and_file_is_the_same_scan_and_run(h: ScanHarness) -> None:
    first = h.go(workbook()).json()
    second = h.go(workbook()).json()
    assert second["scan_id"] == first["scan_id"] and second["run"]["id"] == first["run"]["id"]
    assert h.runs() == 1 and len(h.actions("MASTERS_SCAN_ACCEPTED")) == 1
    assert len({c["id"] for c in h.temporal.calls}) == 1  # one workflow id, one execution


def test_n02b_t05_the_same_key_for_another_file_is_422(h: ScanHarness) -> None:
    h.go(workbook())

    def extra_row(book: Any) -> None:
        book["Rules"].append(["x"])

    other = h.go(workbook(extra_row))
    assert problem(other, 422, "IDEMPOTENCY_KEY_REUSED")


def test_n02b_t05_another_key_is_another_scan(h: ScanHarness) -> None:
    a = h.go(workbook(), key="key-0000-000a").json()
    b = h.go(workbook(), key="key-0000-000b").json()
    assert a["scan_id"] != b["scan_id"] and h.runs() == 2


# --- T06: tenants -------------------------------------------------------------------------------


def test_n02b_t06_another_tenants_admin_and_a_random_id_get_404(h: ScanHarness) -> None:
    scan_id = h.go(workbook()).json()["scan_id"]
    assert h.status(scan_id).status_code == 200
    problem(h.status(scan_id, h.admin_b()), 404, "NOT_FOUND")
    problem(h.status(str(uuid.uuid4())), 404, "NOT_FOUND")
    problem(h.status("not-a-uuid"), 422, "VALIDATION_ERROR")


def test_n02b_t06_the_same_key_in_another_tenant_is_another_scan(h: ScanHarness) -> None:
    a = h.go(workbook()).json()["scan_id"]
    b = h.go(workbook(), token=h.admin_b()).json()["scan_id"]
    assert a != b
    problem(h.status(a, h.admin_b()), 404, "NOT_FOUND")


# --- T07: no blocking ---------------------------------------------------------------------------


def test_n02b_t07_a_full_clamd_bulkhead_does_not_stop_the_202(
    h: ScanHarness, clamd: FakeClamd
) -> None:
    held = [CLAMD.slot() for _ in range(CLAMD.limit)]
    for slot in held:
        slot.__enter__()
    try:
        assert CLAMD.saturated
        problem(h.upload(workbook()), 503, "SCAN_UNAVAILABLE")  # the synchronous path is stuck
        assert h.go(workbook()).status_code == 202  # the run path never asks for a slot
        assert clamd.scans == 0
    finally:
        for slot in held:
            slot.__exit__(None, None, None)
    assert not CLAMD.saturated and CLAMD.in_use == 0


def test_n02b_t07_many_requests_take_no_bulkhead_slot(h: ScanHarness, clamd: FakeClamd) -> None:
    clamd.delay = 1.0
    seen: list[int] = []
    for n in range(6):
        assert h.go(workbook(), key=f"key-0000-00{n}x").status_code == 202
        seen.append(CLAMD.in_use)
    assert seen == [0] * 6 and clamd.scans == 0


# --- T08: Temporal down -------------------------------------------------------------------------


def test_n02b_t08_temporal_down_is_a_clean_503_with_no_half_state(h: ScanHarness) -> None:
    h.temporal.down = True
    res = h.go(workbook())
    problem(res, 503, "ORCHESTRATOR_UNAVAILABLE")
    inp = h.started()
    assert h.runs() == 0 and not h.pending(inp).exists()  # no run, the stored bytes are gone
    assert [a.action for a in h.actions("MASTERS_SCAN_ABANDONED")] == ["MASTERS_SCAN_ABANDONED"]
    problem(h.status(str(inp.scan_id)), 404, "NOT_FOUND")  # nothing to follow
    h.temporal.down = False
    again = h.go(workbook())  # the same key starts afresh
    assert again.status_code == 202 and again.json()["scan_id"] == str(inp.scan_id)
    assert h.pending(h.started()).exists() and h.status(str(inp.scan_id)).status_code == 200


# --- T09: clamd down ----------------------------------------------------------------------------


def test_n02b_t09_clamd_down_is_retried_then_failed_with_nothing_imported(
    h: ScanHarness, clamd: FakeClamd
) -> None:
    clamd.behaviour = "drop"
    h.go(workbook())
    with pytest.raises(ApplicationError) as retry:
        h.work()
    assert retry.value.type == "ScanUnavailable" and not retry.value.non_retryable
    assert h.pending(h.started()).exists() and h.entries() == 0  # kept for the next attempt
    clamd.behaviour = "normal"
    assert h.work().state is MastersScanState.DONE  # clamd came back: the retry succeeds


def test_n02b_t09_after_the_last_attempt_the_scan_is_failed_scan_unavailable(
    h: ScanHarness, clamd: FakeClamd
) -> None:
    clamd.behaviour = "drop"
    scan_id = h.go(workbook()).json()["scan_id"]
    result = fail_for(h.deps(), h.started())  # what the workflow calls when the retries are used up
    assert result.state is MastersScanState.FAILED
    error = h.status(scan_id).json()["error"]
    assert (error["status"], error["code"]) == (503, "SCAN_UNAVAILABLE")
    assert "Nothing was imported" in error["detail"] and h.entries() == 0
    assert not h.pending(h.started()).exists()


# --- T10: master documents ----------------------------------------------------------------------


def test_n02b_t10_a_master_document_goes_through_the_same_run(h: ScanHarness) -> None:
    res = h.go_document("rates.xlsx", XLSX)
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["kind"] == "DOCUMENT" and h.clamd.scans == 0
    assert h.work().parse_pending is True
    done = h.status(body["scan_id"]).json()
    assert done["state"] == "DONE" and done["created"] is True and done["import_report"] is None
    doc = done["document"]
    assert (doc["scan_status"], doc["parse_status"]) == ("CLEAN", "PENDING")
    assert doc["sha256"] == hashlib.sha256(XLSX).hexdigest() and doc["master_type"] == "SKILLS"
    again = h.go_document("rates.xlsx", XLSX, key="doc-0000-0002").json()["scan_id"]
    h.work()
    second = h.status(again).json()
    assert second["created"] is False and second["document"]["id"] == doc["id"]
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(MasterDocument)) == 1


def test_n02b_t10_an_infected_document_ends_failed_with_malware(h: ScanHarness) -> None:
    scan_id = h.go_document("policy.yaml", eicar_bytes()).json()["scan_id"]
    assert h.work().state is MastersScanState.FAILED
    error = h.status(scan_id).json()["error"]
    assert (error["status"], error["code"]) == (422, "MALWARE")
    assert len(h.actions("MASTER_DOCUMENT_QUARANTINED")) == 1
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(MasterDocument)) == 0


def test_n02b_t10_the_document_result_equals_the_synchronous_answer(h: ScanHarness) -> None:
    scan_id = h.go_document("policy.yaml", POLICY).json()["scan_id"]
    h.work()
    got = h.status(scan_id).json()["document"]
    sync = h.call(
        "POST",
        "/admin/master-documents",
        h.admin_a(),
        files={"file": ("policy.yaml", POLICY, "application/octet-stream")},
    )
    assert sync.status_code == 200  # the same file is already there
    assert sync.json() == got


# --- T11: the cheap checks ----------------------------------------------------------------------


def test_n02b_t11_the_cheap_checks_still_answer_at_once(h: ScanHarness) -> None:
    problem(h.go_document("tool.exe", b"MZ\x90\x00"), 415, "UNSUPPORTED_TYPE")
    problem(h.go_document("empty.yaml", b""), 422, "VALIDATION_ERROR")
    problem(h.go_document("big.yaml", b"a" * (15 * 1024 * 1024 + 1)), 413, "FILE_TOO_LARGE")
    problem(h.go(b"x" * (15 * 1024 * 1024 + 1)), 413, "FILE_TOO_LARGE")
    assert h.temporal.calls == [] and h.actions("MASTERS_SCAN_ACCEPTED") == []


def test_n02b_t11_prefer_without_an_idempotency_key_is_400(h: ScanHarness) -> None:
    problem(h.go(workbook(), key=None), 400, "VALIDATION_ERROR")
    problem(h.go_document("policy.yaml", POLICY, key=None), 400, "VALIDATION_ERROR")
    problem(h.go(workbook(), key="bad key!"), 400, "VALIDATION_ERROR")
    assert h.temporal.calls == []


def test_n02b_t11_an_archived_or_foreign_project_is_refused_at_once(h: ScanHarness) -> None:
    res = h.call(
        "POST",
        "/admin/masters/import",
        h.admin_a(),
        files={"file": ("m.xlsx", workbook(), XLSX_MIME)},
        data={"mode": "upsert", "project_id": str(uuid.uuid4())},
        headers={**PREFER, "Idempotency-Key": "key-0000-0099"},
    )
    problem(res, 404, "NOT_FOUND")
    assert h.temporal.calls == []


def test_n02b_wants_async_reads_the_prefer_header() -> None:
    assert scans.wants_async("respond-async")
    assert scans.wants_async("return=minimal, Respond-Async; wait=5")
    assert not scans.wants_async(None) and not scans.wants_async("return=minimal")
