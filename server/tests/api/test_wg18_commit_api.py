"""WG-18 (#766) Commit: the routes through the real app and auth chain, and the activities' steps.

T01 a stale hash is 409; T02 a missing secret blocks Commit; T03 a replaced secret is tested first;
T04 the registered Staging digest is the certified digest and the runtime is asked to start it;
T05 a crash mid-commit and a re-run completes it. (T06, replay, is in tests/temporal.)

Bifrost is a fake, the Staging runtime (#765, not built) is a fake, Temporal is a fake client that
records what is started: no test here makes a real call.
"""

import hashlib
import json
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sbam_api.audit import repository as audit
from sbam_api.audit.models import AuditLog
from sbam_api.builds import workflow_image, workflow_server, workflow_ui
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationLevel, CertificationRow
from sbam_api.connections.models import ConnectionRow
from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.manifests.models import GateResultRow, WorkflowManifestRow
from sbam_api.manifests.ui_bundle import record_ui_bundle_gate
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.temporal.models import TemporalRun
from sbam_api.workflow_commit import checks, service
from sbam_api.workflow_commit.ports import StateMachineFreezer
from sbam_api.workflow_registry.bifrost import RegistryEntry, RegistryUnavailable
from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_api.workflows.models import Workflow
from sbam_contracts.canonical import canonical_json
from sbam_contracts.manifest import TemplateRef, UiBundleEntry, WorkflowManifest
from sbam_contracts.workflow_commit import (
    CommitCheckName,
    CommitState,
    StagingStartRequest,
    StagingStartResult,
    StagingStartStatus,
    WorkflowCommitInput,
)
from sbam_contracts.workflow_registry import RegistryKind
from sbam_workers.commit.activities import REFUSED, CommitSteps
from sbam_workers.repo_fetch.report import bundle_tar
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_project_workspace import Harness
from test_wg16_connections_api import (
    KEYS,
    LEDGER,
    WORKFLOW,
    Net,
    put_value,
    seed_workflow,
    set_test_target,
)
from test_wg16_connections_api import base as connections_base

# The sealed Workflow manifest and the fake Bifrost of WG-12 live with its unit tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_wg12_workflow_registry import (
    DIGEST,
    TOOLS,
    UI_DIGEST,
    FakeBifrost,
    sealed,
    server_entry,
)

KEY = "commit-key-0001"


@dataclass
class FakeRun:
    result_run_id: str = "run-1"
    first_execution_run_id: str = "run-1"


@dataclass
class FakeClient:
    started: list[tuple[str, Any, str]] = field(default_factory=list)

    async def start_workflow(self, workflow: str, inp: Any, *, id: str, **_: Any) -> FakeRun:
        self.started.append((workflow, inp, id))
        return FakeRun()


class FakeRuntime:
    def __init__(self, status: StagingStartStatus = StagingStartStatus.STARTED) -> None:
        self.status = status
        self.requests: list[StagingStartRequest] = []

    def start(self, request: StagingStartRequest) -> StagingStartResult:
        self.requests.append(request)
        return StagingStartResult(status=self.status, detail=None)


def ctx(h: Harness) -> TenantContext:
    return TenantContext(TENANT_A, uuid.UUID(h.project))


def commit_url(h: Harness, workflow: uuid.UUID = WORKFLOW) -> str:
    return f"/projects/{h.project}/workflows/{workflow}/commit"


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    """WG-16's harness (connections, a fake outside world) plus the tables Commit reads."""
    harness = Harness(tmp_path)
    bind = harness.sessions.kw["bind"]
    for table in (ConnectionRow, Workflow, SubSkill):
        table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
    state = harness.client.app.state  # type: ignore[attr-defined]
    state.settings.secrets_keys = SecretStr(KEYS)
    state.connection_net = Net()
    state.connection_probe = state.connection_net.probe
    state.storage = LocalDiskBackend(tmp_path / "store")  # the UI bundles of #768
    yield harness


def storage(h: Harness) -> StorageBackend:
    found: StorageBackend = h.client.app.state.storage  # type: ignore[attr-defined]
    return found


