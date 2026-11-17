"""#759 stage tests: `review_code` against SQLite and a disk storage backend, with a fake reviewer.

`assemble` leaves the stored bundle; the review is then run on it. No model is ever called: the
reviewer is a fake that returns a canned `ReviewAnswer` or raises what `LlmClient` would raise.
"""

import asyncio
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sbam_api.manifests.models import GateResultRow
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.review import ReviewAnswer
from sbam_workers.build.codegen import LoadedTemplate, load_template
from sbam_workers.build.gates.errors import GATE_INPUT_MISMATCH
from sbam_workers.build.review import (
    ALIAS,
    PROMPT,
    Review,
    ReviewInputs,
    review_context,
    stage_scope,
    to_findings,
    unified_diff,
)
from sbam_workers.llm.budget import BudgetExceeded
from sbam_workers.llm.client import LlmResult, LlmTransportError
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.store import LlmScope
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_codegen_assemble import ctx, stage_input
from test_e12_2_ast_gate_stage import assembled, db, given

HIGH = {
    "category": "WRONG_OUTPUT",
    "severity": "HIGH",
    "message": "auth_status answers DENIED; the spec allows AUTHENTICATED, REJECTED or LOCKED.",
    "file": "action_tool.py",
    "line": 35,
    "expected": "REJECTED",
}
LOW = {"category": "OTHER", "severity": "LOW", "message": "A comment could be clearer."}


def answer(verdict: str, *findings: Mapping[str, Any]) -> ReviewAnswer:
    return ReviewAnswer.model_validate({"verdict": verdict, "findings": list(findings)})


class FakeReviewer:
    """Returns `reply`, or raises it if it is an exception; remembers every call."""

    def __init__(self, reply: ReviewAnswer | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        alias: str,
        prompt_id: str,
        output_type: type[ReviewAnswer],
        context: Mapping[str, str],
        scope: LlmScope,
        *,
        subject: Mapping[str, str] | None = None,
    ) -> LlmResult[ReviewAnswer]:
        self.calls.append(
            {
                "alias": alias,
                "prompt": prompt_id,
                "output_type": output_type,
                "context": dict(context),
                "scope": scope,
                "subject": dict(subject or {}),
            }
        )
        if isinstance(self.reply, Exception):
            raise self.reply
        return LlmResult(self.reply, uuid.uuid4(), "trace-1", attempts=1)


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.1")


def stage(
    template: LoadedTemplate,
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    reviewer: FakeReviewer,
) -> Review:
    async def inputs(_: BuildStageInput) -> ReviewInputs:
        base = given(template)
        sub_skill = {"skill_metadata": {"skill_id": "MS01.1"}, "isatvon_definition": {"x": 1}}
        return ReviewInputs(sub_skill, base.skeleton)

    return Review(inputs, storage, sessions, reviewer)


def run(review: Review, inp: BuildStageInput) -> uuid.UUID:
    with_workflow = ActivityEnvironment()
    return asyncio.run(with_workflow.run(review.run, inp)).id


def rows(sessions: sessionmaker[Session], inp: BuildStageInput) -> list[GateResultRow]:
    with sessions() as session:
        found = list(GateResultRepository(session, ctx(inp)).list())
        session.expunge_all()
        return found


def world(
    template: LoadedTemplate, tmp_path: Path, reply: ReviewAnswer | Exception
) -> tuple[sessionmaker[Session], LocalDiskBackend, BuildStageInput, uuid.UUID, FakeReviewer]:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)
    return sessions, storage, inp, attempt_id, FakeReviewer(reply)


# --- a pass -------------------------------------------------------------------------------------


