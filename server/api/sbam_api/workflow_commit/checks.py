"""The checks Commit rechecks (#766, ADR 0032 decision 2), read from what is stored, never cached.

Commit is an additional human gate on top of certification: it never certifies anything. It asks
whether the evidence that certification wrote is still true for the manifest the FDE is looking at:

- WORKFLOW_CERTIFIED  the Workflow is CERTIFIED or FROZEN and a WORKFLOW certificate sits on the
                      newest manifest's hash (the combined hash of the Workflow: the ETag);
- SUB_SKILLS_CERTIFIED every Sub-Skill is CERTIFIED or FROZEN and certified on the bundle of its
                      newest code attempt (a Sub-Skill rebuilt since is not);
- MCP_SERVER          the manifest carries the built server image (nothing is rebuilt at Commit);
- UI_BUNDLE           the newest UI_BUNDLE row is PASSED on the manifest's digest (ADR 0033);
- SECRETS             `connections.commit_blockers`: every required secret is set and tested.

The workflow-level certification and freeze stages are still stubs (#132), so a Workflow that was
never certified fails WORKFLOW_CERTIFIED with a plain reason; Commit does not paper over that.
"""

import json
import uuid
from dataclasses import dataclass

from sbam_contracts.build_dashboard import GateName
from sbam_contracts.connections import Blocker
from sbam_contracts.manifest import WorkflowManifest
from sbam_contracts.state_machines import SubSkillState, WorkflowState
from sbam_contracts.workflow_commit import CommitCheck, CommitCheckName
from sqlalchemy.orm import Session

from sbam_api.audit.repository import entity_history
from sbam_api.builds import workflow_ui
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.certifications.models import CertificationLevel
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.connections import service as connections
from sbam_api.core.conventions import not_found
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests import ui_bundle
from sbam_api.manifests.models import WorkflowManifestRow
from sbam_api.manifests.repository import GateResultRepository, WorkflowManifestRepository
from sbam_api.storage.base import StorageBackend
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository

FROZEN_ACTION = (
    "WORKFLOW_COMMIT_FROZEN"  # audit: the manifest hash it was approved on and the one frozen
)
BUNDLE_CHANGED = "UI_BUNDLE_CHANGED: the bundle in storage no longer matches the recorded digest"
CERTIFIED_STATES = frozenset({WorkflowState.CERTIFIED.value, WorkflowState.FROZEN.value})
SUB_OK = frozenset({SubSkillState.CERTIFIED.value, SubSkillState.FROZEN.value})


