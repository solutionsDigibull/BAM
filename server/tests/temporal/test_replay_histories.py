"""E10.6 replay gate (TD §13.4): every recorded history replays against the workflow code of this
branch. CI runs this file as the `temporal-replay` job (.github/workflows/replay.yml).

- T01: each `histories/*.json` is replayed by the registered workflow named in its first event, so a
  new history is picked up without touching this file.
- T02-T04: proof that the gate catches what it should. A copy of SubSkillBuild with an activity
  inserted before generate_bodies fails replay; the same change behind `workflow.patched` passes; a
  new activity implementation under the same name passes, a renamed activity does not.
- T05: histories are committed, so they are scanned for tokens and personal data (gitleaks scans the
  commits too, in ci.yml `secret-scan` and replay.yml).
- T06: the histories cover the TD §14 paths: correction exhaustion, cancel and TESTING_BLOCKED.

Re-record with `make record-histories` and review the diff. Keep the old history next to the new one
(`<name>_before_<change>.json`) until no run started on the old code can still be running.
"""

import asyncio
import base64
import json
import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sbam_contracts.build import BuildStage, BuildStageInput
from sbam_contracts.pipeline import (
    PROJECT_PIPELINE,
    SUB_SKILL_BUILD,
    IdResult,
    SubSkillBuildInput,
    SubSkillBuildResult,
)
from sbam_contracts.runs import (
    TaskQueue,
    build_workflow_id,
    ping_workflow_id,
    pipeline_workflow_id,
)
from sbam_workers import registry
from sbam_workers.pipeline.sub_skill_build import SubSkillBuild
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

HISTORIES = Path(__file__).parent / "histories"
SUB_SKILL_HISTORY = HISTORIES / "sub_skill_build.json"


def histories() -> list[Path]:
    return sorted(HISTORIES.glob("*.json"))


def started(path: Path) -> dict[str, Any]:
    attributes: dict[str, Any] = json.loads(path.read_text("utf-8"))["events"][0][
        "workflowExecutionStartedEventAttributes"
    ]
    return attributes


def workflow_type(path: Path) -> str:
    name: str = started(path)["workflowType"]["name"]
    return name


def workflow_id(path: Path) -> str:
    """A valid id for the history's workflow type. Histories don't record the id, and workflows
    validate their own id (the run mirror), so any well-formed one replays."""
    anyone = UUID(int=0)
    ids: dict[str, Callable[[], str]] = {
        PROJECT_PIPELINE: lambda: pipeline_workflow_id(anyone),
        SUB_SKILL_BUILD: lambda: build_workflow_id(anyone, 1),
        "PingWorkflow": lambda: ping_workflow_id(
            TaskQueue(started(path)["taskQueue"]["name"]), anyone
        ),
    }
    name = workflow_type(path)
    assert name in ids, f"{path.name}: add a workflow id for {name} to test_replay_histories.py"
    return ids[name]()


def load(path: Path) -> WorkflowHistory:
    return WorkflowHistory.from_json(workflow_id(path), json.loads(path.read_text("utf-8")))


def registered_workflows() -> dict[str, type]:
    """Workflow name -> class, from the same registry the workers start with."""
    registry.load_all()
    found: dict[str, type] = {}
    for queue in TaskQueue:
        for cls in registry.collect(queue)[0]:
            found[getattr(cls, "__temporal_workflow_definition").name] = cls
    return found


