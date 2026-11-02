"""Test runs of a Sub-Skill (E13.6): the stored runs and the grid badge.

The Sub-Skill is looked up through the tenant- and project-scoped repository, so another tenant's,
an unassigned project's or another Workflow's is 404.
"""

import json
import logging
import uuid

from sbam_contracts.harness import HarnessTrace, TestRunTraces
from sbam_contracts.promptfoo import (
    AssertionResult,
    LoopTestsBadge,
    TestRunOut,
    TestRunsResponse,
    TestRunState,
    badge_of,
)
from sqlalchemy.orm import Session

from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.storage.base import StorageBackend, parse_key
from sbam_api.testruns.models import TestRunRow
from sbam_api.testruns.repository import TestRunRepository

log = logging.getLogger("sbam_api.testruns")


def run_out(row: TestRunRow) -> TestRunOut:
    return TestRunOut(
        id=row.id,
        sub_skill_id=row.sub_skill_id,
        code_attempt_id=row.code_attempt_id,
        sandbox_run_id=row.sandbox_run_id,
        suite_sha256=row.suite_sha256,
        state=TestRunState(row.state),
        total=row.total,
        passed=row.passed,
        failed=row.failed,
        non_certifying=row.non_certifying,
        results=[AssertionResult.model_validate(r) for r in row.results],
        report_key=row.report_key,
        created_at=row.created_at,
    )


def loop_tests_badge(
    session: Session, ctx: TenantContext, sub_skill_id: uuid.UUID
) -> LoopTestsBadge | None:
    """The 'Loop tests' cell for a Sub-Skill's newest run, None before any run (E12.6b uses it)."""
    row = TestRunRepository(session, ctx).latest(sub_skill_id)
    return None if row is None else badge_of(run_out(row))


def _sub_skill(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_id: uuid.UUID
) -> SubSkill:
    sub: SubSkill | None = SubSkillRepository(session, ctx).get(sub_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    return sub


def list_runs(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_id: uuid.UUID
) -> TestRunsResponse:
    sub = _sub_skill(session, ctx, workflow_id, sub_id)
    return TestRunsResponse(
        runs=[run_out(row) for row in TestRunRepository(session, ctx).of_sub_skill(sub.id)]
    )


def stored_traces(
    storage: StorageBackend, ctx: TenantContext, key: str | None
) -> list[HarnessTrace]:
    """The traces the run stage stored with the run (`report_key`: `{"traces": [...]}`).

    A run without a stored document, an object that is gone, one stored for another tenant, or one
    that does not parse gives an empty list (the test panel then shows "No trace recorded"); the
    last two are logged by id, never by content."""
    if key is None:
        return []
    try:
        tenant_id, _ = parse_key(key)
        if tenant_id != ctx.tenant_id or not storage.exists(key):
            return []
        document = json.loads(storage.get(key, verify=True))
        return [HarnessTrace.model_validate(item) for item in document["traces"]]
    except (ApiError, ValueError, KeyError, TypeError):
        log.warning("a stored test run trace document is unreadable", extra={"key": key})
        return []


def run_traces(
    session: Session,
    storage: StorageBackend,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    run_id: uuid.UUID,
) -> TestRunTraces:
    """The harness traces of one of the Sub-Skill's runs (E13.7). A run of another Sub-Skill is 404.

    The run stage stores them with the run (#526); a run from before that has none and the answer
    is an empty list, which the test panel shows as "No trace recorded".
    """
    sub = _sub_skill(session, ctx, workflow_id, sub_id)
    row = TestRunRepository(session, ctx).get(run_id)
    if row is None or row.sub_skill_id != sub.id:
        raise not_found()
    return TestRunTraces(run_id=row.id, traces=stored_traces(storage, ctx, row.report_key))