@dataclass(frozen=True)
class Evaluation:
    workflow: Workflow
    row: WorkflowManifestRow | None  # the newest manifest of the Workflow
    manifest: WorkflowManifest | None
    checks: list[CommitCheck]
    blockers: list[Blocker]
    effective: WorkflowManifest | None = (
        None  # `manifest` with the UI bundle entry merged in (#768)
    )
    lineage: frozenset[str] = frozenset()  # the hashes that count as "the one the FDE approved"
    ui_record: workflow_ui.WorkflowUiRecord | None = None

    def approved(self, manifest_hash: str) -> bool:
        return manifest_hash in self.lineage

    @property
    def ready(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def manifest_hash(self) -> str | None:
        return None if self.row is None else self.row.sha256

    def reasons(self) -> list[str]:
        return [f"{c.name.value}: {c.reason}" for c in self.checks if not c.passed and c.reason]


def _check(name: CommitCheckName, reason: str | None) -> CommitCheck:
    return CommitCheck(name=name, passed=reason is None, reason=reason)


def _certified(
    session: Session, ctx: TenantContext, workflow: Workflow, sha: str | None
) -> str | None:
    if workflow.state not in CERTIFIED_STATES:
        return f"the Workflow is {workflow.state}, not CERTIFIED"
    if sha is None:
        return "there is no Workflow manifest yet"
    found = CertificationRepository(session, ctx).find(
        CertificationLevel.WORKFLOW.value, workflow.id, sha
    )
    return None if found else "there is no WORKFLOW certificate on the current manifest hash"


def _sub_skills(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> str | None:
    subs = SubSkillRepository(session, ctx).of_workflow(workflow_id)
    if not subs:
        return "the Workflow has no Sub-Skills"
    attempts, certs = CodeAttemptRepository(session, ctx), CertificationRepository(session, ctx)
    stale: list[str] = []
    for sub in subs:
        newest = [a for a in attempts.of_sub_skill(workflow_id, sub.id) if a.bundle_sha256]
        bundle = newest[-1].bundle_sha256 if newest else None
        ok = sub.state in SUB_OK and bundle is not None
        if ok:
            found = certs.find(CertificationLevel.SUB_SKILL.value, sub.id, f"sha256:{bundle}")
            ok = found is not None
        if not ok:
            stale.append(sub.skill_id)
    if stale:
        return "not certified on the current code: " + ", ".join(sorted(stale)[:10])
    return None


def merged(
    manifest: WorkflowManifest | None, record: workflow_ui.WorkflowUiRecord | None
) -> WorkflowManifest | None:
    """The manifest as Commit freezes it. The build keeps the `ui_bundle` entry in the audit row
    `workflow_ui_built` (the draft has no `mcp_server` to hang it on), so it is merged in here."""
    if manifest is None or manifest.mcp_server is None or manifest.ui_bundle is not None:
        return manifest
    if record is None:
        return manifest
    return WorkflowManifest.sealed({**manifest.dump(), "ui_bundle": record.entry.dump()})


def evaluate(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, storage: StorageBackend
) -> Evaluation:
    """Every check, in order. A Workflow outside the tenant/project is a 404."""
    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    history = WorkflowManifestRepository(session, ctx).history(workflow_id)
    row = history[0] if history else None
    manifest = (
        None if row is None else WorkflowManifest.model_validate_json(json.dumps(row.manifest))
    )
    sha = None if row is None else row.sha256
    server = None if manifest is None else manifest.mcp_server
    record = workflow_ui.newest(session, ctx.tenant_id, workflow_id)
    effective = merged(manifest, record)
    lineage = {sha} if sha else set()
    for logged in entity_history(session, ctx.tenant_id, "workflow", str(workflow_id)):
        after = logged.after or {}
        if logged.action == FROZEN_ACTION and after.get("to_hash") == sha:
            lineage.add(str(after.get("from_hash")))

    bundle: str | None = None
    if effective is not None and effective.requires_ui_bundle:
        gates = GateResultRepository(session, ctx).workflow_rows(
            workflow_id, GateName.UI_BUNDLE.value
        )
        reasons = ui_bundle.certification_reasons(effective, gates)
        bundle = reasons[0] if reasons else None
        entry = effective.ui_bundle
        if bundle is None and record is not None and entry is not None:
            found = workflow_ui.packaged_digest(storage, record)
            if found is None or found.removeprefix("sha256:") != entry.digest.removeprefix(
                "sha256:"
            ):
                bundle = BUNDLE_CHANGED
    elif manifest is None:
        bundle = "there is no Workflow manifest yet"

    readiness = connections.commit_blockers(session, ctx, workflow_id)
    secrets = None
    if not readiness.ready:
        secrets = ", ".join(f"{b.name} {b.reason.value}" for b in readiness.blockers[:5])
    checks = [
        _check(CommitCheckName.WORKFLOW_CERTIFIED, _certified(session, ctx, workflow, sha)),
        _check(CommitCheckName.SUB_SKILLS_CERTIFIED, _sub_skills(session, ctx, workflow_id)),
        _check(
            CommitCheckName.MCP_SERVER,
            None if server is not None else "the manifest has no built MCP server image",
        ),
        _check(CommitCheckName.UI_BUNDLE, bundle),
        _check(CommitCheckName.SECRETS, secrets),
    ]
    return Evaluation(
        workflow,
        row,
        manifest,
        checks,
        list(readiness.blockers),
        effective,
        frozenset(lineage),
        record,
    )