def test_wg11_a_pass_records_a_passed_review_row_and_returns_the_attempt(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp, attempt_id, reviewer = world(template, tmp_path, answer("PASS"))
    assert run(stage(template, sessions, storage, reviewer), inp) == attempt_id
    [row] = rows(sessions, inp)
    assert (row.gate, row.status, row.findings) == (GateName.REVIEW.value, "PASSED", [])
    assert row.code_attempt_id == attempt_id and row.bundle_sha256
    assert (row.sub_skill_id, row.build_no, row.cycle, row.attempt) == (
        inp.sub_skill_id,
        inp.build_no,
        inp.cycle,
        inp.attempt,
    )


def test_wg11_a_pass_with_low_notes_keeps_them_and_still_passes(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp, attempt_id, reviewer = world(template, tmp_path, answer("PASS", LOW))
    assert run(stage(template, sessions, storage, reviewer), inp) == attempt_id
    [row] = rows(sessions, inp)
    assert row.status == "PASSED"
    assert [f["code"] for f in row.findings] == ["REVIEW_OTHER"]


def test_wg11_the_reviewer_gets_the_alias_the_prompt_and_the_data_blocks(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp, _, reviewer = world(template, tmp_path, answer("PASS"))
    run(stage(template, sessions, storage, reviewer), inp)
    [call] = reviewer.calls
    assert (call["alias"], call["prompt"], call["output_type"]) == (ALIAS, PROMPT, ReviewAnswer)
    assert ALIAS == "sbam-reviewer" and ALIAS not in {"sbam-codegen", "sbam-corrector"}
    assert set(call["context"]) == {"spec", "diff"}  # data blocks, never prompt text
    assert '"skill_id": "MS01.1"' in call["context"]["spec"]
    assert "+++ b/action_tool.py" in call["context"]["diff"]
    assert call["subject"] == {"sub_skill_id": str(inp.sub_skill_id)}


def test_wg11_the_replay_key_is_the_stage_not_the_temporal_attempt(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp, _, reviewer = world(template, tmp_path, answer("PASS"))
    run(stage(template, sessions, storage, reviewer), inp)
    scope = reviewer.calls[0]["scope"]
    assert scope == stage_scope(inp, scope.temporal_workflow_id)
    assert scope.activity_id == (
        f"review_code:{inp.sub_skill_id}:{inp.build_no}:{inp.cycle}:{inp.attempt}"
    )
    assert scope.attempt == 1
    other = stage_scope(stage_input(attempt=1, sub_skill_id=inp.sub_skill_id), "wf")
    assert other.activity_id != scope.activity_id  # another attempt is another call


# --- a failure ----------------------------------------------------------------------------------


def test_wg11_a_fail_raises_gate_failed_with_the_attempt_and_keeps_the_findings(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp, attempt_id, reviewer = world(
        template, tmp_path, answer("FAIL", HIGH, LOW)
    )
    with pytest.raises(ApplicationError) as caught:
        run(stage(template, sessions, storage, reviewer), inp)
    assert caught.value.type == GATE_FAILED and caught.value.non_retryable
    assert caught.value.details[0] == [str(attempt_id)]  # exactly the shape every gate raises
    [row] = rows(sessions, inp)
    assert (row.gate, row.status) == ("REVIEW", "FAILED")
    first = row.findings[0]
    assert first["gate"] == "REVIEW" and first["code"] == "REVIEW_WRONG_OUTPUT"
    assert (first["file"], first["line"], first["severity"]) == ("action_tool.py", 35, "HIGH")
    assert row.findings[1]["code"] == "REVIEW_OTHER"


def test_wg11_the_corrector_reads_a_failed_review_like_any_gate(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    """`correct` takes the findings of a pass from `failed_for_pass`; the REVIEW row is there."""
    sessions, storage, inp, _, reviewer = world(template, tmp_path, answer("FAIL", HIGH))
    with pytest.raises(ApplicationError):
        run(stage(template, sessions, storage, reviewer), inp)
    with sessions() as session:
        failed = GateResultRepository(session, ctx(inp)).failed_for_pass(
            inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
        )
        assert [r.gate for r in failed] == ["REVIEW"]
        assert failed[0].findings[0]["message"].startswith("auth_status answers DENIED")


def test_wg11_a_retried_stage_answers_from_its_row_without_asking_again(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp, attempt_id, reviewer = world(template, tmp_path, answer("PASS"))
    review = stage(template, sessions, storage, reviewer)
    assert run(review, inp) == attempt_id
    assert run(review, inp) == attempt_id
    assert len(reviewer.calls) == 1 and len(rows(sessions, inp)) == 1

    sessions, storage, inp, attempt_id, reviewer = world(template, tmp_path, answer("FAIL", HIGH))
    review = stage(template, sessions, storage, reviewer)
    for _ in range(2):
        with pytest.raises(ApplicationError) as caught:
            run(review, inp)
        assert caught.value.type == GATE_FAILED
    assert len(reviewer.calls) == 1 and len(rows(sessions, inp)) == 1


def test_wg11_a_recorded_review_about_another_bundle_is_refused(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp, _, reviewer = world(template, tmp_path, answer("PASS"))
    review = stage(template, sessions, storage, reviewer)
    run(review, inp)
    with sessions() as session:  # the row now names bytes that are not the stored bundle's
        row = GateResultRepository(session, ctx(inp)).list()[0]
        row.bundle_sha256 = "f" * 64
        session.commit()
    with pytest.raises(ApplicationError) as caught:
        run(review, inp)
    assert caught.value.type == GATE_INPUT_MISMATCH and caught.value.non_retryable


# --- what the model side can do ------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        BudgetExceeded("token budget reached: 10 of 10 tokens (Workflow build)"),
        SchemaInvalidAfterRetries(()),
        LlmTransportError("bifrost unreachable"),
    ],
    ids=["budget", "schema", "transport"],
)
def test_wg11_a_model_side_error_surfaces_unchanged_and_leaves_no_row(
    template: LoadedTemplate, tmp_path: Path, error: Exception
) -> None:
    """The workflow turns these (by type name) into the parked-build path; the stage must not
    swallow, wrap or record them."""
    sessions, storage, inp, _, reviewer = world(template, tmp_path, error)
    with pytest.raises(type(error)):
        run(stage(template, sessions, storage, reviewer), inp)
    assert rows(sessions, inp) == []
    assert len(reviewer.calls) == 1


def test_wg11_the_review_stage_is_never_given_an_unassembled_attempt(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    reviewer = FakeReviewer(answer("PASS"))
    with pytest.raises(ApplicationError) as caught:  # no code attempt at all: a wiring bug
        run(stage(template, sessions, storage, reviewer), inp)
    assert caught.value.type == GATE_INPUT_MISMATCH
    assert reviewer.calls == []


# --- the pure parts -----------------------------------------------------------------------------


def test_wg11_the_diff_holds_only_the_files_that_differ_from_the_skeleton() -> None:
    skeleton = {"a.py": "x = 1\ny = 2\n", "b.py": "same\n"}
    files = {"a.py": "x = 1\ny = 3\n", "b.py": "same\n", "c.py": "new\n"}
    diff = unified_diff(skeleton, files)
    assert "--- a/a.py" in diff and "+++ b/a.py" in diff and "+y = 3" in diff
    assert "b.py" not in diff and "+++ b/c.py" in diff


def test_wg11_a_huge_diff_is_cut_with_a_marker() -> None:
    diff = unified_diff({}, {"a.py": "x = 1\n" * 40_000})
    assert len(diff) < 61_000 and diff.endswith("[diff truncated]\n")


def test_wg11_findings_are_stored_as_gate_review_with_a_review_code() -> None:
    [one] = to_findings(answer("FAIL", HIGH))
    assert one == {
        "gate": "REVIEW",
        "code": "REVIEW_WRONG_OUTPUT",
        "message": HIGH["message"],
        "file": "action_tool.py",
        "line": 35,
        "expected": "REJECTED",
        "severity": "HIGH",
    }
    [bare] = to_findings(answer("PASS", LOW))
    assert "file" not in bare and "line" not in bare  # None is left out


def test_wg11_context_names_are_the_two_data_blocks() -> None:
    ctx_ = review_context({"a": 1}, {"x.py": "a\n"}, {"x.py": "b\n"})
    assert list(ctx_) == ["spec", "diff"]
