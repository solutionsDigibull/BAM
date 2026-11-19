"""The Staging runtime's status (#765, WG-17, ADR 0032 and 0036).

After Commit (#766) the workflow's container runs in Staging until a newer version replaces it:
one deployment per project and version, no time limit. `GET W/staging` answers `StagingStatus`
for the workbench (#861); `GET W/staging/ui/{path}` serves the generated UI bundle and
`POST W/staging/mcp` reaches the workflow's MCP server, both only for the assigned FDE.

A status never carries a secret, a secret value or the container's internal address: it names the
digests that run, the hosts the egress allowlist lets through, and the state the runtime recorded.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.manifest import VERSION_PATTERN, Digest
from sbam_contracts.workflow_registry import RegistryName

STAGING_DEPLOYMENT_LIMIT: Final = 100  # the history keeps every version; this bounds one answer


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StagingState(StrEnum):
    NOT_DEPLOYED = "NOT_DEPLOYED"  # nothing was committed to Staging for this Workflow
    PENDING_RUNTIME = "PENDING_RUNTIME"  # committed, but no Staging runtime is configured
    RUNNING = "RUNNING"  # the container of the certified digest was started
    FAILED = "FAILED"  # the runtime refused or could not start it; Commit again retries
    STOPPED = "STOPPED"  # stopped, kept on record
    SUPERSEDED = "SUPERSEDED"  # a newer committed version replaced it; kept on record


class StagingHealth(StrEnum):
    HEALTHY = "HEALTHY"  # the workflow server answered
    UNREACHABLE = "UNREACHABLE"  # it is recorded as running but did not answer
    UNKNOWN = "UNKNOWN"  # not running, or not checked


class StagingDeployment(_Strict):
    """One Staging deployment as the runtime recorded it (kept for every version)."""

    version: Annotated[str, Field(pattern=VERSION_PATTERN)]
    registry_name: RegistryName
    state: StagingState
    image_digest: Digest
    ui_bundle_digest: Digest | None = None
    egress_allowed_hosts: list[Annotated[str, Field(max_length=253)]] = Field(
        default_factory=list, max_length=200
    )
    egress_enforced: bool = False  # false: the container has no outside access at all
    secret_names: list[Annotated[str, Field(max_length=128)]] = Field(
        default_factory=list, max_length=200
    )  # the names injected at start, never a value
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    superseded_by: Annotated[str, Field(pattern=VERSION_PATTERN)] | None = None
    detail: Annotated[str, Field(max_length=300)] | None = None


class StagingStatus(_Strict):
    """What the Staging screen reads: the current deployment, its health and the history."""

    workflow_id: uuid.UUID
    state: StagingState  # the current deployment's, or NOT_DEPLOYED
    health: StagingHealth
    current: StagingDeployment | None = None
    ui_url: str | None = None  # the FDE-only route that serves the bundle, when it can be served
    mcp_url: str | None = None  # the FDE-only route to the workflow's MCP server
    history: list[StagingDeployment] = Field(
        default_factory=list, max_length=STAGING_DEPLOYMENT_LIMIT
    )  # every version, newest first (superseded ones are never deleted)


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/staging/`."""
    return {"status": contract_schema(StagingStatus, "staging/status")}
