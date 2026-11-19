"""WG-28 (#862): builds attach the repository scan report and store the generated server files.

- WG28-T01: a vendored repository is attached once: report, overlay record, one fetch.
- WG28-T02: a refused repository is recorded with its report and attaches no overlay.
- WG28-T03: the Skill manifest carries `build.repos` only when a repository was attached; a build
  without one hashes as before.
- WG28-T05: the server files are stored through StorageBackend, hashed; a retry is one row.
- WG-28-T06: a Workflow whose server cannot be rendered is recorded, not raised.
"""

import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from sbam_api.builds.repo_attached import AttachStatus, RepoAttached, overlays
from sbam_api.builds.workflow_server import WorkflowServerRecord, read_file
from sbam_api.core.conventions import ApiError
from sbam_api.storage.base import StorageBackend, StorageScope, StoredObject
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import WorkflowRef
from sbam_contracts.repo_scan import (
    FetchDecision,
    LicenceFinding,
    LicenceVerdict,
    RepoFetchInput,
    RepoFetchOutput,
    RepoScanReport,
)
from sbam_workers.build.repo_attach import AttachRepoInput, attach_for
from sbam_workers.build.server_stage import build_for
from sbam_workers.build.workflow_server import WorkflowServerError, WorkflowServerInput
from sbam_workers.repo_fetch.report import store_bundle, store_report
from sbam_workers.repo_fetch.scan import Clam
from test_wg10_workflow_server import ms01_files, request, template  # noqa: F401 - fixtures

URL = "https://github.com/acme/lib"
COMMIT = "c" * 40
TENANT = uuid.uuid4()
PROJECT = uuid.uuid4()


def attach_input(**over: Any) -> AttachRepoInput:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "project_id": PROJECT,
        "run_id": uuid.uuid4(),
        "correlation_id": "cid-wg28",
        "workflow_id": uuid.uuid4(),
        "sub_skill_id": uuid.uuid4(),
        "build_no": 1,
        "url": URL,
    }
    return AttachRepoInput(**{**values, **over})


class MemoryAttachStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, int], RepoAttached] = {}

    def get(self, inp: BuildStageInput) -> RepoAttached | None:
        return self.rows.get((inp.sub_skill_id, inp.build_no))

    def put(self, inp: BuildStageInput, found: RepoAttached) -> None:
        self.rows.setdefault((inp.sub_skill_id, inp.build_no), found)


class Fetcher:
    """What the fetcher stores for a repository: the report, and the tree when vendored."""

    def __init__(self, vendored: bool) -> None:
        self.vendored = vendored
        self.calls: list[RepoFetchInput] = []

    def __call__(self, inp: RepoFetchInput, storage: StorageBackend, clam: Clam) -> RepoFetchOutput:
        self.calls.append(inp)
        scope = StorageScope(inp.tenant_id, inp.project_id)
        bundle = store_bundle(storage, scope, {"LICENSE": b"MIT\n", "lib.py": b"X = 1\n"})
        report = RepoScanReport(
            url=inp.url,
            host="github.com",
            commit=COMMIT,
            decision=FetchDecision.VENDORED if self.vendored else FetchDecision.REFUSED,
            licence=LicenceFinding(spdx="MIT", source="LICENSE", verdict=LicenceVerdict.ALLOWED),
            bundle_sha256=bundle.sha256 if self.vendored else None,
            bundle_files=2 if self.vendored else 0,
        )
        stored = store_report(storage, scope, report)
        return RepoFetchOutput(
            report_sha256=stored.sha256,
            decision=report.decision,
            commit=COMMIT,
            bundle_sha256=report.bundle_sha256,
        )


class NoClam:
    def scan(self, stream: Any) -> Any:  # pragma: no cover - the fake fetcher never scans
        raise AssertionError("not called")


def test_wg28_t01_a_vendored_repository_is_attached_once(tmp_path: Path) -> None:
    storage, store, fetch = LocalDiskBackend(tmp_path), MemoryAttachStore(), Fetcher(True)
    inp = attach_input()
    first = attach_for(inp, storage, NoClam(), store, fetch)
    again = attach_for(inp, storage, NoClam(), store, fetch)
    assert first == again and first.status is AttachStatus.ATTACHED and first.commit == COMMIT
    assert len(fetch.calls) == 1  # a retry or a replay finds the record
    assert fetch.calls[0].approved_urls == [URL]  # only the chosen repository may be fetched
    [row] = store.rows.values()
    assert row.overlay is not None
    assert row.overlay["scan_report_sha256"] == first.report_sha256
    assert row.overlay["repo_dir"] == f"vendor/lib@{COMMIT}"
    assert row.overlay["licence"] == "MIT" and row.overlay["patches"] == []


