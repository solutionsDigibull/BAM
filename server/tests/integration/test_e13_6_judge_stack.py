"""E13.6 judging on the Compose stack (#127): T01 and T07 against the real Postgres and the real
`promptfoo` sidecar.

What is real and what is not:

* The `core` stack that CI's compose-smoke job brings up has no sandbox runner: `sandbox-runner`
  (local docker, non-certifying) is only in the `sandbox-local` profile, and CubeSandbox is a KVM
  host. So neither test runs a bundle. The runner's `outputs.json` is written here in the shape
  the runner writes it and parsed the way `run_sandbox_tests` parses it
  (`RunnerOutputs.model_validate_json`, `sandbox/activity.py` `_judged`). Everything after that
  boundary is the product's: `StageJudge.judge`, the suite loaded from the stored dataset, the
  assertions, `record_run` and the `test_runs` table on Postgres at head.
* T07's grader is the real `SidecarGrader`, run inside the real `worker-test` container (the only
  place the shared `promptfoo-jobs` volume is mounted, as `PROMPTFOO_JOBS_DIR`), talking to the
  real `promptfoo` container. The sidecar is made to answer with no verdicts and an error by a job
  it must refuse: a rubric longer than its 40 000-character limit (`compose/docker/promptfoo/
  runner.mjs`, `checkRequest`). This is the sidecar's own "no results" answer, not a crash of the
  `promptfoo` CLI; neither Bifrost nor the shared mock upstream is touched (see
  `test_e13_6c_promptfoo_sidecar.py` for why the gateway is never taken down here).
* T07's suite is given to the judge directly. `load_suite` (`test/promptfoo_suite.py`) calls
  `build_suite` without a rubric, and `build_suite` drops any `llm_rubric` a stored case carries
  (`test/suite.py` `_assertions`), so no suite loaded from the database asks the grader anything.

Each test seeds its own tenant and project in a scratch database (dropped at the end).
Needs `make up`; not part of the unit run.
"""

import asyncio
import json
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest
from sbam_api.builds.models import CodeAttempt
from sbam_api.manifests.models import GateResultRow
from sbam_api.testruns.models import TestRunRow
from sbam_api.testruns.repository import TestRunRepository
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.promptfoo import AssertionType, TestRunState
from sbam_contracts.sandbox import FailureCategory, RunnerOutputs
from sbam_workers.sandbox.classify import CaseVerdict, Classified
from sbam_workers.sandbox.judging import StageJudge
from sbam_workers.sandbox.load import Identity
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .certify_seed import CASES, DIGEST, Build, current_suite, isatvon_of, scratch_db, seed_build
from .conftest import DOCKER_COMPOSE, Compose

pytestmark = pytest.mark.integration

TERMINAL = {name: terminal for name, _, terminal in CASES}
SIDECAR_MAX_TEXT = 40_000  # runner.mjs MAX_TEXT: a longer rubric is refused


@pytest.fixture(scope="module")
def db() -> Iterator[tuple[sessionmaker[Session], str]]:
    with scratch_db("sbam_e136") as made:
        yield made


def raw_case(name: str, handoff: str | None) -> dict[str, Any]:
    """One case of the runner's outputs.json; `handoff=None` is a tool that never emits
    `state_handoff_id`."""
    terminal = TERMINAL[name]
    check: dict[str, Any] = {} if handoff is None else {"state_handoff_id": handoff}
    return {
        "case_id": name,
        "expected_terminal": terminal,
        "terminal": terminal,
        "passed": True,  # the runner's own classification: the harness reached the terminal
        "trace": {
            "case_id": name,
            "terminal": terminal,
            "iterations": 1,
            "passes": [
                {
                    "pass": 1,
                    "fresh_state": {},
                    "action_output": {},
                    "check_output": check,
                    "action_ms": 1,
                    "check_ms": 1,
                    "ui_update": "Looping [Pass 1/3]",
                    "gate_eval": {"success": terminal == "SUCCESS", "escalation": False},
                }
            ],
            "ui_updates": ["Looping [Pass 1/3]", "Status: Completed"],
            "handoff_rows": 1,
        },
        "handoff_rows": [{"iteration_pass": 1}],
    }


def outputs_json(handoff: str | None) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "runtime_digest": DIGEST,
            "cases": [raw_case(name, handoff) for name, _, _ in CASES],
            "resources": {"wall_ms": 1, "cpu_ms": 1, "peak_rss_kb": 1},
            "mode": "LOCAL_DOCKER_NONCERT",
        }
    )


def runner_passed() -> Classified:
    """The classifier's verdict on a run whose cases all reached their terminal state."""
    return Classified(None, None, tuple(CaseVerdict(name, True, None) for name, _, _ in CASES))


def runs_of(build: Build) -> list[TestRunRow]:
    with build.sessions() as s:
        query = select(TestRunRow).where(
            TestRunRow.tenant_id == build.ctx.tenant_id,
            TestRunRow.code_attempt_id == build.attempt_id,
        )
        return list(s.scalars(query))


# --- E13.6-T01 ------------------------------------------------------------------------------


