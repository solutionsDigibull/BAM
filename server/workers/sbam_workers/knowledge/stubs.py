"""Knowledge and OKF stages (owners: E5.x, E6.x). `build_knowledge` is real (E5.2, `build.py`).

STUBS for the ProjectPipeline skeleton (E10.1): fixed activity names and Pydantic in/out
(sbam_contracts.pipeline). The owning issue replaces the bodies and keeps names and types.
"""

from sbam_contracts.pipeline import IdResult, StageRef
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stub_id


@activity_for(TaskQueue.KNOWLEDGE)
@activity.defn(name="build_okf_bundle")
async def build_okf_bundle(inp: StageRef) -> IdResult:
    """Real: OKF bundle from the knowledge version (`ref_id`); returns the bundle id."""
    return IdResult(id=stub_id(inp, "okf"))