def seed(h: Harness, *secrets: str) -> None:
    """The Workflow of WG-16's tests (one Sub-Skill naming `secrets`) and the Commit tables."""
    seed_workflow(h, *secrets)
    bind = h.sessions.kw["bind"]
    for table in (
        WorkflowManifestRow,
        CertificationRow,
        GateResultRow,
        CodeAttempt,
        RegisteredWorkflowRow,
        TemporalRun,
    ):
        table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]


BUNDLE = {"index.html": b"<html></html>", "app.js": b"console.log(1)"}


def store_bundle(h: Harness) -> workflow_ui.WorkflowUiRecord:
    """A UI bundle in storage and the record `build_workflow_ui` writes for it (#768)."""
    files = {p: hashlib.sha256(d).hexdigest() for p, d in BUNDLE.items()}
    digest = "sha256:" + hashlib.sha256(canonical_json(dict(sorted(files.items())))).hexdigest()
    stored = storage(h).put(
        bundle_tar(BUNDLE),
        StorageScope(TENANT_A, uuid.UUID(h.project), WORKFLOW),
        Kind.CODE,
        "application/x-tar",
    )
    return workflow_ui.WorkflowUiRecord(
        entry=UiBundleEntry(
            version="1.0.0",
            digest=digest,
            entry_file="index.html",
            allowed_hosts=["api.example.com"],
            workflow_server="wf_investor_onboarding",
        ),
        template=TemplateRef(name="workflow-ui", version="1.0.0", sha256="a" * 64),
        tar_key=stored.storage_key,
        files=files,
    )


def store_image(h: Harness) -> workflow_image.WorkflowImageRecord:
    """The server files and the image row `build_workflow_image` writes (#867)."""
    server = workflow_server.WorkflowServerRecord(
        server_name="wf_investor_onboarding",
        bundle_sha256="9" * 64,
        tar_key="none",
        files={},
        tools=list(TOOLS),
        base_layer_key="b",
        top_layer_key="t",
    )
    image = workflow_image.WorkflowImageRecord(
        bundle_sha256="9" * 64, image_digest=DIGEST, builder="docker", entry=server_entry()
    )
    with h.sessions() as s:
        for action, after in (
            (workflow_server.BUILT_ACTION, server.model_dump(mode="json")),
            (workflow_image.BUILT_ACTION, image.model_dump(mode="json")),
        ):
            audit.record(
                s,
                tenant_id=TENANT_A,
                actor_id="SYSTEM",
                action=action,
                entity_type="workflow",
                entity_id=str(WORKFLOW),
                correlation_id="cid-867",
                after=after,
            )
        s.commit()
    return image


