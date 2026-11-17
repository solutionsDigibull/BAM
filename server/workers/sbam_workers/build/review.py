"""The `review_code` build stage (#759, WG-11): one model review once every gate has passed.

The reviewer (`sbam-reviewer`, prompt `review-diff`) gets the Sub-Skill's spec and the unified diff
from the skeleton to the stored bundle, and answers a `ReviewAnswer`. The stage then behaves exactly
like a gate (E12.2): it records one `gate_results` row, gate `REVIEW`, keyed by the build stage key
and pointing at the code attempt and the bundle it judged, and a FAIL raises the non-retryable
`GateFailed` with the attempt id as details[0], so `SubSkillBuild` hands it to `correct`, which
reads the findings from the row. A PASS records its row (with any LOW or MEDIUM notes) and
returns the attempt id.

`Review` takes its dependencies as arguments, so tests run it with a fake `Reviewer`, a temp-dir
storage and SQLite; `wiring.py` builds the real ones. Nothing here calls a model except through the
`Reviewer` it is given, which in production is `LlmClient`: the budget is checked inside its `run`,
so a refused call surfaces here as `BudgetExceeded` and the workflow parks the build.

A retried activity finds the row it already wrote and answers from it (no second call). The replay
key of the call is the stage, never the Temporal attempt, so a crash between the answer and the row
finds the answer again in the call store. A bundle that fails its hashes is a platform defect, the
same errors as the gates.

The activity exists but no workflow calls it yet: the REVIEW stage is wired in a later change,
behind `workflow.patched`. Nothing here logs code, findings' text or the spec.
"""

import asyncio
import difflib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.storage.base import StorageBackend
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import Finding, GateName, GateStatus
from sbam_contracts.canonical import sha256_of
from sbam_contracts.pipeline import IdResult
from sbam_contracts.review import ReviewAnswer, ReviewVerdict
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from sbam_workers.build.codegen.render import RenderedSkeleton
from sbam_workers.build.gates.errors import GATE_INPUT_MISMATCH
from sbam_workers.build.gates.stored_bundle import (
    Verdict,
    fail,
    find_assembled,
    read_verified,
    record_verdicts,
)
from sbam_workers.llm.client import LlmResult
from sbam_workers.llm.store import LlmScope

STAGE: Final = "review_code"
ALIAS: Final = "sbam-reviewer"
PROMPT: Final = "review-diff"
TOOL_VERSION: Final = f"{ALIAS}:{PROMPT}"
CONFIG_SHA256: Final = sha256_of(
    ReviewAnswer.model_json_schema()
)  # the answer contract it judges by
DIFF_CONTEXT_LINES: Final = 6  # lines around each change, enough to read a condition in place
MAX_DIFF_CHARS: Final = 60_000
TRUNCATED: Final = "\n[diff truncated]\n"


class Reviewer(Protocol):
    """The part of `LlmClient` this stage uses (a fake in the tests)."""

    async def run(
        self,
        alias: str,
        prompt_id: str,
        output_type: type[ReviewAnswer],
        context: Mapping[str, str],
        scope: LlmScope,
        *,
        subject: Mapping[str, str] | None = None,
    ) -> LlmResult[ReviewAnswer]: ...


@dataclass(frozen=True)
class ReviewInputs:
    """What the review needs besides the stored bundle: the spec and the skeleton to diff with."""

    sub_skill: Mapping[str, Any]  # skill_metadata + isatvon_definition, as the corrections left it
    skeleton: RenderedSkeleton


def stage_key(inp: BuildStageInput) -> str:
    return f"{STAGE}:{inp.sub_skill_id}:{inp.build_no}:{inp.cycle}:{inp.attempt}"


def stage_scope(inp: BuildStageInput, temporal_workflow_id: str) -> LlmScope:
    """The replay key is the stage, not the Temporal attempt (`attempt` stays 1)."""
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=temporal_workflow_id,
        activity_id=stage_key(inp),
    )


def unified_diff(skeleton: Mapping[str, str], files: Mapping[str, str]) -> str:
    """The files that differ from the skeleton, as one unified diff in file-name order, capped."""
    parts: list[str] = []
    for name in sorted(files):
        before, after = skeleton.get(name, ""), files[name]
        if before == after:
            continue
        parts.extend(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                f"a/{name}",
                f"b/{name}",
                n=DIFF_CONTEXT_LINES,
                lineterm="",
            )
        )
    text = "\n".join(parts)
    if len(text) > MAX_DIFF_CHARS:
        return text[:MAX_DIFF_CHARS] + TRUNCATED
    return text


