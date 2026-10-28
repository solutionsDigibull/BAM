"""Scope-change impact (E7.2, C-15): what approving a new SOW version invalidates.

One function (`compute`) for both the preview (`GET P/sow/versions/{v}/impact`) and the approval,
which stores its result (`sow_impacts`), so what the FDE saw is what happens. From the approved
version (`base`) to the draft:

* `changed_sections`: the sections that differ (the same diff as `GET …/diff`);
* a Sub-Skill is affected when its `sow_refs` name a feature that was modified or removed; a
  Macro Skill when its `sow_refs` name a changed module or feature, or one of its Sub-Skills is
  affected. A removed module or feature always invalidates what cites it, never silently kept;
* every other Sub-Skill of the base version's Workflow is reused (`reused_sub_skills_count`);
* `added_modules`: new modules, which will get new Macro Skills.
Sections outside modules and features (e.g. assumptions) are reported but invalidate nothing.
"""

import uuid
from dataclasses import dataclass

from sbam_contracts.api import ErrorCode
from sbam_contracts.sow_api import (
    SectionChange,
    SowImpact,
    SowImpactMacroSkill,
    SowImpactSubSkill,
)
from sbam_contracts.state_machines import SowVersionState
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.sow.models import SowImpactRecord, SowVersion
from sbam_api.sow.repository import SowImpactRepository, SowRepository, SowVersionRepository
from sbam_api.sow.versions import section_diffs

_ITEMS = ("modules", "features")


@dataclass(frozen=True)
class Impact:
    report: SowImpact
    workflow_id: uuid.UUID | None
    affected_sub_skill_ids: list[uuid.UUID]


def _reason(refs: list[str], changed: dict[str, SectionChange]) -> str:
    return ", ".join(f"{ref} {changed[ref].value}" for ref in refs if ref in changed)


def compute(session: Session, ctx: TenantContext, base: SowVersion, draft: SowVersion) -> Impact:
    """The impact of replacing approved `base` by `draft`, on the Workflow made from `base`."""
    # Here, not at the top: the Workflow and ISATVON models import the SOW package (whose router
    # imports this module).
    from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
    from sbam_api.workflows.repository import WorkflowRepository

    sections = section_diffs(base.structured or {}, draft.structured or {})
    changed_sections = [s for s in sections if s.change is not SectionChange.UNCHANGED]
    changed: dict[str, SectionChange] = {
        item.id: item.change
        for s in changed_sections
        if s.section_id in _ITEMS
        for item in s.items
        if item.change is not SectionChange.ADDED
    }
    added = [
        item.id
        for s in changed_sections
        if s.section_id == "modules"
        for item in s.items
        if item.change is SectionChange.ADDED
    ]
    workflow = WorkflowRepository(session, ctx).by_sow_version(base.id)
    macros, sub_skills = [], []
    if workflow is not None and workflow.deleted_at is None:
        macros = MacroSkillRepository(session, ctx).of_workflow(workflow.id)
        sub_skills = SubSkillRepository(session, ctx).of_workflow(workflow.id)
    hit_sub_skills = [s for s in sub_skills if _reason(list(s.sow_refs), changed)]
    hit_macro_ids = {s.macro_skill_id for s in hit_sub_skills}
    affected_macros = []
    for macro in macros:
        reason = _reason(list(macro.sow_refs), changed)
        if not reason and macro.id in hit_macro_ids:
            reason = "Sub-Skills affected"
        if reason:
            affected_macros.append(
                SowImpactMacroSkill(macro_category=macro.macro_category, reason=reason)
            )
    report = SowImpact(
        version=draft.version,
        base_version=base.version,
        changed_sections=changed_sections,
        affected_macro_skills=affected_macros,
        affected_sub_skills=[
            SowImpactSubSkill(skill_id=s.skill_id, reason=_reason(list(s.sow_refs), changed))
            for s in hit_sub_skills
        ],
        added_modules=added,
        reused_sub_skills_count=len(sub_skills) - len(hit_sub_skills),
    )
    return Impact(report, workflow.id if workflow else None, [s.id for s in hit_sub_skills])


def _derives_from(versions: SowVersionRepository, draft: SowVersion, base_id: uuid.UUID) -> bool:
    """Whether `draft`'s parent chain reaches the approved version."""
    seen: set[uuid.UUID] = set()
    parent = draft.parent_version_id
    while parent is not None and parent not in seen:
        if parent == base_id:
            return True
        seen.add(parent)
        row = versions.get(parent)
        parent = row.parent_version_id if row is not None else None
    return False


def not_a_draft(detail: str) -> ApiError:
    return ApiError(409, ErrorCode.NOT_IN_REVIEW, "Not a scope-change draft", detail)


def preview(session: Session, user: CurrentUser, project_id: uuid.UUID, number: int) -> SowImpact:
    """`GET P/sow/versions/{v}/impact`: `v` is a draft derived from the approved version."""
    ctx = user.context(project_id)
    sow = SowRepository(session, ctx).of_project()
    if sow is None:
        raise not_found()
    versions = SowVersionRepository(session, ctx)
    draft = versions.by_number(sow.id, number)
    if draft is None:
        raise not_found()
    base = versions.get(sow.approved_version_id) if sow.approved_version_id else None
    if base is None:
        raise ApiError(
            409, ErrorCode.NO_APPROVED_SOW, "No approved SOW", "Nothing is approved to compare to."
        )
    if draft.state not in (SowVersionState.GENERATED.value, SowVersionState.IN_REVIEW.value):
        raise not_a_draft(f"v{draft.version} is {draft.state}, not a draft under review.")
    if not _derives_from(versions, draft, base.id):
        raise not_a_draft(f"v{draft.version} was not derived from the approved v{base.version}.")
    return compute(session, ctx, base, draft).report


def record_impact(
    session: Session, ctx: TenantContext, base: SowVersion, draft: SowVersion
) -> Impact:
    """At approval, in its transaction: the same computation, stored (`sow_impacts`)."""
    impact = compute(session, ctx, base, draft)
    row = SowImpactRecord(
        sow_version_id=draft.id,
        base_version_id=base.id,
        workflow_id=impact.workflow_id,
        report=impact.report.model_dump(mode="json"),
        affected_sub_skill_ids=[str(i) for i in impact.affected_sub_skill_ids],
    )
    SowImpactRepository(session, ctx).add(row)  # stamps tenant and project
    return impact
