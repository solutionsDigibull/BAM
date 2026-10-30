"""Test runs of a Sub-Skill (E13.6): the stored runs and the grid badge.

The Sub-Skill is looked up through the tenant- and project-scoped repository, so another tenant's,
an unassigned project's or another Workflow's is 404.
"""

import uuid

from sbam_contracts.harness import TestRunTraces
from sbam_contracts.promptfoo import (
    AssertionResult,
    LoopTestsBadge,
    TestRunOut,
    TestRunsResponse,
    TestRunState,
    badge_of,
)
from sqlalchemy.orm import Session

from sbam_api.core.conventions import not_found
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.testruns.models import TestRunRow
from sbam_api.testruns.repository import TestRunRepository


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


def run_traces(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    run_id: uuid.UUID,
) -> TestRunTraces:
    """The harness traces of one of the Sub-Skill's runs (E13.7). A run of another Sub-Skill is 404.

    The traces are recorded with the run by the Promptfoo runner wiring (#526); until then a run has
    none and the answer is an empty list, which the test panel shows as "No trace recorded".
    """
    sub = _sub_skill(session, ctx, workflow_id, sub_id)
    row = TestRunRepository(session, ctx).get(run_id)
    if row is None or row.sub_skill_id != sub.id:
        raise not_found()
    return TestRunTraces(run_id=row.id)