def test_e13_6_t01_known_bad_tool_fails_stack(db: tuple[sessionmaker[Session], str]) -> None:
    """A tool whose outputs never carry `state_handoff_id`: the judge fails the run the runner
    called green, and the `test_runs` row on Postgres says so, naming the assertion."""
    build = seed_build(db[0], sandbox_mode="LOCAL_DOCKER_NONCERT")
    assert build.sandbox_run_id is not None
    outputs = RunnerOutputs.model_validate_json(outputs_json(handoff=None))
    judge = StageJudge(build.sessions, isatvon_of)  # no rubric in a stored suite: no grader
    verdict = asyncio.run(
        judge.judge(
            build.inp,
            Identity(build.attempt_id, build.dataset_id, DIGEST),
            build.sandbox_run_id,
            outputs,
            runner_passed(),
        )
    )

    # the corrector is told: ASSERTION_FAILED, naming the field (never its value)
    assert verdict.category is FailureCategory.ASSERTION_FAILED
    happy = next(c for c in verdict.cases if c.case_id == "happy")
    assert not happy.passed and "state_handoff_id" in (happy.message or ""), happy

    with build.sessions() as s:
        row = TestRunRepository(s, build.ctx).for_sandbox_run(build.sandbox_run_id)
    assert row is not None, "no test_runs row was written"
    assert row.state == TestRunState.FAIL.value  # a red run is FAIL, also from a local runner
    assert row.failed >= 1 and row.total == len(CASES) and row.passed + row.failed == row.total
    assert row.non_certifying is True  # propagated from the run's mode
    assert row.suite_sha256 == current_suite(build)  # the suite built from the stored dataset
    handoff = [r for r in row.results if r["type"] == AssertionType.STATE_HANDOFF_ID.value]
    success_cases = {name for name, _, terminal in CASES if terminal == "SUCCESS"}
    assert {r["case"] for r in handoff} == success_cases, row.results
    for result in handoff:
        assert result["passed"] is False, result
        assert "state_handoff_id" in result["message"], result
    assert len(runs_of(build)) == 1


# --- E13.6-T07 ------------------------------------------------------------------------------

# Runs inside worker-test (`python -c`): the real SidecarGrader on the real jobs directory, the real
# StageJudge on the scratch database, and the activity's own mapping of a blocked judge. Prints one
# `RESULT {json}` line; never raises past the steps it reports.
DRIVER = r"""
import asyncio, json, os, sys, uuid
from pathlib import Path

from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_contracts.build import BuildStageInput
from sbam_contracts.promptfoo import AssertionType, Suite, SuiteAssertion, SuiteBounds, SuiteCase
from sbam_contracts.sandbox import RunnerOutputs
from sbam_workers.sandbox.activity import unavailable
from sbam_workers.sandbox.classify import CaseVerdict, Classified
from sbam_workers.sandbox.judging import JudgeBlocked, StageJudge
from sbam_workers.sandbox.load import Identity
from sbam_workers.test.grader import SidecarGrader
from sbam_workers.test.judge import PromptfooInfraError
from sqlalchemy import pool
from sqlalchemy.engine import make_url

db, tenant, project, workflow, sub, attempt, dataset, run_id, rubric_len = sys.argv[1:10]
outputs = RunnerOutputs.model_validate_json(sys.stdin.read())
terminals = {c.case_id: c.expected_terminal for c in outputs.cases}
kinds = {"happy": "PASS_FIRST", "noop": "NO_OP", "stuck": "ESCALATION"}
DETERMINISTIC = [SuiteAssertion(type=AssertionType.TERMINAL_STATE),
                 SuiteAssertion(type=AssertionType.STATE_HANDOFF_ID)]

def case(name, rubric):
    extra = [] if rubric is None else [SuiteAssertion(type=AssertionType.LLM_RUBRIC, value=rubric)]
    return SuiteCase(name=name, kind=kinds[name], input={}, expected_terminal=terminals[name],
                     assertions=DETERMINISTIC + extra)

def suite(rubric):
    return Suite(
        bounds=SuiteBounds(max_iterations=3, budget_ms=60000, handoff_fields=[]),
        output_schema={"type": "object", "required": ["state_handoff_id"],
                       "properties": {"state_handoff_id": {"type": "string"}}},
        cases=[case(n, rubric if n == "happy" else None) for n in kinds],
    )

out = {}
grader = SidecarGrader(Path(os.environ["PROMPTFOO_JOBS_DIR"]), timeout=180.0)
good = case("happy", "The text is polite.")
bad = case("happy", "x" * int(rubric_len))
try:  # the sidecar is up and grades a well-formed job
    out["healthy"] = {"verdicts": grader.grade([good], outputs)}
except Exception as exc:
    out["healthy"] = {"error": type(exc).__name__, "message": str(exc)[:300]}
try:  # the job it must refuse
    out["direct"] = {"verdicts": grader.grade([bad], outputs)}
except Exception as exc:
    out["direct"] = {"error": type(exc).__name__, "message": str(exc)[:300]}

url = make_url(os.environ["DATABASE_URL"]).set(database=db).render_as_string(hide_password=False)
engine = make_engine(url, poolclass=pool.NullPool)
calls = []

async def counted(cases, run_outputs):
    calls.append(len(cases))
    return await grader(cases, run_outputs)

async def no_isatvon(_):
    raise AssertionError("the suite is given; the ISATVON is not read")

class GivenSuite(StageJudge):
    async def _suite(self, inp):
        return suite("x" * int(rubric_len))

inp = BuildStageInput(tenant_id=uuid.UUID(tenant), project_id=uuid.UUID(project),
                      run_id=uuid.uuid4(), correlation_id="cid-e13-6-t07",
                      workflow_id=uuid.UUID(workflow), sub_skill_id=uuid.UUID(sub), build_no=1)
passed = Classified(None, None, tuple(CaseVerdict(n, True, None) for n in kinds))
try:
    got = asyncio.run(GivenSuite(make_sessionmaker(engine), no_isatvon, counted).judge(
        inp, Identity(uuid.UUID(attempt), uuid.UUID(dataset), outputs.runtime_digest),
        uuid.UUID(run_id), outputs, passed))
    out["judge"] = {"returned": None if got.category is None else got.category.value}
except JudgeBlocked as blocked:
    err = unavailable(blocked.reason, blocked.detail)
    out["judge"] = {"raised": "JudgeBlocked", "reason": blocked.reason.value,
                    "activity_error": err.type, "non_retryable": err.non_retryable}
except Exception as exc:
    out["judge"] = {"raised": type(exc).__name__, "message": str(exc)[:300]}
out["grader_calls"] = len(calls)
engine.dispose()
print("RESULT " + json.dumps(out))
"""


