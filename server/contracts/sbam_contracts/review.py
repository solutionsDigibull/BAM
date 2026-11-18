"""What the reviewer model answers (#759, WG-11): one review of the generated code against the spec.

The reviewer runs after the AST and static gates passed, on the diff from the skeleton to the
assembled bodies. Its answer is a verdict and findings that are precise enough for the corrector
to act on: a category, a message, and where in the bundle. A FAIL needs at least one HIGH finding,
so a failed review always names what to fix; a PASS may still carry LOW or MEDIUM notes. The worker
maps each finding to a `build_dashboard.Finding` with `gate=REVIEW` and `code=REVIEW_<category>`.
"""

from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.build_dashboard import Severity

MAX_REVIEW_FINDINGS: Final = 20
MESSAGE_MAX: Final = 1_000


class ReviewVerdict(StrEnum):
    PASS = "PASS"  # noqa: S105 - a verdict, not a password
    FAIL = "FAIL"


class ReviewCategory(StrEnum):
    """What kind of deviation a finding is; the finding code is `REVIEW_<category>`."""

    SPEC_MISMATCH = "SPEC_MISMATCH"  # the code does something the spec does not say
    MISSING_REQUIREMENT = "MISSING_REQUIREMENT"  # the spec asks for something the code leaves out
    WRONG_OUTPUT = "WRONG_OUTPUT"  # a gate output or tool result is not what the spec defines
    HANDOFF = "HANDOFF"  # the handoff record is incomplete or wrong
    STATE_WRITE = "STATE_WRITE"  # a state table is written wrongly
    SAFETY = "SAFETY"  # data exposure, unsafe handling the static gates cannot see
    OTHER = "OTHER"


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ReviewCategory
    severity: Severity
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MESSAGE_MAX)
    ]
    file: Annotated[str, Field(max_length=500)] | None = None
    line: Annotated[int, Field(ge=1)] | None = None
    expected: Annotated[str, Field(max_length=1_000)] | None = None


class ReviewAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: ReviewVerdict
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=MAX_REVIEW_FINDINGS)

    @model_validator(mode="after")
    def _verdict_matches_findings(self) -> "ReviewAnswer":
        high = any(f.severity is Severity.HIGH for f in self.findings)
        if self.verdict is ReviewVerdict.FAIL and not high:
            raise ValueError("a FAIL names at least one HIGH finding")
        if self.verdict is ReviewVerdict.PASS and high:
            raise ValueError("a PASS has no HIGH finding")
        return self


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/build/`."""
    return {"review_answer": contract_schema(ReviewAnswer, "build/review_answer")}