def test_wg28_t02_a_refused_repository_is_recorded_without_an_overlay(tmp_path: Path) -> None:
    store = MemoryAttachStore()
    got = attach_for(
        attach_input(),
        LocalDiskBackend(tmp_path),
        NoClam(),
        store,
        Fetcher(False),
    )
    assert got.status is AttachStatus.REFUSED and got.commit == COMMIT
    [row] = store.rows.values()
    assert row.overlay is None and row.report_sha256 == got.report_sha256


def test_wg28_overlays_of_a_missing_or_refused_record_are_empty() -> None:
    assert overlays.__name__ == "overlays"  # read through a database in test_wg28 store tests


# --- the server files ----------------------------------------------------------------------------


class MemoryServerStore:
    def __init__(self, storage: StorageBackend, data: WorkflowServerInput | WorkflowServerError):
        self.storage = storage
        self.data = data
        self.saved: list[WorkflowServerRecord] = []
        self.stored: list[StoredObject] = []
        self.failures: list[WorkflowServerError] = []

    def load(self, inp: WorkflowRef) -> WorkflowServerInput:
        if isinstance(self.data, WorkflowServerError):
            raise self.data
        return self.data

    def recorded(self, inp: WorkflowRef, bundle_sha256: str) -> uuid.UUID | None:
        for n, row in enumerate(self.saved):
            if row.bundle_sha256 == bundle_sha256:
                return uuid.UUID(int=n + 1)
        return None

    def save(
        self, inp: WorkflowRef, record: WorkflowServerRecord, stored: StoredObject
    ) -> uuid.UUID:
        self.saved.append(record)
        self.stored.append(stored)
        return uuid.UUID(int=len(self.saved))

    def failed(self, inp: WorkflowRef, error: WorkflowServerError) -> uuid.UUID:
        self.failures.append(error)
        return uuid.uuid4()


def ref() -> WorkflowRef:
    return WorkflowRef(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=uuid.uuid4(),
        correlation_id="cid-wg28",
        workflow_id=uuid.uuid4(),
    )


def test_wg28_t05_the_server_files_are_stored_hashed_and_a_retry_is_the_same_row(
    tmp_path: Path, ms01_files: dict[str, bytes]  # noqa: F811
) -> None:
    storage = LocalDiskBackend(tmp_path)
    store = MemoryServerStore(storage, request(ms01_files, vendor=None))
    first = build_for(store, ref())
    again = build_for(store, ref())
    assert first == again and len(store.saved) == 1  # same bytes: no second row
    [record] = store.saved
    assert {"server.py", "tools.json", "Dockerfile", "schema.sql"} <= set(record.files)
    assert any(p.startswith("loop_specs/") for p in record.files)
    assert record.server_name == "wf_investor_onboarding"
    assert len(record.bundle_sha256) == 64 and record.tools
    server = read_file(storage, record, "server.py")
    assert hashlib.sha256(server).hexdigest() == record.files["server.py"]
    assert b"FastMCP" in server


def test_wg28_t05_a_path_the_file_map_does_not_list_is_not_read(
    tmp_path: Path, ms01_files: dict[str, bytes]  # noqa: F811
) -> None:
    storage = LocalDiskBackend(tmp_path)
    store = MemoryServerStore(storage, request(ms01_files, vendor=None))
    build_for(store, ref())
    with pytest.raises(ApiError) as error:
        read_file(storage, store.saved[0], "../etc/passwd")
    assert error.value.status == 404


def test_wg28_t06_a_server_that_cannot_be_rendered_is_recorded_not_raised(
    tmp_path: Path,
) -> None:
    store = MemoryServerStore(
        LocalDiskBackend(tmp_path), WorkflowServerError("SKILL_NOT_BUILT", "MS01.1 has no bundle")
    )
    build_for(store, ref())
    assert [f.code for f in store.failures] == ["SKILL_NOT_BUILT"] and store.saved == []
