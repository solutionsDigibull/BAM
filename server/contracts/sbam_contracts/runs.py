"""Temporal run contracts: task queues, workflow ids, run mirror, 202 run object (E1.7, TD §13.1).

The API starts workflows and answers long commands with `202 {"run": Run}`; the UI polls
`GET …/runs/{run_id}`, which reads the `temporal_runs` mirror, never Temporal itself.

Rules worth knowing
  * Workflow ids are deterministic per subject (`pipeline-<project_id>`,
    `ingest-<document_version_id>`, `build-<sub_skill_id>-<build_no>`), so a second start finds the
    running execution instead of starting another one.
  * Payloads carry IDs and small DTOs only (`RunInput` and subclasses), at most `MAX_PAYLOAD_BYTES`
    as JSON. Content stays in storage or the DB.
  * `RunState` mirrors Temporal's execution status; the terminal states never change again.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema

MAX_PAYLOAD_BYTES: Final = 64 * 1024
CORRELATION_ID_PATTERN: Final = r"^[A-Za-z0-9-]{1,64}$"  # same rule as the API middleware

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_QUEUE = r"sbam-(?:ingest|knowledge|llm|build|test|certify)"
WORKFLOW_ID_PATTERN: Final = (
    rf"^(?:pipeline-{_UUID}|ingest-{_UUID}|build-{_UUID}-[1-9][0-9]*|ping-{_QUEUE}-{_UUID}"
    # knowledge-: KnowledgeRebuild (E5.2), sow-: SowRegenerate (E6.3), sow-export-: SowExport (E6.4)
    rf"|knowledge-{_UUID}|sow-{_UUID}|sow-export-{_UUID}-(?:pdf|docx|md))$"
)
WORKFLOW_ID: Final = re.compile(WORKFLOW_ID_PATTERN)


class TaskQueue(StrEnum):
    """The six worker task queues (TD §13.1). A worker serves one or more of them (`--queues`)."""

    INGEST = "sbam-ingest"
    KNOWLEDGE = "sbam-knowledge"
    LLM = "sbam-llm"
    BUILD = "sbam-build"
    TEST = "sbam-test"
    CERTIFY = "sbam-certify"


class RunState(StrEnum):
    """Execution state as mirrored in `temporal_runs` (Temporal's workflow execution status)."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TERMINATED = "TERMINATED"
    TIMED_OUT = "TIMED_OUT"


TERMINAL_STATES: Final = frozenset(RunState) - {RunState.RUNNING}


class SubjectType(StrEnum):
    """What a run works on; `subject_id` is that entity's id."""

    PROJECT = "project"
    DOCUMENT_VERSION = "document_version"
    SUB_SKILL = "sub_skill"


def pipeline_workflow_id(project_id: UUID) -> str:
    return f"pipeline-{project_id}"


def ingest_workflow_id(document_version_id: UUID) -> str:
    return f"ingest-{document_version_id}"


def master_document_workflow_id(master_document_id: UUID) -> str:
    return f"master-doc-{master_document_id}"


def build_workflow_id(sub_skill_id: UUID, build_no: int) -> str:
    if build_no < 1:
        raise ValueError("build_no starts at 1")
    return f"build-{sub_skill_id}-{build_no}"


def ping_workflow_id(queue: TaskQueue, project_id: UUID) -> str:
    """The sample workflow's id: one ping per queue and project at a time."""
    return f"ping-{queue.value}-{project_id}"


class RunInput(BaseModel):
    """Base of every workflow/activity input: IDs only, never content (TD §13.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: Annotated[UUID, Field(strict=False)]
    project_id: Annotated[UUID, Field(strict=False)]
    run_id: Annotated[UUID, Field(strict=False)]  # `temporal_runs.id`, the id the API returned
    correlation_id: Annotated[str, Field(pattern=CORRELATION_ID_PATTERN)]


class Run(BaseModel):
    """One Temporal execution as the API returns it (mirror of `temporal_runs`)."""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[UUID, Field(strict=False)]
    temporal_workflow_id: Annotated[str, Field(pattern=WORKFLOW_ID_PATTERN)]
    temporal_run_id: Annotated[str, Field(min_length=1, max_length=64)]
    type: Annotated[str, Field(min_length=1, max_length=100, description="Workflow type name")]
    subject_type: Annotated[SubjectType, Field(strict=False)]
    subject_id: Annotated[UUID, Field(strict=False)]
    state: Annotated[RunState, Field(strict=False)]
    current_stage: Annotated[str | None, Field(max_length=100)] = None
    last_error: Annotated[str | None, Field(max_length=500)] = None
    started_at: Annotated[datetime, Field(strict=False)]
    updated_at: Annotated[datetime, Field(strict=False)]


class RunAccepted(BaseModel):
    """`202 Accepted` body of every long-running command."""

    model_config = ConfigDict(extra="forbid")

    run: Run


# --- Activity DTOs (worker-internal; not exported to the UI) ------------------------------------


class MirrorUpdate(BaseModel):
    """Input of the `update_run_mirror` activity. Keyed by the Temporal execution, not by our id,
    so a run whose mirror row was written late (or by the reconciler) is still found."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: Annotated[UUID, Field(strict=False)]
    project_id: Annotated[UUID, Field(strict=False)]
    temporal_workflow_id: Annotated[str, Field(pattern=WORKFLOW_ID_PATTERN)]
    temporal_run_id: Annotated[str, Field(min_length=1, max_length=64)]
    state: Annotated[RunState, Field(strict=False)]
    current_stage: Annotated[str | None, Field(max_length=100)] = None
    last_error: Annotated[str | None, Field(max_length=500)] = None


class PingResult(BaseModel):
    """Output of the sample `ping` activity: which queue (worker) answered, on which attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_queue: Annotated[TaskQueue, Field(strict=False)]
    attempt: Annotated[int, Field(ge=1)]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/runs/`."""
    return {
        "run": contract_schema(Run, "runs/run"),
        "run_accepted": contract_schema(RunAccepted, "runs/run_accepted"),
    }