def certify(
    h: Harness, *, ui: bool = True, build_no: int = 1, recorded: bool = False, imaged: bool = False
) -> str:
    """A CERTIFIED Workflow whose Sub-Skill is certified on its code, with a sealed manifest,
    the WORKFLOW certificate and the UI_BUNDLE evidence. Returns the combined manifest hash."""
    project = uuid.UUID(h.project)
    manifest = sealed("1.0.0", DIGEST)
    record: workflow_ui.WorkflowUiRecord | None = None
    image: workflow_image.WorkflowImageRecord | None = None
    if (
        recorded or imaged
    ):  # as the build leaves it: the entries are in audit rows, not the manifest
        record = store_bundle(h)
        drop = {"ui_bundle", "mcp_server"} if imaged else {"ui_bundle"}
        manifest = WorkflowManifest.sealed(
            {k: v for k, v in manifest.dump().items() if k not in drop}
        )
    if imaged:
        image = store_image(h)
    sha = manifest.integrity.content_sha256
    with h.sessions() as s:
        workflow = s.get(Workflow, WORKFLOW)
        assert workflow is not None
        workflow.state = "CERTIFIED"
        sub = s.scalars(select(SubSkill).where(SubSkill.workflow_id == WORKFLOW)).one()
        sub.state = "CERTIFIED"
        s.add(
            CodeAttempt(
                tenant_id=TENANT_A,
                project_id=project,
                workflow_id=WORKFLOW,
                sub_skill_id=sub.id,
                build_no=1,
                cycle=0,
                attempt=0,
                template_name="t",
                template_version="1.0.0",
                template_sha256="0" * 64,
                status="ASSEMBLED",
                bodies={},
                trace_id="trace",
                bundle_sha256="e" * 64,
            )
        )
        s.add(
            WorkflowManifestRow(
                tenant_id=TENANT_A,
                project_id=project,
                workflow_id=WORKFLOW,
                build_no=build_no,
                manifest=json.loads(json.dumps(manifest.dump())),
                sha256=sha,
                is_frozen=False,
            )
        )
        for level, subject, content in (
            (CertificationLevel.WORKFLOW, WORKFLOW, sha),
            (CertificationLevel.SUB_SKILL, sub.id, "sha256:" + "e" * 64),
        ):
            s.add(
                CertificationRow(
                    tenant_id=TENANT_A,
                    project_id=project,
                    level=level.value,
                    subject_id=subject,
                    content_sha256=content,
                    evidence={},
                    certified_at=sub.created_at,
                )
            )
        if record is not None:
            base = workflow_image.manifest_with_image(manifest.dump(), image) if image else manifest
            built = WorkflowManifest.sealed({**base.dump(), "ui_bundle": record.entry.dump()})
            audit.record(
                s,
                tenant_id=TENANT_A,
                actor_id="SYSTEM",
                action=workflow_ui.BUILT_ACTION,
                entity_type="workflow",
                entity_id=str(WORKFLOW),
                correlation_id="cid-768",
                after=record.model_dump(mode="json"),
            )
            if ui:
                record_ui_bundle_gate(s, ctx(h), WORKFLOW, built, record.digest)
        elif ui and manifest.ui_bundle is not None:
            record_ui_bundle_gate(s, ctx(h), WORKFLOW, manifest, manifest.ui_bundle.digest)
        s.commit()
    return str(sha)


@pytest.fixture
def client(h: Harness) -> FakeClient:
    fake = FakeClient()
    h.client.app.state.temporal = fake  # type: ignore[attr-defined]
    return fake


def post(h: Harness, sha: str | None, key: str | None = KEY, token: str | None = None) -> Any:
    headers = {} if key is None else {"Idempotency-Key": key}
    if sha is not None:
        headers["If-Match"] = f'"{sha}"'
    return h.call("POST", commit_url(h), token or h.fde(), headers=headers)


def trail(h: Harness, action: str) -> list[AuditLog]:
    with h.sessions() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        return list(rows)


# --- the Validation screen's read ---------------------------------------------------------------


def test_wg18_status_lists_every_check_and_the_etag_is_the_combined_hash(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h)
    res = h.call("GET", commit_url(h), h.fde())
    assert res.status_code == 200, res.text
    body = res.json()
    assert res.headers["etag"] == f'"{sha}"' and body["manifest_hash"] == sha
    assert [c["name"] for c in body["checks"]] == [c.value for c in CommitCheckName]
    assert body["ready"] is True and all(c["passed"] for c in body["checks"])
    assert body["state"] == "NOT_COMMITTED" and body["registration"] is None


def test_wg18_a_workflow_that_was_never_certified_says_so(h: Harness) -> None:
    seed(h)  # FROZEN workflow, no manifest, no certificates: the #132 stubs' gap
    body = h.call("GET", commit_url(h), h.fde()).json()
    assert body["ready"] is False and body["manifest_hash"] is None
    failed = {c["name"]: c["reason"] for c in body["checks"] if not c["passed"]}
    assert "WORKFLOW_CERTIFIED" in failed and "MCP_SERVER" in failed
    assert "no Workflow manifest" in failed["WORKFLOW_CERTIFIED"]


