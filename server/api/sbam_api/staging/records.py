"""What the Staging runtime recorded about each deployment (#765, ADR 0036).

No table: a deployment is a run of audit rows on the `workflow` entity (the pattern of #868 and
#879).
`staging_deployment_started` holds the version, the digests, the container's name and internal
address, the egress allowlist and the NAMES of the secrets injected (never a value);
`staging_deployment_stopped` ends it (reason SUPERSEDED or STOPPED) and
`staging_deployment_failed` records a start that did not happen. Rows are only ever added, so a
superseded deployment stays on record. `replay` folds them, oldest first, into one entry per
version.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict
from sbam_contracts.staging import StagingDeployment, StagingState
from sqlalchemy.orm import Session

from sbam_api.audit import repository as audit

ENTITY: Final = "workflow"
STARTED: Final = "staging_deployment_started"
STOPPED: Final = "staging_deployment_stopped"
FAILED: Final = "staging_deployment_failed"
ACTIONS: Final = frozenset({STARTED, STOPPED, FAILED})
SUPERSEDED: Final = "SUPERSEDED"


class StartedRecord(BaseModel):
    """The `after` of `staging_deployment_started`."""

    model_config = ConfigDict(extra="forbid")

    version: str
    registry_name: str
    image_digest: str
    manifest_hash: str
    ui_bundle_digest: str | None = None
    container: str  # the container's name on the Staging host
    endpoint: str  # the workflow server's internal address (never in an API answer)
    egress_hosts: list[str] = []
    egress_enforced: bool = False
    secret_names: list[str] = []


@dataclass
class Deployment:
    version: str
    registry_name: str
    image_digest: str
    state: StagingState
    started: StartedRecord | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    superseded_by: str | None = None
    detail: str | None = None

    def model(self) -> StagingDeployment:
        started = self.started
        return StagingDeployment(
            version=self.version,
            registry_name=self.registry_name,
            state=self.state,
            image_digest=self.image_digest,
            ui_bundle_digest=None if started is None else started.ui_bundle_digest,
            egress_allowed_hosts=[] if started is None else started.egress_hosts,
            egress_enforced=False if started is None else started.egress_enforced,
            secret_names=[] if started is None else started.secret_names,
            started_at=self.started_at,
            stopped_at=self.stopped_at,
            superseded_by=self.superseded_by,
            detail=self.detail,
        )


def replay(session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID) -> dict[str, Deployment]:
    """One entry per version, from the audit rows oldest first (tenant-scoped)."""
    found: dict[str, Deployment] = {}
    for row in audit.entity_history(session, tenant_id, ENTITY, str(workflow_id)):
        if row.action not in ACTIONS or row.after is None:
            continue
        after = row.after
        version = str(after.get("version", ""))
        if not version:
            continue
        if row.action == STARTED:
            started = StartedRecord.model_validate(after)
            found[version] = Deployment(
                version=version,
                registry_name=started.registry_name,
                image_digest=started.image_digest,
                state=StagingState.RUNNING,
                started=started,
                started_at=row.at,
            )
        elif row.action == STOPPED and version in found:
            current = found[version]
            reason = str(after.get("reason", ""))
            current.state = (
                StagingState.SUPERSEDED if reason == SUPERSEDED else StagingState.STOPPED
            )
            current.stopped_at = row.at
            current.superseded_by = after.get("superseded_by")
        elif row.action == FAILED:
            previous = found.get(version)
            found[version] = Deployment(
                version=version,
                registry_name=str(after.get("registry_name", "")),
                image_digest=str(after.get("image_digest", "")),
                state=StagingState.FAILED,
                started=None if previous is None else previous.started,
                detail=str(after.get("detail", ""))[:300] or None,
            )
    return found


def running(deployments: dict[str, Deployment]) -> list[Deployment]:
    return [d for d in deployments.values() if d.state is StagingState.RUNNING]
