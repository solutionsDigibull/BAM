"""ISATVON pass 2, Sub-Skills (owner: E8.3). Pass 1 is real: `sbam_workers.isatvon.macros`.

STUBS for the ProjectPipeline skeleton (E10.1): fixed activity names and Pydantic in/out
(sbam_contracts.pipeline). The owning issue replaces the bodies and keeps names and types.
"""

from sbam_contracts.pipeline import IdsResult, MacroRef
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stub_id


@activity_for(TaskQueue.LLM)
@activity.defn(name="extract_sub_skills")
async def extract_sub_skills(inp: MacroRef) -> IdsResult:
    """Real: Sub-Skills (ISATVON 1.4) of one Macro Skill (pass 2); runs in parallel per macro."""
    return IdsResult(ids=[stub_id(inp, f"sub:{inp.macro_skill_id}:{n}") for n in (1, 2)])
