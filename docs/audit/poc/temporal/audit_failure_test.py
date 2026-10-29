"""Audit failure-injection tests for ProjectPipeline (not part of the repo suite; see docs/audit/poc/)."""
import asyncio
import uuid
from typing import Any

from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import IdResult, PipelineStage, RebuildSubSkills
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from test_project_pipeline import (  # type: ignore[import-not-found]
    FDE, TENANT, approve, environment, finish, generate, mirror_row, new_project, run, start, until,
)


def _flaky(attempts: dict[uuid.UUID, int], fail_first: int = 1) -> Any:
    @activity.defn(name="render_skeleton")
    async def flaky(inp: BuildStageInput) -> IdResult:
        attempts[inp.sub_skill_id] = attempts.get(inp.sub_skill_id, 0) + 1
        if len(attempts) <= fail_first and attempts[inp.sub_skill_id] == 1:
            raise ApplicationError("template broke", type="TemplateError", non_retryable=True)
        return IdResult(id=uuid.uuid4())
    return flaky


async def _wait_new_run(env: Any, handle: Any, first_run: str) -> str:
    for _ in range(400):
        d = await env.client.get_workflow_handle(handle.id).describe()
        if d.run_id != first_run:
            return d.run_id
        await asyncio.sleep(0.05)
    return first_run


def test_audit_t1_continue_as_new_in_wait_rebuild_then_rebuild_signal() -> None:
    attempts: dict[uuid.UUID, int] = {}

    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = new_project(sessions, TENANT)
        async with environment({"render_skeleton": _flaky(attempts)}) as env:
            handle = await start(env, sessions, project, continue_as_new_after=60)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            await until(handle, PipelineStage.WAIT_REBUILD)
            first = mirror_row(sessions, project).temporal_run_id
            d0 = await env.client.get_workflow_handle(handle.id).describe()
            # force enough history to make the wait continue as new
            for i in range(80):
                await generate(handle)  # same key as the first generate: ignored, but it grows history
            from datetime import timedelta
            await env.sleep(timedelta(days=4))
            await asyncio.sleep(1)
            d1 = await env.client.get_workflow_handle(handle.id).describe()
            print("run changed (continued as new):", d1.run_id != d0.run_id); assert d1.run_id != d0.run_id
            failed = [s for s, n in attempts.items() if n == 1][:1]
            await handle.signal("rebuild_subskills", RebuildSubSkills(sub_skill_ids=failed, idempotency_key="rb-real", actor_id=FDE))
            status = await finish(handle)
            print("final stage:", status.stage)
            assert status.stage is PipelineStage.DONE

    run(scenario)


def test_audit_t2_cancel_after_continue_as_new_in_wait_generate() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            handle = await start(env, sessions, project, continue_as_new_after=20)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            first = mirror_row(sessions, project).temporal_run_id
            await _wait_new_run(env, handle, first)
            from sbam_contracts.pipeline import Cancel
            await handle.signal("cancel", Cancel(reason="audit", actor_id=FDE))
            status = await finish(handle)
            print("after cancel on the continued run:", status.stage)
            assert status.stage is PipelineStage.CANCELLED

    run(scenario)


def test_audit_t3_generate_signal_sent_twice_with_different_keys_builds_once_per_sub_skill() -> None:
    attempts: dict[uuid.UUID, int] = {}

    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = new_project(sessions, TENANT)

        @activity.defn(name="render_skeleton")
        async def count(inp: BuildStageInput) -> IdResult:
            attempts[inp.sub_skill_id] = attempts.get(inp.sub_skill_id, 0) + 1
            return IdResult(id=uuid.uuid4())

        async with environment({"render_skeleton": count}) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle, key="g-1")
            await generate(handle, key="g-2")
            status = await finish(handle)
            assert status.stage is PipelineStage.DONE
            print("builds per sub-skill:", sorted(attempts.values()))
            assert all(n == 1 for n in attempts.values())

    run(scenario)


from sbam_contracts.pipeline import PipelineStatus, RunInput  # noqa: E402
from test_project_pipeline import PROJECT_ROW, actions, state_of  # type: ignore[import-not-found]  # noqa: E402


def _ks(behaviour: str, calls: list[int]) -> Any:
    @activity.defn(name="build_knowledge")
    async def knowledge(inp: RunInput) -> IdResult:
        calls.append(1)
        if behaviour == "hang":
            await asyncio.sleep(10_000)  # never heartbeats
        if behaviour == "flaky" and len(calls) < 4:
            raise RuntimeError("cognee connection reset")  # transient: retryable
        if behaviour == "always":
            raise RuntimeError("cognee down")
        return IdResult(id=uuid.uuid4())
    return knowledge


def _run_ks(behaviour: str) -> tuple[PipelineStatus, int, str, list[str], Any]:
    calls: list[int] = []
    holder: dict[str, Any] = {}

    async def scenario(sessions: sessionmaker[Session]) -> tuple[PipelineStatus, str]:
        holder["s"] = sessions
        project = new_project(sessions, TENANT); holder["p"] = project
        async with environment({"build_knowledge": _ks(behaviour, calls)}) as env:
            handle = await start(env, sessions, project)
            from datetime import timedelta
            from sbam_api.pipeline.service import pipeline_status
            st = None
            for _ in range(200):  # virtual 10 minutes per step, 33 hours total
                await env.sleep(timedelta(minutes=10)); await asyncio.sleep(0.05)
                d = await handle.describe()
                if d.status != 1:
                    break
            st = await pipeline_status(env.client, project)
            if st is None or d.status == 1:
                st = st or PipelineStatus(stage=PipelineStage.FAILED)
            return st, f"temporal status={d.status}"

    st, err = run(scenario)
    s = holder["s"]; p = holder["p"]
    return st, len(calls), err, actions(s, "project", p), (state_of(s, PROJECT_ROW, p), mirror_row(s, p))


def test_audit_t4_knowledge_activity_hangs_forever() -> None:
    st, n, err, acts, (pstate, mrow) = _run_ks("hang")
    print(f"hang: stage={st.stage} attempts={n} err={err!r} last_error={getattr(st,'last_error',None)!r} project_state={pstate} actions={acts} mirror=({mrow.state},{mrow.current_stage})")
    assert st.stage in (PipelineStage.FAILED,)


def test_audit_t5_knowledge_transient_failures_recover() -> None:
    st, n, err, acts, (pstate, mrow) = _run_ks("flaky")
    print(f"flaky: stage={st.stage} attempts={n} project_state={pstate} mirror=({mrow.state},{mrow.current_stage})")
    assert n == 4 and st.stage is not PipelineStage.FAILED


def test_audit_t6_knowledge_retries_exhausted() -> None:
    st, n, err, acts, (pstate, mrow) = _run_ks("always")
    print("ERR:", err); print(f"always: stage={st.stage} failed_stage={st.failed_stage} attempts={n} last_error={st.last_error!r} project_state={pstate} actions={acts} mirror=({mrow.state},{mrow.current_stage})")
    assert st.stage is PipelineStage.FAILED
