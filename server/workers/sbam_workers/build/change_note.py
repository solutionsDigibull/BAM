"""The `apply_change_note` activity (#767, WG-19): one FDE note about a Sub-Skill's generated code.

The note is the FDE's own words and reaches the model only as the `note` `<data>` block of the
classifier request (`sbam-corrector`, prompt `change-note`), next to the Sub-Skill's spec and its
current bodies. Nothing the note says is read as an instruction, and no gate ever reads it: the
gates run on the code and the spec as they always do, so a note that tells the model to relax a
gate or a platform rule has nothing to act on.

The classifier answers `ChangeNoteAnswer`:

* CODE: the Sub-Skill means the same and its code is wrong. The outcome is REBUILDING; the
  workflow then sends the build the `retry` it waits for, and the coder writes a new generated
  version with the note as a data block (`params.CHANGE_NOTES_PARAM`), through every gate, test
  and the review again.
* MEANING: what the Sub-Skill means changes. The proposed RFC 6902 patch is checked with the
  corrector's own `apply_isatvon_patch` (only `/isatvon_definition` is patchable, the result must
  still validate and compile) and is written as the Sub-Skill's `isatvon_corrected` audit row,
  class BEHAVIOUR, which is exactly what the held-patch endpoints read: the FDE accepts or rejects
  it there, and accepting retries the build. A patch that does not apply parks the note.

The model call goes through `LlmClient`, whose budget check refuses it with `BudgetExceeded`: the
note is then PARKED with the reason, nothing else changes, and the FDE sees why. A retried
activity finds the outcome it already wrote and answers from it (the replay key of the call is the
note, so a crash between the answer and the outcome finds the answer again).

`ApplyChangeNote` takes its dependencies as arguments (tests run it with a fake classifier and
SQLite); the registered activity at the bottom builds the real ones on first use.
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final, Protocol

from sbam_api.audit.repository import find_by_key, record
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.request_change import notes
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.build import IsatvonPatchClass
from sbam_contracts.canonical import sha256_of
from sbam_contracts.request_change import (
    ChangeNote,
    ChangeNoteAnswer,
    ChangeNoteOutcome,
    ChangeNoteStatus,
    RequestChangeInput,
    RequestChangeResult,
)
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import SubSkillState
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.codegen.correction import (
    PATCH_ROOT,
    PatchOp,
    apply_isatvon_patch,
    classify_patch,
)
from sbam_workers.llm.budget import BudgetExceeded
from sbam_workers.llm.client import LlmClient, LlmResult
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

STAGE: Final = "apply_change_note"
ALIAS: Final = "sbam-corrector"
PROMPT: Final = "change-note"
CORRECTED: Final = "isatvon_corrected"  # the held-patch flow reads this row (`patch_decision`)
HELD_KEY: Final = "request-change:"
FAILED_GATE: Final = "REQUEST_CHANGE"  # shown as the gate the patch answers
NOT_FOUND: Final = "CHANGE_NOTE_NOT_FOUND"
BUDGET_REASON: Final = "The token budget was reached; raise it, then send the note again."
PATCH_REASON: Final = (
    "The proposed ISATVON patch could not be applied; change the Sub-Skill in the ISATVON Grid."
)
STATE_REASON: Final = "The Sub-Skill was no longer waiting for review; the note is kept."


class Classifier(Protocol):
    """The part of `LlmClient` this activity uses (a fake in the tests)."""

    async def run(
        self,
        alias: str,
        prompt_id: str,
        output_type: type[ChangeNoteAnswer],
        context: Mapping[str, str],
        scope: LlmScope,
        *,
        subject: Mapping[str, str] | None = None,
    ) -> LlmResult[ChangeNoteAnswer]: ...


@dataclass(frozen=True)
class ChangeSubject:
    """What the classifier reads besides the note: the Sub-Skill and its current slot bodies."""

    sub_skill: Mapping[str, Any]  # skill_metadata + isatvon_definition
    bodies: Mapping[str, Any]  # the newest code attempt's bodies ({} before the first)
    state: str


def change_context(subject: ChangeSubject, note: str) -> dict[str, str]:
    """The `<data>` blocks of the request. The note is one of them and nothing else."""
    return {
        "spec": json.dumps(subject.sub_skill, indent=1, sort_keys=True, ensure_ascii=False),
        "current_bodies": json.dumps(subject.bodies, indent=1, sort_keys=True, ensure_ascii=False),
        "note": note,
    }


def stage_scope(inp: RequestChangeInput, temporal_workflow_id: str) -> LlmScope:
    """The replay key is the note: a retry after the model answered finds the same answer."""
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=temporal_workflow_id,
        activity_id=f"{STAGE}:{inp.note_id}",
    )


class SqlChangeSubjects:
    """Loads the note and the Sub-Skill (tenant- and project-scoped)."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def note(self, inp: RequestChangeInput) -> ChangeNote:
        with self.sessions() as session:
            ctx = TenantContext(inp.tenant_id, inp.project_id)
            workflow = WorkflowRepository(session, ctx).get(inp.workflow_id)
            found = (
                None
                if workflow is None
                else notes.get_note(
                    session, inp.tenant_id, inp.workflow_id, workflow.version, inp.note_id
                )
            )
            if found is None or found.sub_skill_id != inp.sub_skill_id:
                raise ApplicationError(
                    f"no change note {inp.note_id} for this Sub-Skill",
                    type=NOT_FOUND,
                    non_retryable=True,
                )
            return found

    def subject(self, inp: RequestChangeInput) -> ChangeSubject:
        with self.sessions() as session:
            ctx = TenantContext(inp.tenant_id, inp.project_id)
            sub: SubSkill | None = SubSkillRepository(session, ctx).get(inp.sub_skill_id)
            if sub is None or sub.workflow_id != inp.workflow_id:
                raise ApplicationError(
                    f"Sub-Skill {inp.sub_skill_id} is not in this run",
                    type=NOT_FOUND,
                    non_retryable=True,
                )
            attempts = CodeAttemptRepository(session, ctx).of_sub_skill(
                inp.workflow_id, inp.sub_skill_id
            )
            bodies = dict(attempts[-1].bodies) if attempts else {}
            return ChangeSubject(dict(sub.isatvon), bodies, str(sub.state))

    def settle(
        self,
        inp: RequestChangeInput,
        status: ChangeNoteStatus,
        reason: str | None,
        held: tuple[Mapping[str, Any], dict[str, Any]] | None = None,
    ) -> ChangeNoteStatus:
        """The outcome row and, for a held patch, the `isatvon_corrected` row: one transaction."""
        with self.sessions() as session:
            if held is not None:
                before, after = held
                key = HELD_KEY + str(inp.note_id)
                if (
                    find_by_key(session, inp.tenant_id, "sub_skill", str(inp.sub_skill_id), key)
                    is None
                ):
                    record(
                        session,
                        tenant_id=inp.tenant_id,
                        actor_id=ALIAS,
                        action=CORRECTED,
                        entity_type="sub_skill",
                        entity_id=str(inp.sub_skill_id),
                        correlation_id=inp.correlation_id,
                        before=dict(before),
                        after=after,
                        idempotency_key=key,
                    )
            recorded = notes.record_outcome(
                session,
                tenant_id=inp.tenant_id,
                workflow_id=inp.workflow_id,
                note_id=inp.note_id,
                status=status,
                reason=reason,
                correlation_id=inp.correlation_id,
            )
            session.commit()
            return recorded


