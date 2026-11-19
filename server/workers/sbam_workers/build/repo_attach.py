"""The `attach_repo` build stage (#862, WG-28): fetch the repository the build chose and attach its
scan report and overlay to the build.

`choose_repo` (#756) records which repository a Sub-Skill starts from but fetches nothing. This
stage runs once per build, right after it, when the decision is a repository:

1. The fetch and scan of #755 (pinned commit, no script run, licence, vulnerabilities, malware).
   The stored `RepoScanReport` is content-addressed under the project, so the workbench's
   repository panel (#843) and the compliance gates (#761) find it by URL and pinned commit.
2. When the tree was vendored, the patch overlay record of #757 (digest of the untouched tree, no
   patch yet) is built from the stored tree and written to the audit trail (`repo_attached`).
   `build_manifest` puts it into the Skill manifest's `build.repos`.

A refused repository (licence, malware, host, unsafe tree, clone failure) is a normal outcome: it
is recorded with its report and the build goes on generating its own code. A step that could not
answer (git, ClamAV, storage) raises and Temporal retries it. A retry or a replay finds the record.
"""

import asyncio
import io
import tarfile
from collections.abc import Callable
from functools import lru_cache
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict
from sbam_api.audit import repository as audit
from sbam_api.builds.repo_attached import (
    ATTACHED_ACTION,
    ENTITY,
    AttachStatus,
    RepoAttached,
    find,
    key_of,
)
from sbam_api.scan.clamd import ClamdClient
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_contracts.build import BuildStageInput
from sbam_contracts.isatvon14 import RepoUrl
from sbam_contracts.repo_scan import FetchDecision, RepoFetchInput, RepoFetchOutput, RepoScanReport
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for
from sbam_workers.repo_fetch import activity as fetcher
from sbam_workers.repo_fetch.overlay import build_overlay
from sbam_workers.repo_fetch.scan import Clam
from sbam_workers.settings import WorkerSettings

STAGE: Final = "attach_repo"
BUNDLE_MISSING: Final = "RepoBundleMissing"  # the stored tree the report names is not there


class AttachRepoInput(BuildStageInput):
    """`url` is the repository the decision chose (it came from the ISATVON, the Admin masters or
    the FDE, so it is the only URL the fetcher may use)."""

    url: RepoUrl


class AttachRepoResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AttachStatus
    commit: str | None = None
    report_sha256: str


def _result(found: RepoAttached) -> AttachRepoResult:
    return AttachRepoResult(
        status=found.status, commit=found.commit, report_sha256=found.report_sha256
    )


Fetch = Callable[[RepoFetchInput, StorageBackend, Clam], RepoFetchOutput]


def read_tree(storage: StorageBackend, scope: StorageScope, bundle_sha256: str) -> dict[str, bytes]:
    """The vendored files of a stored tree (the deterministic tar `store_bundle` wrote)."""
    try:
        data = storage.get(scope.key(Kind.REPOS, bundle_sha256))
    except FileNotFoundError:
        raise ApplicationError(
            "the stored repository tree is missing", type=BUNDLE_MISSING, non_retryable=True
        ) from None
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar.getmembers():
            handle = tar.extractfile(member) if member.isfile() else None
            if handle is not None:
                files[member.name] = handle.read()
    return files


class AttachStore(Protocol):
    def get(self, inp: BuildStageInput) -> RepoAttached | None: ...

    def put(self, inp: BuildStageInput, found: RepoAttached) -> None: ...


class SqlAttachStore:
    """`repo_attached` on the audit trail: one row per (Sub-Skill, build), never edited."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(self, inp: BuildStageInput) -> RepoAttached | None:
        with self.sessions() as session:
            return find(session, inp.tenant_id, inp.sub_skill_id, inp.build_no)

    def put(self, inp: BuildStageInput, found: RepoAttached) -> None:
        with self.sessions() as session:
            if find(session, inp.tenant_id, inp.sub_skill_id, inp.build_no) is None:
                audit.record(
                    session,
                    tenant_id=inp.tenant_id,
                    actor_id=SYSTEM_ACTOR_ID,
                    action=ATTACHED_ACTION,
                    entity_type=ENTITY,
                    entity_id=str(inp.sub_skill_id),
                    correlation_id=inp.correlation_id,
                    after=found.model_dump(mode="json"),
                    idempotency_key=key_of(inp.build_no),
                )
                session.commit()


def attach_for(
    inp: AttachRepoInput,
    storage: StorageBackend,
    clam: Clam,
    store: AttachStore,
    fetch: Fetch = fetcher.run,
) -> AttachRepoResult:
    """The whole stage, synchronous (tests call it directly; the activity runs it in a thread)."""
    recorded = store.get(inp)
    if recorded is not None:  # an activity retry, a replay, or a new cycle of the same build
        return _result(recorded)
    scope = StorageScope(inp.tenant_id, inp.project_id)
    out = fetch(
        RepoFetchInput(
            tenant_id=inp.tenant_id,
            project_id=inp.project_id,
            run_id=inp.run_id,
            correlation_id=inp.correlation_id,
            url=inp.url,
            approved_urls=[inp.url],
        ),
        storage,
        clam,
    )
    if out.decision is not FetchDecision.VENDORED or out.commit is None or not out.bundle_sha256:
        refused = RepoAttached(
            status=AttachStatus.REFUSED,
            url=inp.url,
            commit=out.commit,
            report_sha256=out.report_sha256,
        )
        store.put(inp, refused)
        return _result(refused)
    report = RepoScanReport.model_validate_json(
        storage.get(scope.key(Kind.REPOS, out.report_sha256))
    )
    overlay = build_overlay(
        url=inp.url,
        commit=out.commit,
        vendor=read_tree(storage, scope, out.bundle_sha256),
        licence=None if report.licence is None else report.licence.spdx,
        scan_report_sha256=out.report_sha256,
    ).overlay
    attached = RepoAttached(
        status=AttachStatus.ATTACHED,
        url=inp.url,
        commit=out.commit,
        report_sha256=out.report_sha256,
        overlay=overlay.model_dump(mode="json"),
    )
    store.put(inp, attached)
    return _result(attached)


@lru_cache(maxsize=1)
def _store() -> SqlAttachStore:
    return SqlAttachStore(mirror.sessions())


@activity_for(TaskQueue.BUILD)
@activity.defn(name=STAGE)
async def attach_repo(inp: AttachRepoInput) -> AttachRepoResult:
    """Fetch and scan the chosen repository; attach its report and overlay to the build (#862)."""
    settings = WorkerSettings()  # type: ignore[call-arg]
    clam = ClamdClient(settings.clamav_host, settings.clamav_port, settings.clamav_timeout_seconds)
    return await asyncio.to_thread(attach_for, inp, make_backend(settings), clam, _store())
