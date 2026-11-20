"""The real Macro Skill and Workflow certification stages (E14.2, #130).

Registered under the stubs' names and queue with the same Pydantic in/out (`WorkflowRef` ->
`IdsResult` / `IdResult`), so `ProjectPipeline` does not change. The rules are the pure validators
of `workflow_checks`; this module only loads what they judge and writes what they allow.

* `certify_macros`: per Macro Skill (Agent), its Sub-Skills with their state, their certificate
  and their current bundle (the newest code attempt with a bundle, the one Commit rechecks).
  `macro_verdicts` decides; a Macro with no finding gets a MACRO_SKILL certificate whose
  content hash covers its children's certificate hashes, and its Agent machine is advanced through
  the state-machine service (`build_started` from PENDING, then `all_sub_skills_certified`). A
  blocked Macro stays as it is: the Workflow validation names it next.
* `validate_workflow`: the newest Workflow manifest (its DAG and its stored schema.sql), the tables
  every Sub-Skill declares, each Sub-Skill's interface (`build_contracts` on its stored ISATVON)
  and the children, through `workflow_verdict`. A finding is the
  non-retryable `WorkflowValidationFailed` (the pipeline maps it to `workflow_validation_failed`);
  its message names `check:subject` only. The findings, with their messages, are written to the
  Workflow's audit trail (`WORKFLOW_VALIDATION_FAILED`): no notification store carries more than
  the error type (`pipeline.failed`), so the audit row is where the FDE reads them.
* `certify_workflow`: the same verdict again (refused unless clean), a MACRO_SKILL certificate on
  every Macro's current children and a manifest whose bundles are the current ones; then the
  WORKFLOW certificate on the newest manifest hash, which is what Commit and the freeze recheck
  (`workflow_commit.checks._certified`). Nothing is frozen here (#132), and `workflow_certified`
  is applied by the pipeline after this returns.

Every certificate goes through `CertificationRepository.record`, so a retried activity finds the
row it wrote. Messages and logs carry ids, names and hashes, never code or SQL text.

TODO(#130): the Workflow smoke run through the loop harness in CubeSandbox is not built yet; see
the issue.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ValidationError
from sbam_api.audit.outbox import dispatch_pending
from sbam_api.audit.repository import find_by_key, record
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.certifications.models import CertificationLevel, CertificationRow
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.manifests.repository import WorkflowManifestRepository
from sbam_api.notifications.sink import InAppSink
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition
from sbam_api.storage.base import StorageBackend
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.isatvon14 import build_contracts
from sbam_contracts.manifest import WorkflowManifest
from sbam_contracts.pipeline import IdResult, IdsResult, WorkflowRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import Actor, AgentState
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.manifest import IDS_MISMATCH
from sbam_workers.build.schema_sql.merge import declared
from sbam_workers.certify.workflow_checks import (
    SCHEMA_SUBJECT,
    Check,
    ChildSkill,
    Finding,
    Interface,
    macro_verdicts,
    workflow_verdict,
)
from sbam_workers.registry import activity_for

log = logging.getLogger("sbam_workers.certify.workflow")

VALIDATION_FAILED: Final = "WorkflowValidationFailed"
CERTIFICATION_REFUSED: Final = "WorkflowCertificationRefused"
VALIDATION_ACTION: Final = "WORKFLOW_VALIDATION_FAILED"  # audit: the findings of a failed run
CREATED_BY: Final = "worker-certify"
# Beyond `workflow_checks.Check`: what only the activity can see.
MANIFEST_MISSING: Final = "MANIFEST_MISSING"
MANIFEST_INVALID: Final = "MANIFEST_INVALID"
MANIFEST_STALE: Final = "MANIFEST_STALE"  # the manifest names a bundle that is no longer current
MACRO_NOT_CERTIFIED: Final = "MACRO_NOT_CERTIFIED"
WORKFLOW_SUBJECT: Final = "workflow"

# The Agent machine, from where the pipeline leaves it to CERTIFIED (the freeze does the rest).
_MACRO_STEPS: Final[dict[str, tuple[str, ...]]] = {
    AgentState.PENDING.value: ("build_started", "all_sub_skills_certified"),
    AgentState.BUILDING.value: ("all_sub_skills_certified",),
}


# --- what the stages load -------------------------------------------------------------------------


@dataclass(frozen=True)
class Child:
    sub_skill_id: uuid.UUID
    macro_skill_id: uuid.UUID
    skill: ChildSkill
    certificate_id: uuid.UUID | None  # the certificate `skill.certified_content_sha256` is from


@dataclass(frozen=True)
class Macro:
    id: uuid.UUID
    macro_category: str
    state: str


@dataclass(frozen=True)
class Tree:
    macros: tuple[Macro, ...]  # in `order`
    children: tuple[Child, ...]

    def of(self, macro: Macro) -> list[Child]:
        """The Macro's children in skill_id order (the manifest's order)."""
        mine = [c for c in self.children if c.macro_skill_id == macro.id]
        return sorted(mine, key=lambda c: c.skill.skill_id)


@dataclass(frozen=True)
class _EmptyDag:
    nodes: tuple[dict[str, object], ...] = ()
    edges: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class Facts:
    tree: Tree
    manifest_id: uuid.UUID | None = None
    manifest_sha256: str | None = None
    manifest: WorkflowManifest | None = None
    schema_sql: str | None = None  # None: missing, unreadable or not the manifest's hash
    declared: dict[str, set[str]] = field(default_factory=dict)  # skill_id -> tables
    extra: tuple[Finding, ...] = ()  # what only loading could see (no / an invalid manifest)
    interfaces: dict[str, Interface] = field(default_factory=dict)  # skill_id -> its interface


def _digest(value: str) -> str:
    return value.removeprefix("sha256:")


def load_tree(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> Tree:
    """Every Macro of the Workflow and every Sub-Skill under one of them, with the certificate on
    its current bundle (else its newest one, so the finding names both hashes)."""
    macros = MacroSkillRepository(session, ctx).of_workflow(workflow_id)
    category = {m.id: m.macro_category for m in macros}
    attempts, certs = CodeAttemptRepository(session, ctx), CertificationRepository(session, ctx)
    children = []
    for sub in SubSkillRepository(session, ctx).of_workflow(workflow_id):
        if sub.macro_skill_id not in category:
            continue  # not under one of this Workflow's Macros: the DAG coverage check names it
        bundled = [a for a in attempts.of_sub_skill(workflow_id, sub.id) if a.bundle_sha256]
        bundle = bundled[-1].bundle_sha256 if bundled else None
        cert: CertificationRow | None = None
        if bundle:
            cert = certs.find(CertificationLevel.SUB_SKILL.value, sub.id, f"sha256:{bundle}")
        if cert is None:
            cert = certs.latest(CertificationLevel.SUB_SKILL.value, sub.id)
        children.append(
            Child(
                sub_skill_id=sub.id,
                macro_skill_id=sub.macro_skill_id,
                skill=ChildSkill(
                    skill_id=sub.skill_id,
                    macro=category[sub.macro_skill_id],
                    state=sub.state,
                    certified_content_sha256=None if cert is None else cert.content_sha256,
                    bundle_sha256=bundle,
                ),
                certificate_id=None if cert is None else cert.id,
            )
        )
    return Tree(
        macros=tuple(Macro(m.id, m.macro_category, m.state) for m in macros),
        children=tuple(children),
    )


class SqlFacts:
    """Loads the Workflow-level facts; schema.sql is read through `StorageBackend`."""

    def __init__(self, storage: StorageBackend) -> None:
        self.storage = storage

    def __call__(self, session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> Facts:
        tree = load_tree(session, ctx, workflow_id)
        subs = SubSkillRepository(session, ctx).of_workflow(workflow_id)
        owners = declared(dict(s.isatvon) for s in subs)
        tables = {s.skill_id: owners.of(s.skill_id) for s in subs}
        history = WorkflowManifestRepository(session, ctx).history(workflow_id)
        if not history:
            missing = Finding(MANIFEST_MISSING, WORKFLOW_SUBJECT, "the Workflow has no manifest")
            return Facts(tree, declared=tables, extra=(missing,))
        row = history[0]
        try:
            manifest = WorkflowManifest.model_validate_json(json.dumps(row.manifest))
        except ValidationError:
            invalid = Finding(MANIFEST_INVALID, str(row.id), "the newest manifest is not valid")
            return Facts(tree, row.id, row.sha256, declared=tables, extra=(invalid,))
        return Facts(
            tree,
            row.id,
            row.sha256,
            manifest,
            self._schema(manifest),
            tables,
            interfaces={s.skill_id: interface_of(s.isatvon) for s in subs},
        )

    def _schema(self, manifest: WorkflowManifest) -> str | None:
        entry = manifest.schema_sql
        try:
            data = self.storage.get(entry.artifact_key)
        except Exception as exc:  # missing, unreadable: the finding says so, the log says why
            log.warning("schema.sql unreadable", extra={"exc_type": type(exc).__name__})
            return None
        if hashlib.sha256(data).hexdigest() != _digest(entry.sha256):
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None


def interface_of(isatvon: Mapping[str, Any]) -> Interface:
    """The Sub-Skill's contracts as its Skill Manifest has them; none when its stored ISATVON is
    not a valid ISATVON 1.4 Sub-Skill (a data edge to or from it is then a finding). Its required
    inputs and external_data names are read as `build.dag` reads the inputs, leniently."""
    definition = isatvon.get("isatvon_definition")
    source = definition.get("s_source") if isinstance(definition, Mapping) else None
    source = source if isinstance(source, Mapping) else {}
    inputs = source.get("required_inputs")
    external = source.get("external_data")
    names = (
        [e.get("name") for e in external if isinstance(e, Mapping)]
        if isinstance(external, list)
        else []
    )
    try:
        contracts: dict[str, Any] | None = build_contracts(SubSkillModel.model_validate(isatvon))
    except ValueError:  # pydantic's ValidationError too
        contracts = None
    variables = definition.get("v_variables") if isinstance(definition, Mapping) else None
    parameters = variables.get("parameters") if isinstance(variables, Mapping) else None
    typed = (
        [p.get("name") for p in parameters if isinstance(p, Mapping)]
        if isinstance(parameters, list)
        else []
    )
    return Interface(
        contracts,
        inputs=tuple(i for i in inputs if isinstance(i, str)) if isinstance(inputs, list) else (),
        external=frozenset(n for n in names if isinstance(n, str)),
        typed=frozenset(n for n in typed if isinstance(n, str)),
    )


FactsLoader = Callable[[Session, TenantContext, uuid.UUID], Facts]


# --- the decisions (pure) -------------------------------------------------------------------------


def findings_of(facts: Facts) -> tuple[Finding, ...]:
    """`workflow_verdict` over the loaded facts, plus what only loading could see."""
    extra = list(facts.extra)
    if facts.manifest is not None and facts.schema_sql is None:
        extra.append(
            Finding(
                Check.SCHEMA_INVALID,
                SCHEMA_SUBJECT,
                "the stored schema.sql is missing or does not match the manifest's hash",
            )
        )
    dag = _EmptyDag() if facts.manifest is None else facts.manifest.dag
    verdict = workflow_verdict(
        dag,
        facts.schema_sql or "",
        facts.declared,
        [c.skill for c in facts.tree.children],
        interfaces=facts.interfaces,
    )
    return tuple(sorted(set(verdict) | set(extra)))


def subjects(findings: Sequence[Finding]) -> str:
    return ", ".join(f"{f.check}:{f.subject}" for f in findings)


def validation_failed(findings: Sequence[Finding]) -> ApplicationError:
    return ApplicationError(
        f"workflow validation failed: {subjects(findings)}",
        type=VALIDATION_FAILED,
        non_retryable=True,
    )


def refused(findings: Sequence[Finding]) -> ApplicationError:
    return ApplicationError(
        f"workflow certification refused: {subjects(findings)}",
        type=CERTIFICATION_REFUSED,
        non_retryable=True,
    )


def macro_content_sha256(children: Sequence[Child]) -> str:
    """The MACRO_SKILL certificate's content hash: RFC 8785 + SHA-256 over each child's skill_id
    and certificate hash, in skill_id order. A child re-certified on a new bundle changes it."""
    return sha256_of(
        [
            {"skill_id": c.skill.skill_id, "content_sha256": c.skill.certified_content_sha256}
            for c in sorted(children, key=lambda c: c.skill.skill_id)
        ]
    )


def _stale_skills(manifest: WorkflowManifest, tree: Tree) -> list[Finding]:
    """Every skill the manifest names must be on the bundle the Sub-Skill has now."""
    current = {c.skill.skill_id: c.skill.bundle_sha256 for c in tree.children}
    return [
        Finding(MANIFEST_STALE, s.skill_id, "the manifest names another bundle")
        for agent in manifest.macro_skills
        for s in agent.skills
        if s.bundle_sha256 is None
        or current.get(s.skill_id) is None
        or _digest(s.bundle_sha256) != _digest(current[s.skill_id] or "")
    ]


def _certificate_findings(
    session: Session, ctx: TenantContext, tree: Tree, manifest: WorkflowManifest
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """Each Macro's certificate on its current children (MACRO_NOT_CERTIFIED when it has none, a
    Macro with no Sub-Skill included) and the manifest's bundles (MANIFEST_STALE). Returns the
    certificates found, for the Workflow evidence, and the findings."""
    certs = CertificationRepository(session, ctx)
    macros: list[dict[str, Any]] = []
    findings: list[Finding] = []
    for macro in tree.macros:
        children = tree.of(macro)
        found = None
        if children:
            found = certs.find(
                CertificationLevel.MACRO_SKILL.value, macro.id, macro_content_sha256(children)
            )
        if found is None:
            findings.append(Finding(MACRO_NOT_CERTIFIED, macro.macro_category, "no certificate"))
            continue
        macros.append(
            {
                "macro_category": macro.macro_category,
                "macro_skill_id": str(macro.id),
                "certification_id": str(found.id),
                "content_sha256": found.content_sha256,
            }
        )
    return macros, findings + _stale_skills(manifest, tree)


# --- the stages -----------------------------------------------------------------------------------


class WorkflowCertification:
    """`certify_macros`, `validate_workflow` and `certify_workflow` with their dependencies
    injected (see the module docstring)."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        facts: FactsLoader,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessions
        self.facts = facts
        self.clock = clock

    @staticmethod
    def _ctx(inp: WorkflowRef) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    @staticmethod
    def _workflow(session: Session, ctx: TenantContext, inp: WorkflowRef) -> None:
        if WorkflowRepository(session, ctx).get(inp.workflow_id) is None:
            raise ApplicationError(
                f"Workflow {inp.workflow_id} is not in this run",
                type=IDS_MISMATCH,
                non_retryable=True,
            )

    def _dispatch(self) -> None:
        """After commit only; a failed dispatch leaves the notification pending."""
        try:
            dispatch_pending(self.sessions, InAppSink(self.sessions))
        except Exception as exc:
            log.warning("notification dispatch failed", extra={"exc_type": type(exc).__name__})

    # --- certify_macros ---------------------------------------------------------------------------

    def certify_macros(self, inp: WorkflowRef) -> list[uuid.UUID]:
        ctx = self._ctx(inp)
        with self.sessions() as session:
            self._workflow(session, ctx, inp)
            tree = load_tree(session, ctx, inp.workflow_id)
            verdicts = macro_verdicts(c.skill for c in tree.children)
            certified: list[uuid.UUID] = []
            for macro in tree.macros:
                children = tree.of(macro)
                findings = verdicts.get(macro.macro_category)
                if not children or findings is None or findings:
                    log.info(
                        "macro blocked",
                        extra={"macro_skill_id": str(macro.id), "findings": len(findings or ())},
                    )
                    continue
                row = self._macro_certificate(session, ctx, macro, children)
                self._advance(session, ctx, inp, macro)
                certified.append(row.id)
            session.commit()
        self._dispatch()
        return certified

    def _macro_certificate(
        self, session: Session, ctx: TenantContext, macro: Macro, children: list[Child]
    ) -> CertificationRow:
        return CertificationRepository(session, ctx).record(
            {
                "level": CertificationLevel.MACRO_SKILL.value,
                "subject_id": macro.id,
                "content_sha256": macro_content_sha256(children),
                "evidence": {
                    "macro_category": macro.macro_category,
                    "children": [
                        {
                            "skill_id": c.skill.skill_id,
                            "sub_skill_id": str(c.sub_skill_id),
                            "certification_id": str(c.certificate_id),
                            "content_sha256": c.skill.certified_content_sha256,
                        }
                        for c in children
                    ],
                },
                "certified_at": self.clock(),
                "created_by": CREATED_BY,
            }
        )

    @staticmethod
    def _advance(session: Session, ctx: TenantContext, inp: WorkflowRef, macro: Macro) -> None:
        """The Agent machine up to CERTIFIED, audited; CERTIFIED or FROZEN already: nothing."""
        for event in _MACRO_STEPS.get(macro.state, ()):
            apply_transition(
                session,
                ctx,
                TransitionCommand(
                    model=MacroSkill,
                    entity_id=macro.id,
                    event=event,
                    actor=Actor.SYSTEM,
                    actor_id=SYSTEM_ACTOR_ID,
                    correlation_id=inp.correlation_id,
                    idempotency_key=f"{inp.run_id}:certify_macros:{macro.id}:{event}",
                ),
            )

    # --- validate_workflow ------------------------------------------------------------------------

    def validate_workflow(self, inp: WorkflowRef) -> uuid.UUID:
        ctx = self._ctx(inp)
        with self.sessions() as session:
            self._workflow(session, ctx, inp)
            facts = self.facts(session, ctx, inp.workflow_id)
            findings = findings_of(facts)
            if not findings and facts.manifest is not None:
                # What certify_workflow would still refuse (a Macro without a certificate, e.g.
                # one with no Sub-Skill; a manifest on an old bundle) is found here, so it fails
                # through `workflow_validation_failed` and its retry instead of stranding the
                # Workflow in CERTIFYING.
                _, more = _certificate_findings(session, ctx, facts.tree, facts.manifest)
                findings = tuple(sorted(set(more)))
            if findings:
                self._record_findings(session, inp, facts, findings)
                session.commit()
                raise validation_failed(findings)
            if facts.manifest_id is None:  # findings_of names a missing manifest
                raise validation_failed((Finding(MANIFEST_MISSING, WORKFLOW_SUBJECT, ""),))
            return facts.manifest_id

    @staticmethod
    def _record_findings(
        session: Session, inp: WorkflowRef, facts: Facts, findings: Sequence[Finding]
    ) -> None:
        """Once per run and manifest hash: a retried activity writes no second row."""
        key = f"{inp.run_id}:validate_workflow:{facts.manifest_sha256 or 'none'}"
        entity = str(inp.workflow_id)
        if find_by_key(session, inp.tenant_id, "workflow", entity, key) is not None:
            return
        record(
            session,
            tenant_id=inp.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action=VALIDATION_ACTION,
            entity_type="workflow",
            entity_id=entity,
            correlation_id=inp.correlation_id,
            after={
                "manifest_sha256": facts.manifest_sha256,
                "findings": [
                    {"check": f.check, "subject": f.subject, "message": f.message} for f in findings
                ],
            },
            idempotency_key=key,
        )

    # --- certify_workflow -------------------------------------------------------------------------

    def certify_workflow(self, inp: WorkflowRef) -> uuid.UUID:
        ctx = self._ctx(inp)
        with self.sessions() as session:
            self._workflow(session, ctx, inp)
            facts = self.facts(session, ctx, inp.workflow_id)
            findings = list(findings_of(facts))
            manifest, sha = facts.manifest, facts.manifest_sha256
            if findings or manifest is None or sha is None or facts.manifest_id is None:
                raise refused(findings or [Finding(MANIFEST_MISSING, WORKFLOW_SUBJECT, "")])
            macros, more = _certificate_findings(session, ctx, facts.tree, manifest)
            findings += more
            if findings:
                raise refused(sorted(set(findings)))
            row = CertificationRepository(session, ctx).record(
                {
                    "level": CertificationLevel.WORKFLOW.value,
                    "subject_id": inp.workflow_id,
                    "content_sha256": sha,
                    "evidence": self._workflow_evidence(facts, manifest, macros),
                    "certified_at": self.clock(),
                    "created_by": CREATED_BY,
                }
            )
            session.commit()
            return row.id

    @staticmethod
    def _workflow_evidence(
        facts: Facts, manifest: WorkflowManifest, macros: list[dict[str, Any]]
    ) -> dict[str, Any]:
        certificate = {c.skill.skill_id: c.certificate_id for c in facts.tree.children}
        return {
            "manifest_id": str(facts.manifest_id),
            "manifest_sha256": facts.manifest_sha256,
            "macros": macros,
            "skills": [
                {
                    "skill_id": s.skill_id,
                    "deterministic_hash": s.deterministic_hash,
                    "bundle_sha256": s.bundle_sha256,
                    "certification_id": str(certificate.get(s.skill_id)),
                }
                for agent in manifest.macro_skills
                for s in agent.skills
            ],
            "schema_sql_sha256": manifest.schema_sql.sha256,
        }

    # --- the activity bodies --------------------------------------------------------------------

    @activity.defn(name="certify_macros")
    async def run_certify_macros(self, inp: WorkflowRef) -> IdsResult:
        return IdsResult(ids=await asyncio.to_thread(self.certify_macros, inp))

    @activity.defn(name="validate_workflow")
    async def run_validate_workflow(self, inp: WorkflowRef) -> IdResult:
        return IdResult(id=await asyncio.to_thread(self.validate_workflow, inp))

    @activity.defn(name="certify_workflow")
    async def run_certify_workflow(self, inp: WorkflowRef) -> IdResult:
        return IdResult(id=await asyncio.to_thread(self.certify_workflow, inp))


def _stage() -> WorkflowCertification:
    from sbam_api.storage.factory import make_backend

    from sbam_workers.settings import WorkerSettings

    storage = make_backend(WorkerSettings())  # type: ignore[call-arg]  # values from the env
    return WorkflowCertification(mirror.sessions(), SqlFacts(storage))


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="certify_macros")
async def certify_macros(inp: WorkflowRef) -> IdsResult:
    return await _stage().run_certify_macros(inp)


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="validate_workflow")
async def validate_workflow(inp: WorkflowRef) -> IdResult:
    return await _stage().run_validate_workflow(inp)


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="certify_workflow")
async def certify_workflow(inp: WorkflowRef) -> IdResult:
    return await _stage().run_certify_workflow(inp)