def held_rows(
    subject: ChangeSubject, ops: list[PatchOp], patched: Mapping[str, Any], answer: ChangeNoteAnswer
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The `isatvon_corrected` before/after the held-patch endpoints read. A meaning change is
    BEHAVIOUR whatever the paths look like: the FDE always decides it."""
    _, paths = classify_patch(ops)
    before = {
        "sub_skill": dict(subject.sub_skill),
        "isatvon_sha256": sha256_of(subject.sub_skill[PATCH_ROOT]),
    }
    after = {
        "sub_skill": dict(patched),
        "isatvon_sha256": sha256_of(patched[PATCH_ROOT]),
        "patch": [op.model_dump() for op in ops],
        "patch_class": IsatvonPatchClass.BEHAVIOUR.value,
        "changed_paths": list(paths),
        "rationale": answer.rationale,
        "failed_gate": FAILED_GATE,
        "attempt": 1,
    }
    return before, after


class ApplyChangeNote:
    """`apply_change_note` with its dependencies injected (see the module docstring)."""

    def __init__(self, store: SqlChangeSubjects, classifier: Classifier) -> None:
        self.store = store
        self.classifier = classifier

    @activity.defn(name=STAGE)
    async def run(self, inp: RequestChangeInput) -> RequestChangeResult:
        note = await asyncio.to_thread(self.store.note, inp)
        if note.status is not ChangeNoteStatus.PROCESSING:  # a retried activity: already settled
            return RequestChangeResult(note_id=inp.note_id, status=note.status)
        subject = await asyncio.to_thread(self.store.subject, inp)
        if subject.state != SubSkillState.NEEDS_FDE_REVIEW.value:
            return await self._settle(inp, ChangeNoteStatus.QUEUED, STATE_REASON)
        workflow_id = activity.info().workflow_id
        if workflow_id is None:  # the replay key needs the parent workflow
            raise RuntimeError(f"{STAGE} runs only inside a workflow")
        try:
            result = await self.classifier.run(
                ALIAS,
                PROMPT,
                ChangeNoteAnswer,
                change_context(subject, note.note),
                stage_scope(inp, workflow_id),
                subject={"sub_skill_id": str(inp.sub_skill_id)},
            )
        except BudgetExceeded:
            return await self._settle(inp, ChangeNoteStatus.PARKED, BUDGET_REASON)
        answer = result.output
        if answer.outcome is ChangeNoteOutcome.CODE:
            return await self._settle(inp, ChangeNoteStatus.REBUILDING, answer.rationale)
        return await self._held(inp, subject, answer)

    async def _settle(
        self,
        inp: RequestChangeInput,
        status: ChangeNoteStatus,
        reason: str,
        held: tuple[Mapping[str, Any], dict[str, Any]] | None = None,
    ) -> RequestChangeResult:
        recorded = await asyncio.to_thread(self.store.settle, inp, status, reason, held)
        activity.logger.info(
            "change note settled",
            extra={
                "status": recorded.value,
                "tenant_id": str(inp.tenant_id),
                "project_id": str(inp.project_id),
                "workflow_id": str(inp.workflow_id),
                "sub_skill_id": str(inp.sub_skill_id),
                "correlation_id": inp.correlation_id,
            },
        )
        return RequestChangeResult(note_id=inp.note_id, status=recorded)

    async def _held(
        self, inp: RequestChangeInput, subject: ChangeSubject, answer: ChangeNoteAnswer
    ) -> RequestChangeResult:
        ops = [PatchOp.model_validate(op.model_dump()) for op in answer.isatvon_patch or []]
        patched, _problems = apply_isatvon_patch(subject.sub_skill, ops) if ops else (None, [])
        if patched is None:
            return await self._settle(inp, ChangeNoteStatus.PARKED, PATCH_REASON)
        held = held_rows(subject, ops, patched, answer)
        return await self._settle(inp, ChangeNoteStatus.HELD_PATCH, answer.rationale, held)


@lru_cache(maxsize=1)
def _apply() -> ApplyChangeNote:
    settings, sessions = WorkerSettings(), mirror.sessions()  # type: ignore[call-arg]
    client = LlmClient(settings, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(settings))
    return ApplyChangeNote(SqlChangeSubjects(sessions), client)


@activity_for(TaskQueue.LLM)
@activity.defn(name=STAGE)
async def apply_change_note_activity(inp: RequestChangeInput) -> RequestChangeResult:
    """Classify one change note and hand it on: a held ISATVON patch, a rebuild, or parked."""
    return await _apply().run(inp)
