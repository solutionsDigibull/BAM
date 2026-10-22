"""Build plan (E10.3): the parallel limit and the Sub-Skill dependency graph for one generate.

`ProjectPipeline` calls `plan_builds` once per generate and keeps the result in workflow state, so
changing `BUILD_MAX_PARALLEL` mid-run applies only to the next generate. The graph is validated
here: an edge to an unknown Sub-Skill is `UnknownSubSkill`, a cycle is `DependencyCycle`, both
non-retryable.

STUB edges: the real `depends_on` + output->input links come from the E9.5 DAG (#105); until then
every Sub-Skill is independent.
"""

from collections.abc import Iterable, Mapping
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sbam_contracts.pipeline import (
    MAX_PARALLEL_BUILDS,
    MAX_PARALLEL_LIMIT,
    BuildPlan,
    BuildPlanInput,
)
from sbam_contracts.runs import TaskQueue
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.registry import activity_for


class BuildSettings(BaseSettings):
    """Platform config `build.max_parallel` (env `BUILD_MAX_PARALLEL`); a bad value is refused."""

    model_config = SettingsConfigDict(env_prefix="BUILD_", env_file=".env", extra="ignore")

    max_parallel: int = Field(default=MAX_PARALLEL_BUILDS, ge=1, le=MAX_PARALLEL_LIMIT)


def make_plan(
    sub_skill_ids: Iterable[UUID], depends_on: Mapping[UUID, Iterable[UUID]], max_parallel: int
) -> BuildPlan:
    known = set(sub_skill_ids)
    edges = {s: sorted(set(ups), key=str) for s, ups in depends_on.items() if ups}
    unknown = {s for s, ups in edges.items() for x in (s, *ups) if x not in known}
    if unknown:
        raise ApplicationError(
            "depends_on names unknown Sub-Skills",
            sorted(map(str, unknown)),
            type="UnknownSubSkill",
            non_retryable=True,
        )
    cycle = find_cycle(edges)
    if cycle:
        raise ApplicationError(
            "Sub-Skill dependencies form a cycle",
            [str(s) for s in cycle],
            type="DependencyCycle",
            non_retryable=True,
        )
    return BuildPlan(max_parallel=max_parallel, depends_on=edges)


def find_cycle(edges: Mapping[UUID, list[UUID]]) -> list[UUID]:
    """One cycle as a path (first id repeated at the end), or [] when the graph is acyclic."""
    state: dict[UUID, int] = {}  # 1 = on the current path, 2 = finished
    for root in sorted(edges, key=str):
        if root in state:
            continue
        path: list[UUID] = [root]
        stack = [iter(edges.get(root, []))]
        state[root] = 1
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                state[path.pop()] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                return [*path[path.index(nxt) :], nxt]
            elif nxt not in state:
                state[nxt] = 1
                path.append(nxt)
                stack.append(iter(edges.get(nxt, [])))
    return []


@activity_for(TaskQueue.BUILD)
@activity.defn(name="plan_builds")
async def plan_builds(inp: BuildPlanInput) -> BuildPlan:
    return make_plan(inp.sub_skill_ids, {}, BuildSettings().max_parallel)
