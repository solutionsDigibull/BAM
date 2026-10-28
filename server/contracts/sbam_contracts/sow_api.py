"""SOW API contracts (TD §8.7, issue E6.3): versions, save, diff, regenerate, and the TipTap shape.

The editor's document (`content_tiptap`) is TipTap JSON in an SBAM-defined shape (ADR 0019), which
the server converts to and from `SowDocument`:

    doc
      sowSection {section_id}           x 9, in `SECTIONS` order, template-locked
        heading {level: 2}              the section's title from `SECTION_TITLES`, unchanged
        sowField {field}                one per field of the section (not `citations`)
        sowItem {id, module_id?}        modules and features only: one per module / feature
          heading {level: 3}            the item's `name`
          sowField {field}              its other fields
          sowCitations                  its citations
        sowCitations                    the section's citations (not on modules / features)
          citation {okf_ref_id, document_version_id, location}

A `sowField` holds its value as: paragraphs (text and number fields; paragraphs join with a line
break), a `bulletList` of `listItem > paragraph` (lists of text), nested `sowField`s (an object,
e.g. `bound`, `tools`) or `sowRecord` nodes of nested `sowField`s (a list of objects, `roles`).
Text marks (bold, italic, ...) are kept in `content_tiptap` only; `structured` holds plain text.
Ids, module ids and citations are node attributes, so typing in the editor cannot change them.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.runs import RunInput
from sbam_contracts.sow import SECTIONS

MAX_TIPTAP_BYTES: Final = 2 * 1024 * 1024  # compact JSON of content_tiptap; more is 413
MAX_HINT_CHARS: Final = 2000
SOW_REGENERATE: Final = "SowRegenerate"  # workflow type of POST P/sow/regenerate from SOW_REVIEW
SOW_EXPORT: Final = "SowExport"  # workflow type of GET P/sow/versions/{v}/export (E6.4)
EXPORT_TEMPLATE: Final = "1.0.0"  # bump when the layout changes: cached exports are keyed by it

SECTION_TITLES: Final[dict[str, str]] = {
    "overview": "Overview",
    "users_authority": "Users and authority",
    "scope": "Scope",
    "modules": "Modules",
    "features": "Features",
    "data_integrations": "Data and integrations",
    "rules_constraints": "Rules and constraints",
    "deliverables_acceptance": "Deliverables and acceptance",
    "assumptions_open_points": "Assumptions and open points",
}
if tuple(SECTION_TITLES) != SECTIONS:  # pragma: no cover - a guard for whoever edits SECTIONS
    raise RuntimeError("SECTION_TITLES must follow SECTIONS")


def sow_export_workflow_id(sow_version_id: UUID, fmt: "ExportFormat") -> str:
    """One render per version and format at a time: a second request joins the running one."""
    return f"sow-export-{sow_version_id}-{fmt.value}"


def sow_workflow_id(project_id: UUID) -> str:
    """One regeneration at a time per project (Temporal's FAIL conflict policy)."""
    return f"sow-{project_id}"


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TipTapMark(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Annotated[str, Field(min_length=1, max_length=64)]
    attrs: dict[str, Any] | None = None


class TipTapNode(BaseModel):
    """Any TipTap node. The SOW shape (module docstring) is checked by the server on save."""

    model_config = ConfigDict(extra="allow")

    type: Annotated[str, Field(min_length=1, max_length=64)]
    attrs: dict[str, Any] | None = None
    content: list["TipTapNode"] | None = None
    text: str | None = None
    marks: list[TipTapMark] | None = None


class SowVersionStatus(StrEnum):
    GENERATED = "GENERATED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    GENERATION_FAILED = "GENERATION_FAILED"


class SowVersionSource(StrEnum):
    GENERATED = "GENERATED"
    EDITED = "EDITED"


class SowVersionSummary(_Dto):
    id: UUID
    version: Annotated[int, Field(ge=1)]
    status: SowVersionStatus
    source: SowVersionSource
    sha256: str | None = None  # None for a version whose generation failed
    parent_version: int | None = None
    knowledge_version_id: UUID
    error_reason: str | None = None
    author: str | None = None  # the FDE who saved it; None for SBAM's generations
    created_at: datetime


class SowVersionList(_Dto):
    items: list[SowVersionSummary]  # newest first


class SowCitationRef(_Dto):
    """A citation with where it sits in the document (`features.0`, `scope`, ...)."""

    path: str
    kind: Literal["okf", "fde"] = "okf"
    okf_ref_id: str | None = None  # None for an FDE-authored citation
    document_version_id: UUID | None = None
    location: dict[str, Any] = Field(default_factory=dict)
    author: str | None = None  # FDE-authored only
    date: str | None = None
    note: str | None = None


class SowVersionDetail(SowVersionSummary):
    content_tiptap: TipTapNode | None = None
    structured: dict[str, Any] | None = None  # SowDocument
    citations: list[SowCitationRef] = Field(default_factory=list)


MAX_CITATION_PASSAGE: Final = 4000


class SowCitationDetail(_Dto):
    """What a citation points at (E6.5 citation panel), read from the version's OKF bundle."""

    okf_ref_id: str
    kind: Literal["chunk", "entity"]
    document_version_id: UUID
    title: str | None = None  # None: the document was deleted ("source deleted")
    location: dict[str, Any]
    passage: str  # at most MAX_CITATION_PASSAGE characters; `truncated` says if it was cut
    truncated: bool = False
    source_available: bool  # GET .../source serves the document's text


class SowApproveRequest(_Dto):
    """POST P/sow/versions/{v}/approve (E7.1): the hash of the version the FDE reviewed."""

    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SowApproval(_Dto):
    version: Annotated[int, Field(ge=1)]
    status: SowVersionStatus
    is_locked: bool
    approved_at: datetime
    approved_by: str
    sha256: str


class SowOverview(_Dto):
    sow_id: UUID
    current: SowVersionSummary | None = None
    approved: SowVersionSummary | None = None
    etag: str  # the If-Match a save must send


class SowSaveRequest(_Dto):
    content_tiptap: TipTapNode
    base_version: Annotated[int, Field(ge=1)]


class SowRegenerateRequest(_Dto):
    hint: Annotated[str | None, Field(max_length=MAX_HINT_CHARS)] = None


class SectionChange(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"
    UNCHANGED = "UNCHANGED"


class SowDiffItem(_Dto):
    """A changed module / feature (by id) or, in other sections, a changed field (by name)."""

    id: str
    change: SectionChange


class SowSectionDiff(_Dto):
    section_id: Literal[
        "overview",
        "users_authority",
        "scope",
        "modules",
        "features",
        "data_integrations",
        "rules_constraints",
        "deliverables_acceptance",
        "assumptions_open_points",
    ]
    change: SectionChange
    items: list[SowDiffItem] = Field(default_factory=list)


class SowDiff(_Dto):
    version: int
    other: int
    sections: list[SowSectionDiff]  # all nine, in `SECTIONS` order


class ExportFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    MD = "md"


class SowExportInput(RunInput):
    """`export_sow`'s input: ids only; the worker reads the version itself."""

    sow_version_id: Annotated[UUID, Field(strict=False)]
    format: ExportFormat


class SowExportResult(_Dto):
    storage_key: str
    sha256: str  # of the exported file
    size: int


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/sow/`."""
    return {
        "overview": contract_schema(SowOverview, "sow/overview"),
        "version_list": contract_schema(SowVersionList, "sow/version_list"),
        "version_detail": contract_schema(SowVersionDetail, "sow/version_detail"),
        "save_request": contract_schema(SowSaveRequest, "sow/save_request"),
        "diff": contract_schema(SowDiff, "sow/diff"),
        "regenerate_request": contract_schema(SowRegenerateRequest, "sow/regenerate_request"),
        "approve_request": contract_schema(SowApproveRequest, "sow/approve_request"),
        "approval": contract_schema(SowApproval, "sow/approval"),
        "citation_detail": contract_schema(SowCitationDetail, "sow/citation_detail"),
    }
