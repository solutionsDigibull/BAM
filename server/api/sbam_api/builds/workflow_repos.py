"""A Workflow's repositories for the workbench panel (#843). Read only.

The repositories are the ones the Sub-Skills' newest Skill manifests record in `build.repos` (WG-9);
the scan report of each (#755) is looked up among the project's stored reports (kind `repos`) by
URL and pinned commit, the newest one winning, as the certification stage does. A repository with no
stored report is still listed. Nothing here writes, and nothing opens a path.
"""

import uuid
from typing import Any, Final

from pydantic import ValidationError
from sbam_contracts.repo_overlay import RepoOverlay
from sbam_contracts.repo_scan import RepoScanReport
from sbam_contracts.workflow_repos import (
    SbomComponent,
    SbomKind,
    WorkflowRepositories,
    WorkflowRepository,
)
from sqlalchemy.orm import Session

from sbam_api.builds.workflow_code import _check_workflow, overlays_of
from sbam_api.core.auth import CurrentUser
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.repository import SubSkillManifestRepository
from sbam_api.storage.base import Kind, StorageBackend, StorageScope

MAX_REPORT_BYTES: Final = 4 * 1024 * 1024  # a stored tree is a tar, far larger: not a report
MAX_REPORTS_READ: Final = 500

RepoKey = tuple[str, str]  # (url, pinned commit)


def stored_reports(storage: StorageBackend, scope: StorageScope) -> dict[RepoKey, RepoScanReport]:
    """The newest stored `RepoScanReport` per (URL, commit) in the project's scope."""
    if not hasattr(storage, "list_keys"):
        return {}
    prefix = scope.key(Kind.REPOS, "").rstrip("/") + "/"
    newest: dict[RepoKey, tuple[Any, RepoScanReport]] = {}
    for n, item in enumerate(storage.list_keys(prefix)):
        if n >= MAX_REPORTS_READ:
            break
        if item.size > MAX_REPORT_BYTES:
            continue
        try:
            report = RepoScanReport.model_validate_json(storage.get(item.storage_key))
        except (ValidationError, ValueError):
            continue  # a vendored tree (a tar) or something else
        if report.commit is None:
            continue
        key = (str(report.url), str(report.commit))
        if key not in newest or item.modified_at > newest[key][0]:
            newest[key] = (item.modified_at, report)
    return {key: found[1] for key, found in newest.items()}


def _name(url: str) -> str:
    """`https://github.com/acme/lib` -> `acme/lib`."""
    return "/".join(url.rstrip("/").split("/")[-2:])


def components(overlay: RepoOverlay, report: RepoScanReport | None) -> list[SbomComponent]:
    """The repository itself, then its dependencies from the scan report, in a stable order."""
    out = [
        SbomComponent(
            kind=SbomKind.REPOSITORY,
            name=_name(str(overlay.url)),
            version=str(overlay.commit),
            licence=overlay.licence,
        )
    ]
    if report is not None:
        deps = sorted(report.dependency_licences, key=lambda d: (d.ecosystem, d.name, d.version))
        out += [
            SbomComponent(
                kind=SbomKind.DEPENDENCY,
                name=d.name,
                version=d.version,
                ecosystem=d.ecosystem,
                licence=d.spdx,
            )
            for d in deps
        ]
    return out


def repositories(
    session: Session,
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
) -> WorkflowRepositories:
    ctx = _check_workflow(session, user, project_id, workflow_id)  # another tenant: 404
    subs = SubSkillRepository(session, ctx).of_workflow(workflow_id)
    manifests = SubSkillManifestRepository(session, ctx).newest([s.id for s in subs])
    found: dict[RepoKey, tuple[RepoOverlay, set[str]]] = {}
    for sub in subs:
        manifest = manifests.get(sub.id)
        for overlay in overlays_of(manifest.manifest if manifest is not None else None):
            key = (str(overlay.url), str(overlay.commit))
            have = found.setdefault(key, (overlay, set()))
            have[1].add(sub.skill_id)
    reports = stored_reports(storage, StorageScope(ctx.tenant_id, project_id)) if found else {}
    items = []
    for key in sorted(found):
        overlay, skills = found[key]
        report = reports.get(key)
        items.append(
            WorkflowRepository(
                url=key[0],
                commit=key[1],
                licence=overlay.licence,
                repo_dir=overlay.repo_dir,
                sub_skills=sorted(skills),
                patches=list(overlay.patches),
                report=report,
                sbom=components(overlay, report),
            )
        )
    return WorkflowRepositories(workflow_id=str(workflow_id), repositories=items)
