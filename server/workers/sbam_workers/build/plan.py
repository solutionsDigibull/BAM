"""Build plan (E10.3): the parallel limit and the Sub-Skill dependency graph for one generate.

`ProjectPipeline` calls `plan_builds` once per generate and keeps the result in workflow state, so
changing `BUILD_MAX_PARALLEL` mid-run applies only to the next generate. The graph is validated
here: an edge to an unknown Sub-Skill is `UnknownSubSkill`, a cycle is `DependencyCycle`, both
non-retryable.

The edges are the Workflow DAG's data links (E9.5, `dag.data_links`): a Sub-Skill waits for the
Sub-Skills whose outputs it takes as inputs.
"""

import asyncio
from collections.abc import Iterable, Mapping
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_contracts.pipeline import (
    MAX_PARALLEL_BUILDS,
    MAX_PARALLEL_LIMIT,
    BuildPlan,
    BuildPlanInput,
)
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.dag import Node, data_links, find_cycle
from sbam_workers.registry import activity_for

__all__ = ["BuildSettings", "find_cycle", "make_plan", "plan_builds"]


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


@activity_for(TaskQueue.BUILD)
@activity.defn(name="plan_builds")
async def plan_builds(inp: BuildPlanInput) -> BuildPlan:
    edges = await asyncio.to_thread(depends_on, mirror.sessions(), inp)
    return make_plan(inp.sub_skill_ids, edges, BuildSettings().max_parallel)


def depends_on(sessions: sessionmaker[Session], inp: BuildPlanInput) -> dict[UUID, list[UUID]]:
    """Each Sub-Skill's upstreams, from the stored ISATVON of the Workflow (tenant-scoped)."""
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with sessions() as session:
        macros = MacroSkillRepository(session, ctx).of_workflow(inp.workflow_id)
        category = {m.id: m.macro_category for m in macros}
        nodes = [
            Node(s.id, s.skill_id, category.get(s.macro_skill_id, ""), s.isatvon)
            for s in SubSkillRepository(session, ctx).of_workflow(inp.workflow_id)
        ]
    planned = set(inp.sub_skill_ids)  # only the run's Sub-Skills are scheduled
    edges: dict[UUID, list[UUID]] = {}
    for up, down, _ in data_links([n for n in nodes if n.sub_skill_id in planned]):
        ups = edges.setdefault(down.sub_skill_id, [])
        if up.sub_skill_id not in ups:
            ups.append(up.sub_skill_id)
    return edges
