"""SOW generation stage (owner: E7.x).

STUBS for the ProjectPipeline skeleton (E10.1): fixed activity names and Pydantic in/out
(sbam_contracts.pipeline). The owning issue replaces the bodies and keeps names and types.
"""

from sbam_contracts.pipeline import IdResult, StageRef
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stub_id


@activity_for(TaskQueue.LLM)
@activity.defn(name="generate_sow")
async def generate_sow(inp: StageRef) -> IdResult:
    """Real: SOW from the OKF bundle (`ref_id`) via sbam-sow; returns the SOW version id.
    Non-retryable failure type: SchemaInvalidAfterRetries."""
    return IdResult(id=stub_id(inp, "sow"))
