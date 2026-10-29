"""Real Temporal dev server (real timers): a knowledge activity that hangs without heartbeating."""
import asyncio, time, uuid
from sbam_contracts.pipeline import IdResult, PipelineStage, RunInput
from sbam_api.pipeline.service import pipeline_status
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from test_project_pipeline import (  # type: ignore[import-not-found]
    DEFAULT_OVERRIDES, PROJECT_ROW, TENANT, actions, mirror_row, new_project, registry, run, start, state_of, workers,
)

calls: list[float] = []


@activity.defn(name="build_knowledge")
async def hung(inp: RunInput) -> IdResult:
    calls.append(time.time())
    await asyncio.sleep(100_000)  # never heartbeats
    return IdResult(id=uuid.uuid4())


def test_audit_real_hung_knowledge_activity() -> None:
    holder: dict = {}

    async def scenario(sessions: sessionmaker[Session]) -> None:
        holder["s"] = sessions
        registry.load_all()
        env = await WorkflowEnvironment.start_local(port=57234, ui=False, data_converter=pydantic_data_converter)
        try:
            async with workers(env.client, {**DEFAULT_OVERRIDES, "build_knowledge": hung}, set()):
                project = new_project(sessions, TENANT); holder["p"] = project
                handle = await start(env, sessions, project)
                t0 = time.time()
                for i in range(1400):  # up to ~23 minutes
                    await asyncio.sleep(1)
                    d = await handle.describe()
                    if d.status != 1:
                        break
                st = await pipeline_status(env.client, project)
                print(f"RESULT elapsed={time.time()-t0:.0f}s attempts={len(calls)} gaps={[round(b-a) for a,b in zip(calls,calls[1:])]} temporal_status={d.status} stage={st.stage if st else None} last_error={getattr(st,'last_error',None)!r}", flush=True)
        finally:
            await env.shutdown()

    run(scenario)
    s, p = holder["s"], holder["p"]
    print("RESULT project_state=", state_of(s, PROJECT_ROW, p), "actions=", actions(s, "project", p), "mirror=", (mirror_row(s, p).state, mirror_row(s, p).current_stage), flush=True)
