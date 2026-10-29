"""Reading the manifests back (E9.6, TD §8.8, §11): what the Manifest viewer shows.

- `GET W/sub-skills/{sub_id}/manifests` and `GET W/manifests` → `ManifestVersionList`, newest first.
- `GET W/sub-skills/{sub_id}/manifest?version=&build_no=` and `GET W/manifest?build_no=` →
  `ManifestView`: the stored manifest (a `SkillManifest` / `WorkflowManifest` as JSON) and its hash.
- `GET /schemas/manifest/{name}` serves the committed JSON Schema (`skill_manifest`,
  `workflow_manifest`) for the editor's hover.
"""

from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from sbam_contracts._jsonschema import contract_schema

# MAJOR.0.PATCH (C-32), as a query parameter
VERSION_PATTERN: Final = r"^\d+\.0\.\d+$"


class ManifestVersion(BaseModel):
    """One stored manifest: a Sub-Skill build stage key, or a Workflow build."""

    model_config = ConfigDict(extra="forbid")

    version: str  # MAJOR.0.PATCH
    sha256: str  # governance.deterministic_hash / integrity.content_sha256
    build_no: int
    cycle: int | None = None  # Sub-Skill manifests only
    attempt: int | None = None
    is_frozen: bool
    created_at: datetime


class ManifestVersionList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ManifestVersion]


class ManifestView(ManifestVersion):
    """The manifest as stored, rendered read-only by the viewer (never as HTML)."""

    schema_version: str
    reused: bool = Field(
        default=False, description="the Sub-Skill was reused, or its hash equals the build before"
    )
    manifest: dict[str, JsonValue]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/manifest/`."""
    models: dict[str, type[BaseModel]] = {
        "manifest_version_list": ManifestVersionList,
        "manifest_view": ManifestView,
    }
    return {stem: contract_schema(model, f"manifest/{stem}") for stem, model in models.items()}