def review_context(
    sub_skill: Mapping[str, Any], skeleton: Mapping[str, str], files: Mapping[str, str]
) -> dict[str, str]:
    """The `<data>` blocks of the request."""
    return {
        "spec": json.dumps(sub_skill, indent=1, sort_keys=True, ensure_ascii=False),
        "diff": unified_diff(skeleton, files),
    }


def to_findings(answer: ReviewAnswer) -> list[dict[str, Any]]:
    """The answer's findings as `gate_results` keeps them: gate REVIEW, code `REVIEW_<category>`."""
    return [
        Finding(
            gate=GateName.REVIEW,
            code=f"REVIEW_{f.category.value}",
            message=f.message,
            file=f.file,
            line=f.line,
            expected=f.expected,
            severity=f.severity,
        ).model_dump(mode="json", exclude_none=True)
        for f in answer.findings
    ]


def review_failed(attempt_id: uuid.UUID) -> Exception:
    return fail("generated code failed the review", attempt_id, GATE_FAILED)


class Review:
    """`review_code` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        inputs: Callable[[BuildStageInput], Awaitable[ReviewInputs]],
        storage: StorageBackend,
        sessions: sessionmaker[Session],
        reviewer: Reviewer,
    ) -> None:
        self.inputs = inputs
        self.storage = storage
        self.sessions = sessions
        self.reviewer = reviewer

    def _recorded(self, inp: BuildStageInput) -> tuple[str, uuid.UUID | None, str | None] | None:
        """(status, code attempt, bundle sha) of the REVIEW row this stage key already has."""
        with self.sessions() as session:
            row = GateResultRepository(
                session, TenantContext(inp.tenant_id, inp.project_id)
            ).for_stage(
                inp.sub_skill_id, GateName.REVIEW.value, inp.build_no, inp.cycle, inp.attempt
            )
            return None if row is None else (row.status, row.code_attempt_id, row.bundle_sha256)

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        """Errors of the model side (`SchemaInvalidAfterRetries`, `LlmTransportError`,
        `BudgetExceeded`, ...) propagate before anything is written: a failed call leaves no row."""
        assembled = await asyncio.to_thread(find_assembled, self.sessions, inp)
        recorded = await asyncio.to_thread(self._recorded, inp)
        if recorded is not None:
            status, attempt_id, bundle_sha = recorded
            if attempt_id != assembled.attempt_id or bundle_sha != assembled.bundle_sha:
                message = "a recorded review is about another attempt or another bundle"
                raise fail(message, assembled.attempt_id, GATE_INPUT_MISMATCH)
            if status != GateStatus.PASSED.value:
                raise review_failed(assembled.attempt_id)
            return IdResult(id=assembled.attempt_id)
        bundle = await asyncio.to_thread(read_verified, self.storage, assembled)
        given = await self.inputs(inp)
        workflow_id = activity.info().workflow_id
        if workflow_id is None:  # the replay key needs the parent workflow
            raise RuntimeError(f"{STAGE} runs only inside a workflow")
        files = {
            name: data.decode("utf-8", errors="replace")
            for name, data in bundle.files.items()
            if name.endswith(".py")
        }
        result = await self.reviewer.run(
            ALIAS,
            PROMPT,
            ReviewAnswer,
            review_context(given.sub_skill, given.skeleton.files, files),
            stage_scope(inp, workflow_id),
            subject={"sub_skill_id": str(inp.sub_skill_id)},
        )
        answer = result.output
        failed = answer.verdict is ReviewVerdict.FAIL
        verdict = Verdict(
            GateName.REVIEW,
            GateStatus.FAILED if failed else GateStatus.PASSED,
            to_findings(answer),
            TOOL_VERSION,
            CONFIG_SHA256,
        )
        await asyncio.to_thread(record_verdicts, self.sessions, inp, bundle, [verdict])
        if failed:
            activity.logger.info(
                "generated code failed the review",
                extra={
                    "findings": len(answer.findings),
                    "tenant_id": str(inp.tenant_id),
                    "project_id": str(inp.project_id),
                    "workflow_id": str(inp.workflow_id),
                    "sub_skill_id": str(inp.sub_skill_id),
                    "correlation_id": inp.correlation_id,
                },
            )
            raise review_failed(bundle.attempt_id)
        return IdResult(id=bundle.attempt_id)
