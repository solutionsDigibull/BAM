"""A Workflow's repositories, for the workbench's repository panel (#843). Read only.

`GET /projects/{project_id}/workflows/{workflow_id}/repositories` lists, per repository the
Workflow's Sub-Skills start from (the Skill manifests' `build.repos`, WG-9): its pinned commit, the
patches applied on top with their reasons, the stored scan report of the fetcher (#755) found by URL
and pinned commit, and a flat component list (the repository and its dependencies, each with a
version). A repository with no stored report still appears, with `report` empty: the panel says so
instead of failing.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.repo_overlay import PatchRecord
from sbam_contracts.repo_scan import RepoScanReport


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SbomKind(StrEnum):
    REPOSITORY = "repository"
    DEPENDENCY = "dependency"


class SbomComponent(_Out):
    kind: SbomKind
    name: str
    version: str  # the pinned commit for a repository
    ecosystem: str | None = None  # PyPI or npm; none for the repository itself
    licence: str | None = None  # SPDX id when known


class WorkflowRepository(_Out):
    url: str
    commit: str
    licence: str | None = None  # the SPDX id the Skill manifest recorded
    repo_dir: str  # vendor/<repo>@<commit>
    sub_skills: list[str]  # the skill ids that start from it
    patches: list[PatchRecord]  # applied in this order; the reason of each is in the manifest
    report: RepoScanReport | None = None  # none: no report is stored for this URL and commit
    sbom: list[SbomComponent]


class WorkflowRepositories(_Out):
    workflow_id: str
    repositories: list[WorkflowRepository]  # empty: the Workflow uses no repository


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/workflow/`."""
    return {"repositories": contract_schema(WorkflowRepositories, "workflow/repositories")}
