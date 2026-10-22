"""E10.3 parallel build scheduler: limit, upstream-first ordering, blocked dependents, replay.

Runs the real ProjectPipeline + SubSkillBuild on the time-skipping test server (helpers from
test_project_pipeline). Each test controls the Sub-Skills (`extract_*`) and the plan
(`plan_builds`, still validated by `make_plan`) and watches the children through two stages:
`build_manifest` opens a build, `certify_sub_skill` closes a certified one.
"""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from pipeline_db import new_project
from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import (
    BuildPlan,
    BuildPlanInput,
    BuildProgress,
    IdResult,
    IdsResult,
    MacroRef,
    PipelineStage,
    RebuildSubSkills,
    WorkflowRef,
)
from sbam_workers.build.plan import BuildSettings, make_plan
from sbam_workers.pipeline.project_pipeline import ProjectPipeline
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer
from test_project_pipeline import (
    FDE,
    TENANT,
    Overrides,
    approve,
    environment,
    finish,
    generate,
    name_of,
    run,
    start,
    until,
)

Edges = dict[uuid.UUID, list[uuid.UUID]]


def skills(n: int) -> list[uuid.UUID]:
    """n Sub-Skill ids in the scheduler's tie-break order (sorted by str)."""
    return sorted((uuid.uuid4() for _ in range(n)), key=str)


class Builds:
    """Stand-ins that record build events and the peak number of builds open at once."""

    def __init__(self, ids: list[uuid.UUID], edges: Edges, limit: int, hold: float = 0.2) -> None:
        self.ids, self.edges, self.limit, self.hold = ids, edges, limit, hold
        self.events: list[tuple[str, uuid.UUID]] = []
        self.open = 0
        self.peak = 0
        self.fail_first: set[uuid.UUID] = set()  # these fail their first build (TemplateError)

    def starts(self) -> list[uuid.UUID]:
        return [s for kind, s in self.events if kind == "start"]

    def index(self, kind: str, sub_skill_id: uuid.UUID, nth: int = 0) -> int:
        hits = [i for i, e in enumerate(self.events) if e == (kind, sub_skill_id)]
        return hits[nth]

    def overrides(self) -> Overrides:
        @activity.defn(name="extract_macro_skills")
        async def one_macro(inp: WorkflowRef) -> IdsResult:
            return IdsResult(ids=[uuid.uuid5(inp.run_id, "macro")])

        @activity.defn(name="extract_sub_skills")
        async def sub_skills(inp: MacroRef) -> IdsResult:
            return IdsResult(ids=self.ids)

        @activity.defn(name="plan_builds")
        async def plan(inp: BuildPlanInput) -> BuildPlan:
            return make_plan(inp.sub_skill_ids, self.edges, self.limit)

        @activity.defn(name="build_manifest")
        async def manifest(inp: BuildStageInput) -> IdResult:
            self.events.append(("start", inp.sub_skill_id))
            self.open += 1
            self.peak = max(self.peak, self.open)
            await asyncio.sleep(self.hold)  # real time: keeps builds overlapping
            return IdResult(id=uuid.uuid4())

        @activity.defn(name="render_skeleton")
        async def skeleton(inp: BuildStageInput) -> IdResult:
            if inp.sub_skill_id in self.fail_first and inp.build_no == 1:
                self.events.append(("failed", inp.sub_skill_id))
                self.open -= 1
                raise ApplicationError("template broke", type="TemplateError", non_retryable=True)
            return IdResult(id=uuid.uuid4())

        @activity.defn(name="certify_sub_skill")
        async def certify(inp: BuildStageInput) -> IdResult:
            self.events.append(("certified", inp.sub_skill_id))
            self.open -= 1
            return IdResult(id=uuid.uuid4())

        chosen: list[Callable[..., Any]] = [
            one_macro,
            sub_skills,
            plan,
            manifest,
            skeleton,
            certify,
        ]
        return {name_of(fn): fn for fn in chosen}


async def to_generate(handle: WorkflowHandle[Any, Any]) -> None:
    await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
    await approve(handle)
    await until(handle, PipelineStage.WAIT_GENERATE)
    await generate(handle)


def build_all(builds: Builds) -> PipelineStage:
    async def scenario(sessions: sessionmaker[Session]) -> PipelineStage:
        project = new_project(sessions, TENANT)
        async with environment(builds.overrides()) as env:
            handle = await start(env, sessions, project)
            await to_generate(handle)
            return (await finish(handle)).stage

    stage: PipelineStage = run(scenario)
    return stage


# --- T01 / T02: the limit ------------------------------------------------------------------------


def test_e10_3_t01_default_limit_four_of_ten() -> None:
    ids = skills(10)
    builds = Builds(ids, {}, BuildSettings().max_parallel)
    assert build_all(builds) is PipelineStage.DONE
    assert builds.peak == 4
    assert sorted(builds.starts(), key=str) == ids  # each built exactly once


@pytest.mark.parametrize("limit", [2, 1, 16], ids=["two", "sequential", "above_count"])
def test_e10_3_t02_configured_limit(limit: int) -> None:
    ids = skills(5)
    builds = Builds(ids, {}, limit)
    assert build_all(builds) is PipelineStage.DONE
    assert builds.peak == min(limit, len(ids))
    assert len(builds.starts()) == len(ids)
    if limit == 1:  # fully sequential, in skill id order
        assert builds.starts() == ids


# --- T03 / T04: upstream first, blocked dependents -----------------------------------------------


