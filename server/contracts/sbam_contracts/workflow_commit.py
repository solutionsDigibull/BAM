"""Commit: the FDE's final approval, which moves a certified Workflow to Staging (#766, WG-18).

ADR 0032: Commit freezes the Workflow, registers a STAGING entry in the registry on the digest that
was built and certified (no rebuild), asks the Staging runtime to start that digest, and the
workbench then switches to Staging. Promotion to SRIM production is a separate, later step.

- `GET W/commit` -> `CommitStatus` with an `ETag`: the combined manifest hash the FDE looked at.
- `POST W/commit` with `If-Match` (that hash) and `Idempotency-Key` -> 202 `CommitAccepted`.
  A stale hash is 409; a failed check, a missing secret or an untested secret is 409 with the
  reason.

`StagingStartRequest` / `StagingStartResult` are the port to the Staging runtime (#765, not built
yet): Commit calls it and records what it answered. A request carries secret NAMES only; the
runtime asks `connections.inject.resolve_env` for the values when it starts the container.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.connections import Blocker
from sbam_contracts.manifest import VERSION_PATTERN, Digest
from sbam_contracts.runs import Run, RunInput
from sbam_contracts.workflow_registry import RegisteredWorkflow, RegistryName

WORKFLOW_COMMIT: Final = "WorkflowCommit"  # workflow type
MAX_REASONS: Final = 500


def workflow_commit_id(workflow_id: uuid.UUID) -> str:
    """One Commit at a time per Workflow (Temporal's conflict policy)."""
    return f"workflow-commit-{workflow_id}"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommitCheckName(StrEnum):
    WORKFLOW_CERTIFIED = "WORKFLOW_CERTIFIED"  # state CERTIFIED/FROZEN and a WORKFLOW certificate
    SUB_SKILLS_CERTIFIED = "SUB_SKILLS_CERTIFIED"  # every Sub-Skill certified on its current hash
    MCP_SERVER = "MCP_SERVER"  # the manifest carries the built server image
    UI_BUNDLE = "UI_BUNDLE"  # the bundle evidence is PASSED on the manifest's digest (ADR 0033)
    SECRETS = "SECRETS"  # every required secret is set and its connection test passed (ADR 0030)


class CommitCheck(_Strict):
    name: CommitCheckName
    passed: bool
    reason: Annotated[str, Field(max_length=300)] | None = None  # why it did not pass


class StagingStartStatus(StrEnum):
    STARTED = "STARTED"  # the runtime started the container on the certified digest
    PENDING_RUNTIME = "PENDING_RUNTIME"  # no Staging runtime yet (#765): the request is recorded
    FAILED = "FAILED"  # the runtime refused or could not start it; Commit can be run again


class StagingStartRequest(_Strict):
    """What the Staging runtime (#765) needs to start a committed Workflow. IDs, the digest that
    was certified, and secret names: never a secret value."""

    tenant_id: uuid.UUID
    project_id: uuid.UUID
    workflow_id: uuid.UUID
    version: Annotated[str, Field(pattern=VERSION_PATTERN)]
    registry_name: RegistryName
    image_digest: Digest
    manifest_hash: Digest
    ui_bundle_digest: Digest | None = None
    secret_names: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=200
    )
    requested_by: Annotated[str, Field(min_length=1, max_length=255)]


class StagingStartResult(_Strict):
    status: StagingStartStatus
    detail: Annotated[str, Field(max_length=300)] | None = None


class CommitState(StrEnum):
    NOT_COMMITTED = "NOT_COMMITTED"
    COMMITTING = "COMMITTING"  # a run is going, or stopped half way: Commit again completes it
    COMMITTED = "COMMITTED"  # frozen and registered in Staging


class CommitStatus(_Strict):
    """What the Validation screen shows: the checks, the secrets gate, and what Commit did."""

    workflow_id: uuid.UUID
    manifest_hash: Digest | None = None  # the combined hash (the ETag); null: nothing built yet
    ready: bool
    checks: list[CommitCheck] = Field(default_factory=list, max_length=MAX_REASONS)
    blockers: list[Blocker] = Field(default_factory=list, max_length=MAX_REASONS)
    state: CommitState
    registration: RegisteredWorkflow | None = None  # the STAGING entry once committed
    staging: StagingStartResult | None = None  # what the Staging runtime answered
    committed_by: Annotated[str, Field(max_length=255)] | None = None
    committed_at: datetime | None = None
    run: Run | None = None  # the newest Commit run


class CommitAccepted(_Strict):
    run: Run
    manifest_hash: Digest


class WorkflowCommitInput(RunInput):
    """`WorkflowCommit`'s input: ids and the hash the FDE approved (nothing else)."""

    workflow_id: uuid.UUID
    manifest_hash: Digest
    actor_id: Annotated[str, Field(min_length=1, max_length=255)]


class WorkflowCommitResult(_Strict):
    workflow_id: uuid.UUID
    version: Annotated[str, Field(pattern=VERSION_PATTERN)]
    registry_name: RegistryName
    image_digest: Digest
    manifest_hash: Digest
    staging: StagingStartResult


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/workflow_commit/`."""
    models: dict[str, type[BaseModel]] = {
        "status": CommitStatus,
        "accepted": CommitAccepted,
        "staging_start_request": StagingStartRequest,
        "staging_start_result": StagingStartResult,
    }
    return {
        stem: contract_schema(model, f"workflow_commit/{stem}") for stem, model in models.items()
    }
