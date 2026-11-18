"""Build stages (owners: E11.x, #112 templates, E12 gates, #107 the SubSkillBuild flow).

STUBS: fixed activity names and Pydantic in/out (sbam_contracts.pipeline / .build). `SubSkillBuild`
(#107) runs them in the TD §13.2 order; the owning issue replaces a body and keeps names and types.
Every real stage upserts by (sub_skill_id, build_no, cycle, attempt, stage), so a retried activity
never writes a second row (TD §13.4). Gates raise `ApplicationError(type=GateFailed,
non_retryable=True)` with the persisted finding ids as details.
"""

from sbam_contracts.build import (
    BuildStageInput,
    CorrectionInput,
    CorrectionResult,
)
from sbam_contracts.pipeline import IdResult
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stage_stub_id, stub_activity


@activity_for(TaskQueue.BUILD)
@activity.defn(name="render_skeleton")
@stub_activity
async def render_skeleton(inp: BuildStageInput) -> IdResult:
    """Real (#112 templates): non-retryable TemplateError."""
    return IdResult(id=stage_stub_id(inp, "skeleton"))


@activity.defn(name="generate_bodies")  # registered by `wiring.py`; the workflow's typed handle
async def generate_bodies(inp: BuildStageInput) -> IdResult:
    """Real: LLM fills the function bodies through the Bifrost `sbam-codegen` alias."""
    return IdResult(id=stage_stub_id(inp, "bodies"))


@activity.defn(name="assemble")  # registered by `wiring.py`; the workflow's typed handle
async def assemble(inp: BuildStageInput) -> IdResult:
    """Real: skeleton + bodies -> the Sub-Skill's code; non-retryable TemplateError."""
    return IdResult(id=stage_stub_id(inp, "assembled"))


@activity.defn(name="ast_gate")  # registered by `wiring.py`; the workflow's typed handle
async def ast_gate(inp: BuildStageInput) -> IdResult:
    """Real (E11/E12): AST checks; raises GateFailed with finding ids."""
    return IdResult(id=stage_stub_id(inp, "ast_gate"))


@activity.defn(name="static_gate")  # registered by `wiring.py`; the workflow's typed handle
async def static_gate(inp: BuildStageInput) -> IdResult:
    """Real (E12.4): Ruff, Black, MyPy, Bandit, ClamAV; raises GateFailed with the attempt id."""
    return IdResult(id=stage_stub_id(inp, "static_gate"))


@activity.defn(name="import_gate")  # registered by `wiring.py`; the workflow's typed handle
async def import_gate(inp: BuildStageInput) -> IdResult:
    """Real (#761): every module imports with no side effect, in a sandbox VM of the pinned runtime;
    raises GateFailed with the attempt id, the verdict is in `gate_results`."""
    return IdResult(id=stage_stub_id(inp, "import_gate"))


@activity.defn(name="compliance_gates")  # registered by `wiring.py`; the workflow's typed handle
async def compliance_gates(inp: BuildStageInput) -> IdResult:
    """Real (#761): the REPO_LICENCE, DEPENDENCY_VULN and MCP_COMPLIANCE rows of the bundle;
    raises GateFailed with the attempt id when one fails."""
    return IdResult(id=stage_stub_id(inp, "compliance_gates"))


@activity.defn(name="review_code")  # registered by `wiring.py`; the workflow's typed handle
async def review_code(inp: BuildStageInput) -> IdResult:
    """Real (#759): one model review of the stored bundle after the AST and static gates passed;
    a FAIL raises GateFailed with the attempt id (the verdict is in `gate_results`)."""
    return IdResult(id=stage_stub_id(inp, "review"))


@activity.defn(name="correct")  # registered by `wiring.py`; the workflow's typed handle
async def correct(inp: CorrectionInput) -> CorrectionResult:
    """Real: LLM (`sbam-corrector`) rewrites the failing bodies from the findings; stores them
    as the next attempt's bodies and returns their id and what it did to the ISATVON."""
    return CorrectionResult(id=stage_stub_id(inp, "correction"))
