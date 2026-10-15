"""Retry a Sub-Skill build (E10.2, TD §13.2): resolve the SubSkillBuild child and signal it.

The build is the child workflow `build-<sub_skill_id>-<build_no>` with the highest build_no. Its
`build_status` query says whose it is (tenant, project, Workflow, Sub-Skill) and whether it waits
for the FDE. Until the Sub-Skill tables exist (#91), that query is also how a foreign or unknown id
is told apart: anything that doesn't match the URL is a 404, as everywhere else (no existence leak).

* Not waiting (BUILDING, CERTIFIED, finished, ...): 409 NOT_IN_REVIEW, nothing is signalled.
* The same Idempotency-Key again: nothing is signalled, the answer is the same 202.
  (The workflow also drops a repeated key, so two racing requests still retry once.)
"""

import asyncio
from datetime import timedelta
from uuid import UUID

from sbam_contracts.api import ErrorCode
from sbam_contracts.build import (
    BUILD_STATUS_QUERY,
    RETRY_SIGNAL,
    RetryAccepted,
    SubSkillBuildStatus,
    SubSkillRetry,
)
from sbam_contracts.runs import build_workflow_id
from temporalio.client import Client, WorkflowQueryFailedError
from temporalio.service import RPCError, RPCStatusCode

from sbam_api.core.conventions import ApiError, not_found
from sbam_api.temporal.client import OrchestratorUnavailable

QUERY_TIMEOUT = timedelta(seconds=5)
QUERY_ATTEMPTS = 3
MAX_BUILDS = 100  # a safety cap on the probe in `latest_build_id`
QUERY_BACKOFF_SECONDS = 0.25
TRANSIENT = frozenset({RPCStatusCode.CANCELLED, RPCStatusCode.DEADLINE_EXCEEDED})


class NotInReview(ApiError):  # noqa: N818 - code name from the E10.2 spec
    def __init__(self) -> None:
        super().__init__(
            409,
            ErrorCode.NOT_IN_REVIEW,
            "Sub-Skill is not waiting for a retry",
            "The Sub-Skill is not in NEEDS_FDE_REVIEW or a failed state; nothing was sent.",
        )


async def latest_build_id(client: Client, sub_skill_id: UUID) -> str | None:
    """Id of the Sub-Skill's newest build (highest build_no), running or finished.

    Build numbers are contiguous from 1 (ProjectPipeline starts build_no+1 for each rebuild), so the
    newest is the last one that exists. Probing needs no visibility store, which the dev/test
    Temporal servers don't have; a Sub-Skill has a handful of builds, not hundreds.
    """
    newest: str | None = None
    for build_no in range(1, MAX_BUILDS + 1):
        build_id = build_workflow_id(sub_skill_id, build_no)
        try:
            await client.get_workflow_handle(build_id).describe(rpc_timeout=QUERY_TIMEOUT)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                break
            raise OrchestratorUnavailable() from None
        newest = build_id
    return newest


async def build_status(client: Client, workflow_id: str) -> SubSkillBuildStatus | None:
    """The build's `build_status` query; None when it can't answer (finished without a status)."""
    handle = client.get_workflow_handle(workflow_id)
    for attempt in range(1, QUERY_ATTEMPTS + 1):
        try:
            status: SubSkillBuildStatus = await handle.query(
                BUILD_STATUS_QUERY, result_type=SubSkillBuildStatus, rpc_timeout=QUERY_TIMEOUT
            )
            return status
        except WorkflowQueryFailedError:
            return None
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return None
            if exc.status not in TRANSIENT or attempt == QUERY_ATTEMPTS:
                raise OrchestratorUnavailable() from None
            await asyncio.sleep(QUERY_BACKOFF_SECONDS * attempt)
    return None


async def retry_build(
    client: Client,
    *,
    tenant_id: UUID,
    project_id: UUID,
    workflow_id: UUID,
    sub_skill_id: UUID,
    retry: SubSkillRetry,
) -> RetryAccepted:
    """Signal `retry` to the Sub-Skill's newest build. The caller authorized the FDE and project."""
    build_id = await latest_build_id(client, sub_skill_id)
    if build_id is None:
        raise not_found()
    status = await build_status(client, build_id)
    if status is None:
        raise NotInReview()
    if (status.tenant_id, status.project_id, status.workflow_id, status.sub_skill_id) != (
        tenant_id,
        project_id,
        workflow_id,
        sub_skill_id,
    ):
        raise not_found()
    accepted = RetryAccepted(sub_skill_id=sub_skill_id, build_no=status.build_no)
    if retry.idempotency_key in status.retry_keys:
        return accepted  # this request was already taken: same answer, no second signal
    if not status.waiting_for_retry:
        raise NotInReview()
    try:
        await client.get_workflow_handle(build_id).signal(RETRY_SIGNAL, retry)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:  # finished between the query and the signal
            raise NotInReview() from None
        raise OrchestratorUnavailable() from None
    return accepted
