"""Workflow versions MAJOR.0.PATCH and the patch route (#132, E14.4, TD §11.4, ADR 0037).

- `GET W/versions` -> `WorkflowVersionList`: every frozen version of the Workflow, newest first. A
  frozen row is never edited, so `superseded` is derived: a newer version of the same Workflow
  (a patch) or of a later Workflow (a newer approved SOW) exists.
- `POST W/sub-skills/{sub_skill_id}/new-version` -> 202 `NewVersionStarted`: the FDE opens the next
  PATCH of a FROZEN Sub-Skill (409 `NOT_FROZEN` for any other state). The frozen files stay.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.manifest import VERSION_PATTERN

Version = Annotated[str, Field(pattern=VERSION_PATTERN)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowVersion(_Strict):
    id: uuid.UUID
    workflow_id: uuid.UUID
    version: Version
    sow_version_id: uuid.UUID
    workflow_manifest_id: uuid.UUID
    change_reason: Annotated[str, Field(max_length=500)]
    frozen_at: datetime
    superseded: bool


class WorkflowVersionList(_Strict):
    items: list[WorkflowVersion]


class NewVersionRequest(_Strict):
    change_reason: Annotated[str, Field(min_length=1, max_length=500)]


class NewVersionStarted(_Strict):
    workflow_id: uuid.UUID
    sub_skill_id: uuid.UUID
    next_version: Version  # the version the patch will be frozen as


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/workflow_versions/`."""
    models: dict[str, type[BaseModel]] = {
        "list": WorkflowVersionList,
        "new_request": NewVersionRequest,
        "new_started": NewVersionStarted,
    }
    return {
        stem: contract_schema(model, f"workflow_versions/{stem}") for stem, model in models.items()
    }
