"""A certified Workflow's entry in the registry (ADR 0028, decision 4; issue WG-12, #760).

SBAM keeps its own table of registered Workflows as the source of truth, and Bifrost is where SRIM
fetches from. This is one row of that table, and it is also what the lookup returns, so SRIM sees
the digest that was certified: `image_digest` and `manifest_hash` are copied from the frozen
manifest and its WORKFLOW certificate, never computed again.

A new version is a new entry. The entry it replaces is marked SUPERSEDED and disabled in Bifrost,
and is never deleted (no revocation, C-31): a running SRIM can still look its version up.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import ShortText
from sbam_contracts.manifest import (
    MCP_SERVER_NAME,
    VERSION_PATTERN,
    Digest,
    McpToolEntry,
    UiBundleEntry,
)


class RegistryStatus(StrEnum):
    REGISTERED = "REGISTERED"  # the current entry of its Workflow: enabled in Bifrost
    SUPERSEDED = "SUPERSEDED"  # a later version replaced it: disabled in Bifrost, still listed


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


Version = Annotated[str, Field(strict=True, pattern=VERSION_PATTERN)]  # MAJOR.0.PATCH
RegistryName = Annotated[
    str, Field(strict=True, max_length=64, pattern=MCP_SERVER_NAME.pattern)
]  # wf_<workflow>_v<major>_<patch>: underscores only, Bifrost forbids hyphens


class RegisteredWorkflow(_Strict):
    workflow_id: uuid.UUID
    version: Version
    registry_name: RegistryName  # the name of the Bifrost entry
    registry_id: ShortText  # Bifrost's id for the entry
    image_digest: Digest  # the image that was certified
    manifest_hash: Digest  # the frozen manifest it was certified on
    certification_id: uuid.UUID  # the WORKFLOW certificate
    owner: ShortText  # who the entry belongs to: the registering user or service
    tools: list[McpToolEntry] = Field(min_length=2)  # with their input and output schemas
    ui_bundle: UiBundleEntry | None = None  # the certified bundle: version, digest, entry, hosts
    status: RegistryStatus
    registered_at: datetime
    superseded_at: datetime | None = None
    superseded_by: Version | None = None  # the version that replaced it


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/workflow_registry/`."""
    return {"registration": contract_schema(RegisteredWorkflow, "workflow_registry/registration")}