def test_wg18_a_rebuilt_sub_skill_and_missing_ui_evidence_are_named(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    certify(h, ui=False)
    with h.sessions() as s:
        attempt = s.scalars(select(CodeAttempt)).one()
        attempt.bundle_sha256 = "f" * 64  # the code changed after the certificate
        s.commit()
    body = h.call("GET", commit_url(h), h.fde()).json()
    failed = {c["name"]: c["reason"] for c in body["checks"] if not c["passed"]}
    assert set(failed) == {"SUB_SKILLS_CERTIFIED", "UI_BUNDLE"}
    assert "MS01.1_Ledger" in failed["SUB_SKILLS_CERTIFIED"]
    assert failed["UI_BUNDLE"] == "MISSING_EVIDENCE:UI_BUNDLE"


# --- WG18-T01: a stale hash is 409 ---------------------------------------------------------------


def test_wg18_t01_stale_hash_returns_409_and_starts_nothing(h: Harness, client: FakeClient) -> None:
    seed(h)
    certify(h)
    res = post(h, "sha256:" + "0" * 64)
    problem(res, 409, "VERSION_CONFLICT")
    assert client.started == [] and trail(h, service.REQUESTED) == []


def test_wg18_commit_needs_if_match_and_an_idempotency_key(h: Harness, client: FakeClient) -> None:
    seed(h)
    sha = certify(h)
    problem(post(h, None), 428, "PRECONDITION_REQUIRED")
    problem(post(h, sha, key=None), 400, "VALIDATION_ERROR")
    assert client.started == []


# --- WG18-T02 / T03: the secrets gate ------------------------------------------------------------


def test_wg18_t02_a_missing_secret_blocks_commit_with_its_name(
    h: Harness, client: FakeClient
) -> None:
    seed(h, LEDGER)
    sha = certify(h)
    res = post(h, sha)
    problem(res, 409, "CONFLICT")
    assert "SECRETS" in res.json()["detail"] and LEDGER in res.json()["detail"]
    status = h.call("GET", commit_url(h), h.fde()).json()
    assert status["blockers"] == [{"name": LEDGER, "reason": "MISSING"}] and not status["ready"]
    assert client.started == [] and trail(h, service.REQUESTED) == []


def test_wg18_t03_a_replaced_secret_must_be_tested_before_commit_goes_on(
    h: Harness, client: FakeClient
) -> None:
    seed(h, LEDGER)
    sha = certify(h)
    put_value(h)
    set_test_target(h)
    assert h.call("POST", f"{connections_base(h)}/{LEDGER}/test", h.fde()).json()["outcome"] == "OK"
    assert h.call("GET", commit_url(h), h.fde()).json()["ready"] is True
    etag = h.call("GET", f"{connections_base(h)}/{LEDGER}", h.fde()).headers["etag"]
    assert put_value(h, value="a-different-value-0123456789", if_match=etag).status_code == 200
    blocked = h.call("GET", commit_url(h), h.fde()).json()
    assert blocked["blockers"] == [{"name": LEDGER, "reason": "NOT_TESTED"}]
    problem(post(h, sha), 409, "CONFLICT")
    assert h.call("POST", f"{connections_base(h)}/{LEDGER}/test", h.fde()).status_code == 200
    assert post(h, sha).status_code == 202


# --- the request ---------------------------------------------------------------------------------


def test_wg18_a_commit_is_audited_and_starts_one_own_workflow(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h)
    res = post(h, sha)
    assert res.status_code == 202, res.text
    run = res.json()["run"]
    assert res.json()["manifest_hash"] == sha and run["type"] == "WorkflowCommit"
    [(name, inp, wid)] = client.started
    assert (name, wid) == ("WorkflowCommit", f"workflow-commit-{WORKFLOW}")
    assert inp.manifest_hash == sha and inp.workflow_id == WORKFLOW
    [row] = trail(h, service.REQUESTED)
    assert (row.after or {})["manifest_hash"] == sha and row.actor_id != "SYSTEM"
    assert h.call("GET", commit_url(h), h.fde()).json()["state"] == "COMMITTING"


def test_wg18_the_same_key_answers_the_same_run_and_another_hash_is_refused(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h)
    first, again = post(h, sha), post(h, sha)
    assert first.status_code == again.status_code == 202
    assert len(trail(h, service.REQUESTED)) == 1
    problem(post(h, "sha256:" + "0" * 64), 422, "IDEMPOTENCY_KEY_REUSED")


def test_wg18_idor_another_tenant_an_unassigned_fde_and_a_foreign_workflow_get_404(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h)
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other_tenant, h.realm.token(FDE_A2)):
        problem(h.call("GET", commit_url(h), token), 404, "NOT_FOUND")
        problem(post(h, sha, token=token), 404, "NOT_FOUND")
    problem(h.call("GET", commit_url(h, uuid.uuid4()), h.fde()), 404, "NOT_FOUND")
    foreign = h.call(
        "POST",
        commit_url(h, uuid.uuid4()),
        h.fde(),
        headers={"Idempotency-Key": KEY, "If-Match": f'"{sha}"'},
    )
    problem(foreign, 404, "NOT_FOUND")
    assert client.started == [] and trail(h, service.REQUESTED) == []