def test_e10_3_t03_upstream_certified_before_downstream_starts() -> None:
    ms01_1, ms01_2, ms02_1 = skills(3)
    # Make the dependent sort first, so only the graph (not the tie-break) can order it last.
    ms01_2, ms01_1 = sorted([ms01_1, ms01_2], key=str)
    builds = Builds([ms01_1, ms01_2, ms02_1], {ms01_2: [ms01_1]}, 4)
    assert build_all(builds) is PipelineStage.DONE
    assert builds.index("certified", ms01_1) < builds.index("start", ms01_2)
    assert builds.index("start", ms02_1) < builds.index("certified", ms01_1)  # ran alongside


def test_e10_3_diamond_dependency() -> None:
    a, b, c, d = skills(4)
    builds = Builds([d, c, b, a], {b: [a], c: [a], d: [b, c]}, 4)
    assert build_all(builds) is PipelineStage.DONE
    starts = builds.starts()  # b and c start together; their activities may reach us in any order
    assert (starts[0], set(starts[1:3]), starts[3]) == (a, {b, c}, d)
    for up, down in [(a, b), (a, c), (b, d), (c, d)]:
        assert builds.index("certified", up) < builds.index("start", down)


def test_e10_3_t04_failed_upstream_blocks_only_its_dependents() -> None:
    ms01_1, ms01_2, ms02_1 = skills(3)
    builds = Builds([ms01_1, ms01_2, ms02_1], {ms01_2: [ms01_1]}, 4)
    builds.fail_first = {ms01_1}

    async def scenario(sessions: sessionmaker[Session]) -> tuple[BuildProgress, PipelineStage]:
        project = new_project(sessions, TENANT)
        async with environment(builds.overrides()) as env:
            handle = await start(env, sessions, project)
            await to_generate(handle)
            await until(handle, PipelineStage.WAIT_REBUILD)
            progress: BuildProgress = await handle.query(
                "build_schedule", result_type=BuildProgress
            )
            # The FDE rebuilds only the upstream; its blocked dependent comes along.
            rebuild = RebuildSubSkills(sub_skill_ids=[ms01_1], idempotency_key="rb", actor_id=FDE)
            await handle.signal("rebuild_subskills", rebuild)
            return progress, (await finish(handle)).stage

    progress, stage = run(scenario)
    assert progress.blocked == [ms01_2]
    assert (progress.certified, progress.needs_review) == (1, 2)  # ms02_1; ms01_1 + ms01_2
    assert ("certified", ms02_1) in builds.events
    assert builds.starts().count(ms01_2) == 1  # never started while its upstream was failed
    assert builds.index("certified", ms01_1) < builds.index("start", ms01_2)
    assert stage is PipelineStage.DONE


def test_e10_3_rebuild_subset_keeps_certified_ones() -> None:
    a, b, c = skills(3)
    builds = Builds([a, b, c], {}, 4)
    builds.fail_first = {b}

    async def scenario(sessions: sessionmaker[Session]) -> PipelineStage:
        project = new_project(sessions, TENANT)
        async with environment(builds.overrides()) as env:
            handle = await start(env, sessions, project)
            await to_generate(handle)
            await until(handle, PipelineStage.WAIT_REBUILD)
            rebuild = RebuildSubSkills(sub_skill_ids=[b], idempotency_key="rb", actor_id=FDE)
            await handle.signal("rebuild_subskills", rebuild)
            return (await finish(handle)).stage

    assert run(scenario) is PipelineStage.DONE
    assert sorted(builds.starts(), key=str) == [a, b, b, c]  # only b built twice


def test_e10_3_cycle_fails_at_generate() -> None:
    a, b = skills(2)
    builds = Builds([a, b], {a: [b], b: [a]}, 4)

    async def scenario(sessions: sessionmaker[Session]) -> Any:
        project = new_project(sessions, TENANT)
        async with environment(builds.overrides()) as env:
            handle = await start(env, sessions, project)
            await to_generate(handle)
            return await finish(handle)

    status = run(scenario)
    assert status.stage is PipelineStage.FAILED
    assert status.last_error == "plan_builds: DependencyCycle"
    assert builds.starts() == []


# --- T06: deterministic order under replay -------------------------------------------------------


def test_e10_3_t06_six_skill_run_replays_with_the_same_start_order() -> None:
    ids = skills(6)
    a, b, c, d, e, _f = ids
    edges: Edges = {d: [a], e: [b, c]}
    builds = Builds(list(reversed(ids)), edges, 2, hold=0.05)

    async def scenario(sessions: sessionmaker[Session]) -> list[uuid.UUID]:
        project = new_project(sessions, TENANT)
        async with environment(builds.overrides()) as env:
            handle = await start(env, sessions, project)
            await to_generate(handle)
            assert (await finish(handle)).stage is PipelineStage.DONE
            history = await handle.fetch_history()
        replayer = Replayer(workflows=[ProjectPipeline], data_converter=pydantic_data_converter)
        await replayer.replay_workflow(history)  # raises on any nondeterminism
        started = [
            event.start_child_workflow_execution_initiated_event_attributes.workflow_id
            for event in history.events
            if event.HasField("start_child_workflow_execution_initiated_event_attributes")
        ]
        return [uuid.UUID(w.removeprefix("build-")[:36]) for w in started]

    order = run(scenario)  # child start commands, as recorded in the history
    assert sorted(order, key=str) == ids
    assert order[:2] == [a, b]  # limit 2, ties broken by skill id (c and f are ready too)
    assert order.index(a) < order.index(d)
    assert max(order.index(b), order.index(c)) < order.index(e)