def drive_worker_test(compose: Compose, build: Build, database: str) -> dict[str, Any]:
    for service in ("worker-test", "promptfoo"):
        if not compose("ps", "-q", service).stdout.strip():
            pytest.fail(f"the {service} service is not running in this stack (make up)")
    assert build.sandbox_run_id is not None
    args = [
        database,
        str(build.ctx.tenant_id),
        str(build.ctx.project_id),
        str(build.inp.workflow_id),
        str(build.inp.sub_skill_id),
        str(build.attempt_id),
        str(build.dataset_id),
        str(build.sandbox_run_id),
        str(SIDECAR_MAX_TEXT + 1),
    ]
    # the outputs go in on stdin: the `compose` fixture takes no input
    run = subprocess.run(  # noqa: S603 - fixed docker argv, no shell
        [*DOCKER_COMPOSE, "exec", "-T", "worker-test", "python", "-c", DRIVER, *args],
        input=outputs_json(handoff="h-1"),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    line = next((x for x in run.stdout.splitlines() if x.startswith("RESULT ")), None)
    if line is None:
        pytest.fail(f"driver failed ({run.returncode}): {run.stderr[-3000:]}")
    return dict(json.loads(line.removeprefix("RESULT ")))


def test_e13_6_t07_promptfoo_error_is_infra_not_correction_stack(
    compose: Compose, db: tuple[sessionmaker[Session], str]
) -> None:
    """The sidecar answers with an error and no verdicts: the real grader raises the retryable
    `PromptfooInfraError`, the judge asks three times and then blocks testing (SandboxUnavailable,
    retryable, the TESTING_BLOCKED path), and no test_runs row and no correction are written."""
    sessions, database = db
    build = seed_build(sessions, sandbox_mode="LOCAL_DOCKER_NONCERT")
    got = drive_worker_test(compose, build, database)

    # the sidecar is alive and grades a well-formed job: the failure below is the job's, not a
    # missing container or a dead gateway
    assert got["healthy"] == {"verdicts": {"happy": True}}, got
    # anything but a clean answer is the infrastructure's failure, never "rubric not met"
    assert got["direct"]["error"] == "PromptfooInfraError", got
    assert "refused" in got["direct"]["message"], got  # the sidecar's own answer, not a timeout
    # retried (GRADER_ATTEMPTS = 3), then the judge blocks; the activity maps that to a
    # retryable SandboxUnavailable (sandbox/activity.py: the workflow parks TESTING_BLOCKED)
    assert got["grader_calls"] == 3, got
    assert got["judge"] == {
        "raised": "JudgeBlocked",
        "reason": "RUNNER_FAILED",
        "activity_error": "SandboxUnavailable",
        "non_retryable": False,
    }, got

    # nothing was scored as the code's fault, and no correction was started
    assert runs_of(build) == []
    with build.sessions() as s:
        loop_gates = s.scalar(
            select(func.count())
            .select_from(GateResultRow)
            .where(
                GateResultRow.tenant_id == build.ctx.tenant_id,
                GateResultRow.sub_skill_id == build.inp.sub_skill_id,
                GateResultRow.gate == GateName.LOOP_TESTS.value,
            )
        )
        attempts = list(
            s.scalars(
                select(CodeAttempt.attempt).where(
                    CodeAttempt.tenant_id == build.ctx.tenant_id,
                    CodeAttempt.sub_skill_id == build.inp.sub_skill_id,
                )
            )
        )
    assert loop_gates == 0
    assert attempts == [0]  # no correction attempt was made
