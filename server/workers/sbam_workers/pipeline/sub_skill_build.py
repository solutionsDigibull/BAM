"""`SubSkillBuild` child workflow (TD §13.2): STUB for the ProjectPipeline skeleton (E10.1).

Started by ProjectPipeline with id `build-<sub_skill_id>-<build_no>` on sbam-build. #107 replaces
the body (render → bodies → gates → tests → correction ≤2, `retry` signal) and keeps the name and
the input/output types.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.pipeline import SUB_SKILL_BUILD, SubSkillBuildInput, SubSkillBuildResult
    from sbam_contracts.runs import TaskQueue

    from sbam_workers.build.stubs import build_sub_skill
    from sbam_workers.registry import workflow_for


@workflow_for(TaskQueue.BUILD)
@workflow.defn(name=SUB_SKILL_BUILD)
class SubSkillBuild:
    @workflow.run
    async def run(self, inp: SubSkillBuildInput) -> SubSkillBuildResult:
        result: SubSkillBuildResult = await workflow.execute_activity(
            build_sub_skill,
            inp,
            task_queue=TaskQueue.BUILD.value,
            start_to_close_timeout=timedelta(minutes=20),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return result
