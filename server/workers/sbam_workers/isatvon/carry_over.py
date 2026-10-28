"""`carry_over_workflow` (E7.2, C-15): a scope change keeps what it didn't touch.

The ProjectPipeline runs it right after `create_workflow`. On a first approval there is no stored
impact and it does nothing. After a scope-change approval it copies the previous Workflow
version's Macro and Sub-Skills into the new (next MAJOR) one, exactly as the impact stored at the
approval says (`sow_impacts`, the same report the FDE previewed):

* a Macro Skill keeps the `sow_refs` that still exist in the new SOW; one left with none (its
  module was removed) is dropped with its Sub-Skills;
* a Sub-Skill citing only removed features is dropped; one the impact lists is copied
  INVALIDATED (`carried_invalidated`) and then DRAFT (`re_extracted`), to be extracted again;
* every other Sub-Skill is reused: a CERTIFIED or FROZEN one is copied CERTIFIED (`reused`) with
  `reused_from_id` (its certification is the previous one's); one never built is copied as a draft
  and validated again;
* ids stay stable (`macro_category`, `skill_id`, order); content, findings and hashes are copied.
The previous Workflow version and its rows are only read, never changed. Everything is written in
one transaction; a retry finds the copies already there and returns them.
"""

import uuid
from typing import Final

from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.sow.models import SowVersion
from sbam_api.sow.repository import SowImpactRepository, SowVersionRepository
from sbam_api.state_machine import TransitionCommand, apply_create, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_contracts.isatvon14 import DEFAULT_LIBRARY_REGISTRY, validate_sub_skill
from sbam_contracts.pipeline import CarryOverInput, IdsResult
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import Actor, SubSkillState
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for

BUILT: Final = frozenset({SubSkillState.CERTIFIED.value, SubSkillState.FROZEN.value})


def _ctx(inp: CarryOverInput) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _sow_ids(version: SowVersion) -> set[str]:
    """Module and feature ids of the new SOW version."""
    doc = version.structured or {}
    return {m["id"] for m in doc.get("modules", [])} | {f["id"] for f in doc.get("features", [])}


class _Copier:
    def __init__(self, session: Session, inp: CarryOverInput) -> None:
        self.session, self.inp, self.ctx = session, inp, _ctx(inp)

    def _key(self, kind: str, source: uuid.UUID, step: str = "") -> str:
        return f"{self.inp.run_id}:carry_over:{kind}:{source}{step}"

    def _create(self, row: MacroSkill | SubSkill, event: str, key: str) -> None:
        apply_create(
            self.session,
            self.ctx,
            row,
            event=event,
            actor=Actor.SYSTEM,
            actor_id=SYSTEM_ACTOR_ID,
            correlation_id=self.inp.correlation_id,
            idempotency_key=key,
        )

    def _move(self, row: SubSkill, event: str, key: str) -> None:
        apply_transition(
            self.session,
            self.ctx,
            TransitionCommand(
                model=SubSkill,
                entity_id=row.id,
                event=event,
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=self.inp.correlation_id,
                idempotency_key=key,
            ),
        )

    def macro(self, source: MacroSkill, refs: list[str]) -> MacroSkill:
        row = MacroSkill(
            id=uuid.uuid4(),
            workflow_id=self.inp.workflow_id,
            sow_version_id=self.inp.sow_version_id,
            macro_category=source.macro_category,
            name=source.name,
            description=source.description,
            order=source.order,
            sow_refs=refs,
            agent_ref=source.agent_ref,
            validation_errors=list(source.validation_errors),
            created_by="SYSTEM",
        )
        self._create(row, "carried_over", self._key("macro", source.id))
        return row

    def sub_skill(
        self, source: SubSkill, macro: MacroSkill, refs: list[str], affected: bool
    ) -> SubSkill:
        reused = not affected and source.state in BUILT
        row = SubSkill(
            id=uuid.uuid4(),
            macro_skill_id=macro.id,
            workflow_id=self.inp.workflow_id,
            skill_id=source.skill_id,
            skill_name=source.skill_name,
            order=source.order,
            isatvon=dict(source.isatvon),
            isatvon_framework_version=source.isatvon_framework_version,
            sow_refs=refs,
            validation_errors=list(source.validation_errors),
            content_sha256=source.content_sha256,
            reused_from_id=source.id if reused else None,
            created_by="SYSTEM",
        )
        key = self._key("sub_skill", source.id)
        if affected:  # the scope change touches it: extracted again
            self._create(row, "carried_invalidated", key)
            self._move(row, "re_extracted", f"{key}:re_extracted")
        elif reused:  # certified and untouched: reused as it is
            self._create(row, "reused", key)
        else:  # never built: a draft, validated again
            self._create(row, "extracted", key)
            findings = validate_sub_skill(
                row.isatvon,
                macro_category=macro.macro_category,
                library_registry=DEFAULT_LIBRARY_REGISTRY,
            )
            row.validation_errors = [f.model_dump(mode="json") for f in findings]
            event = "validation_failed" if findings else "validation_passed"
            self._move(row, event, f"{key}:validated")
        return row


def carry_over(sessions: sessionmaker[Session], inp: CarryOverInput) -> list[uuid.UUID]:
    """The copied Sub-Skills' ids (empty on a first approval); see the module docstring."""
    ctx = _ctx(inp)
    with sessions() as session:
        impact = SowImpactRepository(session, ctx).of_version(inp.sow_version_id)
        if impact is None or impact.workflow_id is None:
            return []  # a first approval, or nothing was built from the previous version
        macros = MacroSkillRepository(session, ctx)
        sub_skills = SubSkillRepository(session, ctx)
        if macros.of_workflow(inp.workflow_id):  # a retry: already copied
            return [s.id for s in sub_skills.of_workflow(inp.workflow_id)]
        version = SowVersionRepository(session, ctx).get(inp.sow_version_id)
        if version is None:
            raise ApplicationError(
                "the approved SOW version is gone", type="SowVersionNotFound", non_retryable=True
            )
        still = _sow_ids(version)
        affected = set(impact.affected_sub_skill_ids)
        copier = _Copier(session, inp)
        copied: list[uuid.UUID] = []
        for source_macro in macros.of_workflow(impact.workflow_id):
            refs = [r for r in source_macro.sow_refs if r in still]
            if not refs:
                continue  # its module was removed: no Macro Skill for it any more
            macro = copier.macro(source_macro, refs)
            for source in sub_skills.of_macro(source_macro.id):
                kept = [r for r in source.sow_refs if r in still]
                if not kept:
                    continue  # cites only removed features
                row = copier.sub_skill(source, macro, kept, str(source.id) in affected)
                copied.append(row.id)
        session.commit()
        return copied


@activity_for(TaskQueue.LLM)  # where `create_workflow` runs
@activity.defn(name="carry_over_workflow")
def carry_over_workflow(inp: CarryOverInput) -> IdsResult:
    """Copy the previous Workflow version's unaffected and invalidated rows (E7.2)."""
    return IdsResult(ids=carry_over(mirror.sessions(), inp))
