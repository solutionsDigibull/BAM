"""The export package of a frozen Workflow version (#133, E14.5, PLAN Phase 14).

- `POST W/versions/{version}/export` with `Idempotency-Key` (a UUID) -> 202 `ExportAccepted`: the
  `ExportVersion` run that packages the version. 409 `NOT_FROZEN` unless the version is frozen. The
  same key answers the same run (and the same zip); two keys give byte-identical zips.
- `GET W/versions/{version}/export` -> 302 to a short-lived, tenant-scoped signed download URL; 404
  when the package has not been built (or the Workflow is not the caller's).

The package is a zip with sorted entries, fixed timestamps and permissions, no directories and no
symlinks. `CHECKSUMS.sha256` (in `sha256sum -c` format) is the last entry and covers every other
one. The handover to SRIM is the registry lookup (#760): there is no `/handover/*` route.
"""

import re
import uuid
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.manifest import VERSION_PATTERN
from sbam_contracts.runs import Run, RunInput

EXPORT_VERSION: Final = "ExportVersion"  # workflow type
EXPORT_ACTIVITY: Final = "export_version"  # its one activity

# The entries of the package (paths inside the zip).
CHECKSUMS_FILE: Final = "CHECKSUMS.sha256"
WORKFLOW_MANIFEST_FILE: Final = "workflow_manifest.json"
SCHEMA_SQL_FILE: Final = "schema.sql"
EVIDENCE_INDEX_FILE: Final = "evidence_index.json"
SBOM_FILE: Final = "sbom.cdx.json"  # CycloneDX 1.5 (#757)
ENV_EXAMPLE_FILE: Final = ".env.example"  # names only (ADR 0030)
TOOLS_FILE: Final = "tools.json"
IMAGE_FILE: Final = "image.json"  # the image digests
UI_BUNDLE_FILE: Final = "ui_bundle.json"  # the UI bundle entry (#857)
SKILLS_ROOT: Final = "skills"  # skills/<skill_id>/{manifest.json,loop_spec.json,code/...}
SKILL_MANIFEST_FILE: Final = "manifest.json"
SKILL_LOOP_SPEC_FILE: Final = "loop_spec.json"
SKILL_CODE_DIR: Final = "code"

SHA256_HEX: Final = re.compile(r"^[0-9a-f]{64}$")
Version = Annotated[str, Field(pattern=VERSION_PATTERN)]


def export_workflow_id(run_id: uuid.UUID) -> str:
    """One execution per (version, Idempotency-Key): the run id is derived from both."""
    return f"export-{run_id}"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportAccepted(_Strict):
    """`202 Accepted` body of `POST W/versions/{version}/export`."""

    run: Run
    workflow_id: uuid.UUID
    version: Version


class ExportInput(RunInput):
    """`ExportVersion`'s input: ids only."""

    workflow_id: uuid.UUID
    version: Version
    actor_id: Annotated[str, Field(min_length=1, max_length=255)]


class ExportResult(_Strict):
    """What the activity stored: the frozen EXPORT artifact."""

    workflow_id: uuid.UUID
    version: Version
    storage_key: Annotated[str, Field(min_length=1, max_length=1024)]
    sha256: Annotated[str, Field(pattern=SHA256_HEX.pattern)]
    size: Annotated[int, Field(ge=0)]
    entries: Annotated[int, Field(ge=1)]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/exports/`."""
    return {"accepted": contract_schema(ExportAccepted, "exports/accepted")}