def test_wg18_an_admin_cannot_commit(h: Harness, client: FakeClient) -> None:
    seed(h)
    sha = certify(h)
    res = post(h, sha, token=h.admin_a())
    assert res.status_code in (403, 404) and client.started == []


# --- the activities' steps: WG18-T04 and T05 ------------------------------------------------------


class Flaky(FakeBifrost):
    """A Bifrost that does not answer until `up` is set."""

    up = False

    def register(self, name: str, address: str) -> RegistryEntry:
        if not self.up:
            raise RegistryUnavailable("Bifrost did not answer")
        return super().register(name, address)


def steps(h: Harness, fake: FakeBifrost, runtime: FakeRuntime) -> CommitSteps:
    return CommitSteps(h.sessions, storage(h), lambda: fake, StateMachineFreezer(), runtime)


def commit_input(h: Harness, sha: str) -> WorkflowCommitInput:
    return WorkflowCommitInput(
        tenant_id=TENANT_A,
        project_id=uuid.UUID(h.project),
        run_id=uuid.uuid4(),
        correlation_id="cid-wg18",
        workflow_id=WORKFLOW,
        manifest_hash=sha,
        actor_id="fde-a1",
    )


def run_all(s: CommitSteps, inp: WorkflowCommitInput) -> Any:
    s.recheck(inp)
    s.freeze(inp)
    s.register(inp)
    return s.start_staging(inp)


def test_wg18_t04_the_staging_entry_has_the_certified_digest_and_that_is_what_starts(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h)
    fake, runtime = FakeBifrost(TOOLS), FakeRuntime()
    result = run_all(steps(h, fake, runtime), commit_input(h, sha))
    assert result.image_digest == DIGEST and result.manifest_hash == sha
    assert result.registry_name == "wf_investor_onboarding_v1_0_stg"
    [request] = runtime.requests
    assert request.image_digest == DIGEST and request.ui_bundle_digest == UI_DIGEST
    assert request.secret_names == [] and request.requested_by == "fde-a1"
    with h.sessions() as s:
        [entry] = s.scalars(select(RegisteredWorkflowRow)).all()
        assert (entry.kind, entry.image_digest) == (RegistryKind.STAGING.value, DIGEST)
        assert s.get(Workflow, WORKFLOW).state == "FROZEN"  # type: ignore[union-attr]
        frozen = s.scalars(select(WorkflowManifestRow)).one()
        assert frozen.is_frozen is True
    assert [name for name, _ in fake.writes()] == ["register"]
    status = h.call("GET", commit_url(h), h.fde()).json()
    assert status["state"] == CommitState.COMMITTED.value
    assert status["registration"]["image_digest"] == DIGEST
    assert status["registration"]["kind"] == "staging"
    assert status["staging"]["status"] == "STARTED" and status["committed_by"] == "fde-a1"
    [row] = trail(h, service.COMMITTED)
    assert row.actor_id == "fde-a1" and (row.after or {})["image_digest"] == DIGEST


