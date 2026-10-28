"""Code viewer API contracts (TD §9.3, E11.5): a Sub-Skill's attempts, their files and diffs.

Read-only. `GET W/sub-skills/{sub_id}/code/attempts` lists the attempts of one build cycle (the
newest unless `build_no` / `cycle` say otherwise), `.../attempts/{n}/files` the bundle's file map or
(with `path`) one file, `.../attempts/{n}/diff/{m}` a unified diff between two attempts. `path` is
only ever matched against the attempt's stored file map, never used to open anything.
"""

from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema

MAX_VIEW_BYTES: Final = 1024 * 1024  # a file view is capped at 1 MiB; more is cut and flagged
MAX_DIFF_BYTES: Final = 1024 * 1024

Line = Annotated[int, Field(ge=1)]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CodeFinding(_Out):
    """One gate finding (the shape E11.3 / E12.2 write into `code_attempts.findings`)."""

    gate: str
    code: str
    file: str | None = None
    line: Line | None = None
    col: int | None = None
    message: str


class CodeFileRef(_Out):
    path: str
    sha256: str


class CodeAttemptSummary(_Out):
    build_no: int
    cycle: int
    attempt: Annotated[int, Field(ge=0, le=2)]
    status: str
    template_name: str
    template_version: str
    correction_reason: str | None = None
    files: list[CodeFileRef]  # empty when assembly failed (nothing was stored)
    findings: list[CodeFinding]


class CodeAttemptList(_Out):
    items: list[CodeAttemptSummary]  # attempt 0..2, in order


class LineRange(_Out):
    start: Line
    end: Line  # inclusive


class CodeFileView(_Out):
    path: str
    sha256: str
    content: str
    truncated: bool  # `content` was cut at MAX_VIEW_BYTES
    slots: list[LineRange]  # the lines between LLM_BODY markers; every other line is protected
    findings: list[
        CodeFinding
    ]  # this attempt's findings for this file (a line may be past the end)


class CodeDiff(_Out):
    from_attempt: int
    to_attempt: int
    path: str
    changed: bool
    unified: str  # empty when `changed` is false
    truncated: bool


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/code/`."""
    models: dict[str, type[BaseModel]] = {
        "attempt_list": CodeAttemptList,
        "file_view": CodeFileView,
        "diff": CodeDiff,
    }
    return {stem: contract_schema(model, f"code/{stem}") for stem, model in models.items()}
