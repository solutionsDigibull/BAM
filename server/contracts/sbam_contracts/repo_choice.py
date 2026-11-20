"""The choose-a-repository step of a Sub-Skill build (ADR 0028, decision 1, step 3; issue #756).

When no repository is named in the ISATVON, the build scores the project's Admin catalogue (#754,
#759). One clear winner is used and recorded. Several close candidates or a weak match pause the
build and ask the FDE; with no candidate the question is whether the FDE has a repository, and if
not the code is generated. These are the bodies of that step:

- `RepoQuestion`: what the workbench shows (the short list, the scores, the reasons). Written to the
  audit trail as `repo_question_asked` when the build starts to wait.
- `RepoAnswerRequest`: the FDE's answer, `POST W/sub-skills/{sub_id}/repo-answer`. The API checks it
  against the stored question and sends `RepoAnswerSignal` to the build, which records the
  `RepoDecision` (who, when) as `repo_decided`.
- `RepoQuestions`: `GET W/repo-questions`, the questions of a Workflow's builds that wait, and the
  decisions already taken.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.build import BuildStageInput
from sbam_contracts.isatvon14 import RepoUrl, ShortText
from sbam_contracts.pipeline import ActorId
from sbam_contracts.repo_decision import RepoCandidate, RepoDecision, RepoOutcome

REPO_ANSWER_SIGNAL: Final = "repo_answer"
QUESTION_ASKED: Final = "repo_question_asked"  # audit action: the build now waits for the FDE
DECIDED: Final = "repo_decided"  # audit action: the repository decision of the build (RepoDecision)
REMINDER_NOTIFICATION: Final = "sub_skill.repo_choice_reminder"
ANSWER_INVALID: Final = "RepoAnswerInvalid"  # ApplicationError.type: an answer the API let through
QUESTION_MISSING: Final = "RepoQuestionMissing"  # ApplicationError.type: an answer, nothing asked
MAX_SHORT_LIST: Final = 5
REPO_REMINDER_EVERY: Final = timedelta(days=3)  # how often the build reminds the FDE (#931)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RepoQuestionKind(StrEnum):
    CLOSE_CANDIDATES = "CLOSE_CANDIDATES"  # several candidates fit about equally well
    WEAK_MATCH = "WEAK_MATCH"  # the best candidate is not clearly the same job
    NO_CANDIDATE = "NO_CANDIDATE"  # the catalogue has nothing: does the FDE have a repository?


class RepoAnswerAction(StrEnum):
    CHOOSE = "CHOOSE"  # use `url`: one of the short list (or the FDE's own, with no candidate)
    NO_REPO = "NO_REPO"  # no repository: the code is generated


class RepoQuestion(_Strict):
    sub_skill_id: ShortText
    kind: Annotated[RepoQuestionKind, Field(strict=False)]
    candidates: list[RepoCandidate] = Field(default_factory=list, max_length=MAX_SHORT_LIST)
    asked_at: datetime


class RepoAnswerRequest(_Strict):
    """Body of the answer. `url` is required to choose and absent for "no repository"."""

    action: RepoAnswerAction
    url: RepoUrl | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.action is RepoAnswerAction.CHOOSE and self.url is None:
            raise ValueError("choosing a repository needs its url")
        if self.action is RepoAnswerAction.NO_REPO and self.url is not None:
            raise ValueError("no repository means no url")
        return self


class RepoAnswerSignal(_Strict):
    """`repo_answer` signal, sent by the API after it authorized the FDE and the project."""

    action: RepoAnswerAction
    url: RepoUrl | None = None
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    actor_id: ActorId


class RepoAnswered(_Strict):
    """202 body: the build that was told the FDE's answer."""

    sub_skill_id: UUID
    build_no: int


class PendingRepoQuestion(_Strict):
    question: RepoQuestion
    sub_skill_name: str | None = None  # the Sub-Skill's name, for the card's heading
    build_no: int | None = None


class RepoQuestions(_Strict):
    """`questions`: builds waiting for the FDE (empty: nothing to decide). `decisions`: the
    repository decisions already recorded for the Workflow's Sub-Skills, newest per Sub-Skill."""

    workflow_id: str
    questions: list[PendingRepoQuestion] = Field(default_factory=list, max_length=200)
    decisions: list[RepoDecision] = Field(default_factory=list, max_length=200)


class ChooseRepoInput(BuildStageInput):
    """Input of the `choose_repo` activity: `answer` is empty on the first call (score the
    catalogue, decide or ask) and holds the FDE's answer on the call after the wait."""

    answer: RepoAnswerSignal | None = None


class ChooseRepoStatus(StrEnum):
    DECIDED = "DECIDED"  # the decision is recorded: the build goes on
    ASKED = "ASKED"  # the question is recorded: the build waits for the FDE


class ChooseRepoResult(_Strict):
    status: Annotated[ChooseRepoStatus, Field(strict=False)]
    outcome: Annotated[RepoOutcome, Field(strict=False)] | None = None  # when DECIDED
    url: RepoUrl | None = None  # the repository the code starts from, when DECIDED with REPO


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/repo_choice/`."""
    return {
        "question": contract_schema(RepoQuestion, "repo_choice/question"),
        "answer_request": contract_schema(RepoAnswerRequest, "repo_choice/answer_request"),
        "answered": contract_schema(RepoAnswered, "repo_choice/answered"),
        "questions": contract_schema(RepoQuestions, "repo_choice/questions"),
    }