def test_wg18_t05_a_crash_after_the_freeze_is_finished_by_running_commit_again(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h)
    inp = commit_input(h, sha)
    down = Flaky(TOOLS)
    runtime = FakeRuntime()
    first = steps(h, down, runtime)
    first.recheck(inp)
    first.freeze(inp)
    with pytest.raises(RegistryUnavailable):
        first.register(inp)
    with h.sessions() as s:
        assert s.get(Workflow, WORKFLOW).state == "FROZEN"  # type: ignore[union-attr]
        assert s.scalars(select(RegisteredWorkflowRow)).all() == []
    down.up = True
    result = run_all(steps(h, down, runtime), inp)  # the whole run again: every step finds its work
    assert result.staging.status is StagingStartStatus.STARTED
    again = run_all(steps(h, down, runtime), inp)  # and a third time changes nothing
    assert again.registry_name == result.registry_name
    with h.sessions() as s:
        assert len(s.scalars(select(RegisteredWorkflowRow)).all()) == 1
    assert len(trail(h, service.COMMITTED)) == 1
    assert [call for call in down.calls if call[0] == "register"] == [
        ("register", result.registry_name)
    ]


def test_wg18_a_stale_hash_in_the_worker_is_refused_without_a_retry(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    certify(h)
    with pytest.raises(ApplicationError) as caught:
        steps(h, FakeBifrost(TOOLS), FakeRuntime()).recheck(commit_input(h, "sha256:" + "0" * 64))
    assert caught.value.type == REFUSED and caught.value.non_retryable is True


def test_wg18_a_runtime_that_is_not_built_yet_is_recorded_not_hidden(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h)
    runtime = FakeRuntime(StagingStartStatus.PENDING_RUNTIME)
    result = run_all(steps(h, FakeBifrost(TOOLS), runtime), commit_input(h, sha))
    assert result.staging.status is StagingStartStatus.PENDING_RUNTIME
    staging = h.call("GET", commit_url(h), h.fde()).json()["staging"]
    assert staging["status"] == "PENDING_RUNTIME"


def test_wg18_a_runtime_that_fails_ends_the_run_but_keeps_the_commit(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h)
    with pytest.raises(ApplicationError) as caught:
        run_all(
            steps(h, FakeBifrost(TOOLS), FakeRuntime(StagingStartStatus.FAILED)),
            commit_input(h, sha),
        )
    assert caught.value.type == "StagingStartFailed"
    status = h.call("GET", commit_url(h), h.fde()).json()
    assert status["state"] == "COMMITTED" and status["staging"]["status"] == "FAILED"


def test_wg18_freezing_never_certifies_a_workflow_that_is_not_certified(h: Harness) -> None:
    seed(h)
    sha = certify(h)
    with h.sessions() as s:
        workflow = s.get(Workflow, WORKFLOW)
        assert workflow is not None
        workflow.state = "BUILDING"
        s.commit()
    with pytest.raises(ApplicationError):
        steps(h, FakeBifrost(TOOLS), FakeRuntime()).freeze(commit_input(h, sha))
    with h.sessions() as s, pytest.raises(ApiError):
        checks.evaluate(s, ctx(h), uuid.uuid4(), storage(h))  # a Workflow of nobody is a 404


def test_wg18_the_checks_write_nothing(h: Harness) -> None:
    seed(h, LEDGER)
    certify(h)
    with h.sessions() as s:
        checks.evaluate(s, ctx(h), WORKFLOW, storage(h))
        assert not s.new and not s.dirty and not s.deleted


# --- the UI bundle of #768: Commit merges its entry and refuses a bundle that changed -------


def test_wg18_the_recorded_bundle_entry_is_merged_into_the_frozen_manifest(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h, recorded=True)
    record = workflow_ui.newest(h.sessions(), TENANT_A, WORKFLOW)
    assert record is not None
    assert h.call("GET", commit_url(h), h.fde()).json()["ready"] is True
    runtime = FakeRuntime()
    run_all(steps(h, FakeBifrost(TOOLS), runtime), commit_input(h, sha))
    with h.sessions() as s:
        rows = list(s.scalars(select(WorkflowManifestRow).order_by(WorkflowManifestRow.build_no)))
        assert [r.is_frozen for r in rows] == [False, True]
        frozen = WorkflowManifest.model_validate_json(json.dumps(rows[1].manifest))
        assert frozen.ui_bundle is not None and frozen.ui_bundle.digest == record.digest
        [entry] = s.scalars(select(RegisteredWorkflowRow)).all()
        assert entry.ui_bundle is not None and entry.ui_bundle["digest"] == record.digest
        assert entry.manifest_hash == rows[1].sha256 != sha
        cert = s.scalars(
            select(CertificationRow).where(CertificationRow.content_sha256 == rows[1].sha256)
        ).one()
        assert cert.evidence["derived_from"] == sha
    assert runtime.requests[0].ui_bundle_digest == record.digest
    run_all(steps(h, FakeBifrost(TOOLS), runtime), commit_input(h, sha))  # a re-run adds nothing
    with h.sessions() as s:
        assert len(s.scalars(select(WorkflowManifestRow)).all()) == 2
    status = h.call("GET", commit_url(h), h.fde()).json()
    assert (
        status["state"] == "COMMITTED"
        and status["registration"]["ui_bundle"]["digest"] == record.digest
    )


def test_wg18_a_bundle_that_changed_in_storage_is_refused_with_409(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h, recorded=True)
    record = workflow_ui.newest(h.sessions(), TENANT_A, WORKFLOW)
    assert record is not None
    other = bundle_tar({**BUNDLE, "app.js": b"console.log(2)"})
    stored = storage(h)._path(record.tar_key)  # type: ignore[attr-defined]
    stored.chmod(0o644)  # a stored object is read-only
    stored.write_bytes(other)
    res = post(h, sha)
    problem(res, 409, "CONFLICT")
    assert "UI_BUNDLE_CHANGED" in res.json()["detail"]
    assert client.started == [] and trail(h, service.REQUESTED) == []
    with pytest.raises(ApplicationError) as caught:
        steps(h, FakeBifrost(TOOLS), FakeRuntime()).freeze(commit_input(h, sha))
    assert caught.value.type == REFUSED
    with h.sessions() as s:
        assert len(s.scalars(select(WorkflowManifestRow)).all()) == 1  # nothing was frozen


def test_wg18_a_missing_bundle_record_blocks_commit(h: Harness, client: FakeClient) -> None:
    seed(h)
    sha = certify(h, recorded=True)
    storage(h).delete(workflow_ui.newest(h.sessions(), TENANT_A, WORKFLOW).tar_key)  # type: ignore[union-attr]
    problem(post(h, sha), 409, "CONFLICT")


# --- the image of #867: Commit merges the recorded server entry and never rebuilds ----------------


def test_wg18_the_recorded_image_and_bundle_are_both_merged_and_the_digest_is_not_rebuilt(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h, imaged=True)
    assert h.call("GET", commit_url(h), h.fde()).json()["ready"] is True
    runtime = FakeRuntime()
    result = run_all(steps(h, FakeBifrost(TOOLS), runtime), commit_input(h, sha))
    assert result.image_digest == DIGEST and runtime.requests[0].image_digest == DIGEST
    with h.sessions() as s:
        rows = list(s.scalars(select(WorkflowManifestRow).order_by(WorkflowManifestRow.build_no)))
        frozen = WorkflowManifest.model_validate_json(json.dumps(rows[-1].manifest))
        assert frozen.mcp_server is not None and frozen.mcp_server.image_digest == DIGEST
        assert frozen.ui_bundle is not None and rows[-1].is_frozen is True
        [entry] = s.scalars(select(RegisteredWorkflowRow)).all()
        assert entry.image_digest == DIGEST


def test_wg18_a_failed_image_build_refuses_commit_with_its_reason(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    sha = certify(h, imaged=True)
    with h.sessions() as s:
        audit.record(
            s,
            tenant_id=TENANT_A,
            actor_id="SYSTEM",
            action=workflow_image.FAILED_ACTION,
            entity_type="workflow",
            entity_id=str(WORKFLOW),
            correlation_id="cid-867",
            after={"code": "BUILDER_DISABLED", "message": "no builder is configured"},
        )
        s.commit()
    res = post(h, sha)
    problem(res, 409, "CONFLICT")
    assert "BUILDER_DISABLED" in res.json()["detail"]
    assert client.started == []
    with h.sessions() as s:
        assert len(s.scalars(select(WorkflowManifestRow)).all()) == 1
