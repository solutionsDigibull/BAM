"""Grid metadata for the ISATVON workbench (TD §8.8, E8.6): what `GET /schemas/isatvon/{version}`
answers. The API builds it from the `SubSkill` JSON Schema (`sbam_api.isatvon.grid`); these are the
shapes the client renders, so the workbench never makes a column from a row key.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from sbam_contracts._jsonschema import contract_schema


class Editor(StrEnum):
    """The only cell editors the client implements (allow-list)."""

    TEXT = "text"
    LARGE_TEXT = "largeText"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"
    EXPRESSION = "expression"
    STRING_LIST = "stringList"
    SELECT = "select"
    READONLY = "readonly"


class GridColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str
    header: str
    type: str
    editor: Editor
    required: bool
    editable: bool
    platform: bool
    enum: list[str] | None = None
    min: int | float | None = None
    max: int | float | None = None
    width: int


class GridGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    header: str
    description: str
    children: list[GridColumn]


class Grid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_groups: list[GridGroup]


class IsatvonSchemaResponse(BaseModel):
    """GET /schemas/isatvon/{version}."""

    version: str
    json_schema: dict[str, Any]
    grid: Grid


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/isatvon/`."""
    return {"schema_response": contract_schema(IsatvonSchemaResponse, "isatvon/schema_response")}
