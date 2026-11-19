"""The repository a build was attached to (#862): the record `attach_repo` writes and the readers.

`attach_repo` fetches and scans the repository the build chose (#756), then writes one audit row
per (Sub-Skill, build) holding the outcome: the stored scan report (found by its sha256, URL and
pinned commit) and, when the tree was vendored, the patch overlay record of #757. `build_manifest`
reads the overlay into the Skill manifest's `build.repos`, which is what the workbench's repository
panel (#843), the licence and vulnerability gates (#761) and the certification evidence read.
A refused repository is recorded too (the report says why) and the build goes on generating code.
"""

import uuid
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict
from sbam_contracts.repo_overlay import RepoOverlay
from sqlalchemy.orm import Session

from sbam_api.audit import repository as audit

ENTITY: Final = "sub_skill"
ATTACHED_ACTION: Final = "repo_attached"


class AttachStatus(StrEnum):
    ATTACHED = "ATTACHED"  # vendored: the overlay record is in `overlay`
    REFUSED = "REFUSED"  # the scan refused it (licence, malware, host, ...): code is generated


class RepoAttached(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AttachStatus
    url: str
    commit: str | None = None
    report_sha256: str
    overlay: dict[str, Any] | None = None  # a `RepoOverlay`, when ATTACHED


def key_of(build_no: int) -> str:
    return f"repo_attached:{build_no}"


def find(
    session: Session, tenant_id: uuid.UUID, sub_skill_id: uuid.UUID, build_no: int
) -> RepoAttached | None:
    row = audit.find_by_key(session, tenant_id, ENTITY, str(sub_skill_id), key_of(build_no))
    if row is None or row.after is None:
        return None
    return RepoAttached.model_validate(row.after)


def overlays(
    session: Session, tenant_id: uuid.UUID, sub_skill_id: uuid.UUID, build_no: int
) -> tuple[RepoOverlay, ...]:
    """The vendored repositories this build attached (none when it attached nothing or the scan
    refused the repository)."""
    found = find(session, tenant_id, sub_skill_id, build_no)
    if found is None or found.status is not AttachStatus.ATTACHED or found.overlay is None:
        return ()
    return (RepoOverlay.model_validate(found.overlay),)
