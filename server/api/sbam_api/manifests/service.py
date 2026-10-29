"""Reading manifests back for the viewer (E9.6). Read-only and tenant- and project-scoped: a
Workflow or Sub-Skill of another project (or tenant) is 404."""

import uuid
from typing import Any

from sbam_contracts.manifest_api import ManifestVersion, ManifestVersionList, ManifestView
from sqlalchemy.orm import Session

from sbam_api.core.conventions import not_found
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.models import SubSkillManifest, WorkflowManifestRow
from sbam_api.manifests.repository import SubSkillManifestRepository, WorkflowManifestRepository
from sbam_api.workflows.repository import WorkflowRepository


def _sub_skill(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_skill_id: uuid.UUID
) -> SubSkill:
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    sub: SubSkill | None = SubSkillRepository(session, ctx).get(sub_skill_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    return sub


def _skill_version(manifest: dict[str, Any]) -> str:
    v = manifest.get("governance", {}).get("versioning", {})
    return f"{v.get('major', 1)}.{v.get('minor', 0)}.{v.get('patch', 0)}"


def _skill_entry(row: SubSkillManifest) -> ManifestVersion:
    return ManifestVersion(
        version=_skill_version(row.manifest),
        sha256=row.sha256,
        build_no=row.build_no,
        cycle=row.cycle,
        attempt=row.attempt,
        is_frozen=row.is_frozen,
        created_at=row.created_at,
    )


def _distinct(rows: list[SubSkillManifest]) -> list[SubSkillManifest]:
    """Newest first, one row per hash: a retried attempt that rebuilt the same manifest is one
    version, not two."""
    seen: set[str] = set()
    out = []
    for row in rows:
        if row.sha256 not in seen:
            seen.add(row.sha256)
            out.append(row)
    return out


def sub_skill_versions(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_skill_id: uuid.UUID
) -> ManifestVersionList:
    _sub_skill(session, ctx, workflow_id, sub_skill_id)
    rows = _distinct(SubSkillManifestRepository(session, ctx).history(sub_skill_id))
    return ManifestVersionList(items=[_skill_entry(r) for r in rows])


def sub_skill_manifest(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID,
    version: str | None,
    build_no: int | None,
) -> ManifestView:
    sub = _sub_skill(session, ctx, workflow_id, sub_skill_id)
    rows = _distinct(SubSkillManifestRepository(session, ctx).history(sub_skill_id))
    index = next(
        (
            i
            for i, r in enumerate(rows)
            if (version is None or _skill_version(r.manifest) == version)
            and (build_no is None or r.build_no == build_no)
        ),
        None,
    )
    if index is None:
        raise not_found()
    row = rows[index]
    older = rows[index + 1] if index + 1 < len(rows) else None
    return ManifestView(
        **_skill_entry(row).model_dump(),
        schema_version=row.schema_version,
        reused=sub.reused_from_id is not None or (older is not None and older.sha256 == row.sha256),
        manifest=row.manifest,
    )


def _workflow_entry(row: WorkflowManifestRow) -> ManifestVersion:
    return ManifestVersion(
        version=str(row.manifest.get("version", "")),
        sha256=row.sha256,
        build_no=row.build_no,
        is_frozen=row.is_frozen,
        created_at=row.created_at,
    )


def _workflow_rows(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID
) -> list[WorkflowManifestRow]:
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    return WorkflowManifestRepository(session, ctx).history(workflow_id)


def workflow_versions(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID
) -> ManifestVersionList:
    rows = _workflow_rows(session, ctx, workflow_id)
    return ManifestVersionList(items=[_workflow_entry(r) for r in rows])


def workflow_manifest(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, build_no: int | None
) -> ManifestView:
    rows = _workflow_rows(session, ctx, workflow_id)
    row = next((r for r in rows if build_no is None or r.build_no == build_no), None)
    if row is None:
        raise not_found()
    return ManifestView(
        **_workflow_entry(row).model_dump(),
        schema_version=row.schema_version,
        manifest=row.manifest,
    )