def replay(path: Path, workflow_cls: type, sandboxed: bool = True) -> None:
    replayer = (
        Replayer(workflows=[workflow_cls], data_converter=pydantic_data_converter)
        if sandboxed
        else Replayer(
            workflows=[workflow_cls],
            data_converter=pydantic_data_converter,
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
    )
    asyncio.run(replayer.replay_workflow(load(path)))


def replay_variant(workflow_cls: type) -> None:
    """The T02-T04 variants live in this test module, which the sandbox can't re-import (it reads
    the histories directory at import). The sandbox is not what detects nondeterminism, so they
    replay unsandboxed; the real workflows in T01 replay sandboxed."""
    replay(SUB_SKILL_HISTORY, workflow_cls, sandboxed=False)


# --- T01: every history replays ------------------------------------------------------------------


@pytest.mark.parametrize("path", histories(), ids=lambda p: p.stem)
def test_e10_6_t01_history_replays(path: Path) -> None:
    workflows = registered_workflows()
    name = workflow_type(path)
    assert name in workflows, f"{path.name}: no registered workflow named {name}"
    replay(path, workflows[name])


# --- T02-T04: the gate catches a nondeterministic change and only that ---------------------------


@activity.defn(name="add_x")
async def add_x(inp: BuildStageInput) -> IdResult:  # never runs: replay doesn't call activities
    raise AssertionError("replay must not run activities")


@activity.defn(name="generate_bodies")
async def rewritten_generate_bodies(inp: BuildStageInput) -> IdResult:  # T04: new body, same name
    raise AssertionError("replay must not run activities")


@activity.defn(name="generate_bodies_v2")
async def renamed_generate_bodies(inp: BuildStageInput) -> IdResult:
    raise AssertionError("replay must not run activities")


async def run_add_x(payload: Any) -> None:
    await workflow.execute_activity(
        add_x, payload, task_queue="sbam-build", start_to_close_timeout=timedelta(minutes=1)
    )


@workflow.defn(name=SUB_SKILL_BUILD)
class InsertedActivity(SubSkillBuild):
    """T02: a new activity before generate_bodies, not guarded."""

    @workflow.run
    async def run(self, inp: SubSkillBuildInput) -> SubSkillBuildResult:
        return await super().run(inp)

    async def _call(self, stage: BuildStage, fn: Callable[..., Any], payload: Any) -> Any:
        if stage is BuildStage.GENERATE_BODIES:
            await run_add_x(payload)
        return await super()._call(stage, fn, payload)


@workflow.defn(name=SUB_SKILL_BUILD)
class PatchedActivity(SubSkillBuild):
    """T03: the same change behind `workflow.patched("add-x")`."""

    @workflow.run
    async def run(self, inp: SubSkillBuildInput) -> SubSkillBuildResult:
        return await super().run(inp)

    async def _call(self, stage: BuildStage, fn: Callable[..., Any], payload: Any) -> Any:
        if stage is BuildStage.GENERATE_BODIES and workflow.patched("add-x"):
            await run_add_x(payload)
        return await super()._call(stage, fn, payload)


class Swapped(SubSkillBuild):
    """Calls `replacement` where SubSkillBuild calls generate_bodies."""

    replacement: Callable[..., Any]

    async def _call(self, stage: BuildStage, fn: Callable[..., Any], payload: Any) -> Any:
        if stage is BuildStage.GENERATE_BODIES:
            fn = self.replacement
        return await super()._call(stage, fn, payload)


@workflow.defn(name=SUB_SKILL_BUILD)
class RewrittenActivity(Swapped):
    """T04: generate_bodies has a new implementation; its name is unchanged."""

    replacement = rewritten_generate_bodies

    @workflow.run
    async def run(self, inp: SubSkillBuildInput) -> SubSkillBuildResult:
        return await super().run(inp)


@workflow.defn(name=SUB_SKILL_BUILD)
class RenamedActivity(Swapped):
    """Edge case: generate_bodies renamed, which running builds can't follow."""

    replacement = renamed_generate_bodies

    @workflow.run
    async def run(self, inp: SubSkillBuildInput) -> SubSkillBuildResult:
        return await super().run(inp)


def test_e10_6_t02_unpatched_activity_before_generate_bodies_fails_replay() -> None:
    with pytest.raises(NondeterminismError):
        replay_variant(InsertedActivity)


def test_e10_6_t03_same_change_behind_patched_replays() -> None:
    replay_variant(PatchedActivity)


def test_e10_6_t04_activity_implementation_change_replays() -> None:
    replay_variant(RewrittenActivity)


def test_e10_6_activity_rename_fails_replay() -> None:
    with pytest.raises(NondeterminismError):
        replay_variant(RenamedActivity)


# --- T05: nothing secret or personal in a committed history --------------------------------------

SECRETS = {
    "bearer token": re.compile(r"bearer\s+[a-z0-9._~+/-]{8,}", re.I),
    "jwt": re.compile(r"eyJ[a-zA-Z0-9_-]{8,}\.eyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]+"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "api key": re.compile(r"\b(sk|pk|rk)[-_](live|test|or|ant)?[-_]?[a-zA-Z0-9]{16,}"),
    "password": re.compile(r"(password|passwd|secret|api_key|client_secret)\"?\s*[:=]", re.I),
    "email": re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I),
}


def decoded(path: Path) -> str:
    """The history text with every payload's base64 `data` decoded, so a token inside an activity
    input or result is visible to the scan."""
    text = path.read_text("utf-8")
    payloads = re.findall(r'"data":\s*"([A-Za-z0-9+/=]+)"', text)
    return "\n".join([text, *(base64.b64decode(p).decode("utf-8", "replace") for p in payloads)])


def leaks(text: str) -> list[str]:
    return sorted(kind for kind, pattern in SECRETS.items() if pattern.search(text))


@pytest.mark.parametrize("path", histories(), ids=lambda p: p.stem)
def test_e10_6_t05_history_has_no_secrets_or_pii(path: Path) -> None:
    assert leaks(decoded(path)) == []


def test_e10_6_t05_scan_flags_a_bearer_token_inside_a_payload(tmp_path: Path) -> None:
    data = json.loads(SUB_SKILL_HISTORY.read_text("utf-8"))
    secret = base64.b64encode(b'{"authorization": "Bearer abcdef0123456789xyz"}').decode()
    data["events"][0]["workflowExecutionStartedEventAttributes"]["input"]["payloads"][0][
        "data"
    ] = secret
    planted = tmp_path / "planted.json"
    planted.write_text(json.dumps(data), "utf-8")
    assert "bearer token" in leaks(decoded(planted))


# --- T06: the histories cover the paths that matter ----------------------------------------------


def transitions(path: Path) -> set[str]:
    """The SubSkillBuild state-machine actions and signals a history went through."""
    seen: set[str] = set()
    for event in json.loads(path.read_text("utf-8"))["events"]:
        if signal := event.get("workflowExecutionSignaledEventAttributes"):
            seen.add(f"signal:{signal['signalName']}")
        scheduled = event.get("activityTaskScheduledEventAttributes")
        if scheduled and scheduled["activityType"]["name"] == "apply_pipeline_transition":
            data = scheduled["input"]["payloads"][0]["data"]
            seen.add(json.loads(base64.b64decode(data))["event"])
    return seen


@pytest.mark.parametrize(
    "path_name, marker",
    [
        ("correction exhaustion", "attempts_exhausted"),
        ("cancel", "signal:cancel"),
        ("TESTING_BLOCKED", "sandbox_unavailable"),
        ("FDE retry", "signal:retry"),
        ("certified", "tests_passed"),
    ],
)
def test_e10_6_t06_histories_cover_the_build_paths(path_name: str, marker: str) -> None:
    builds = [p for p in histories() if workflow_type(p) == SUB_SKILL_BUILD]
    assert any(marker in transitions(p) for p in builds), f"no history covers {path_name}"


def test_e10_6_t06_pipeline_build_and_ping_have_histories() -> None:
    recorded = {workflow_type(p) for p in histories()}
    assert {"ProjectPipeline", SUB_SKILL_BUILD, "PingWorkflow"} <= recorded
