"""The Workflow's foundational `schema.sql` (TD §12.6, O-16, E9.4).

One PostgreSQL schema per Workflow for every table its Sub-Skills declare (`v_variables.tables`,
`o_outcome.state_handoff_file.table`). Its base is the FDE's uploaded `.sql` when there is one;
SBAM generates DDL for the tables the base doesn't define and appends the platform's insert-only
guard to every handoff table. The SCHEMA_SQL gate parses it, applies it to a throwaway database
and checks every declared table and guard; its findings use `SchemaFindingCode`.

- `GET W/schema-sql?version=` → `WorkflowSchemaSql`.
- `PUT W/schema-sql/source` takes `SchemaSqlSourcePut` → `WorkflowSchemaSql` (422
  SCHEMA_PARSE_ERROR with the statement index, 409 WORKFLOW_BUILDING).
"""

from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.build_dashboard import Finding, GateStatus

# A table SBAM creates or expects: lower-case, unquoted, within PostgreSQL's 63 characters.
TABLE_NAME: Final = r"^[a-z_][a-z0-9_]{0,62}$"
MAX_SOURCE_BYTES: Final = 15 * 1024 * 1024  # an uploaded base .sql


class SchemaFindingCode(StrEnum):
    PARSE_ERROR = "PARSE_ERROR"  # sqlglot can't parse a statement
    APPLY_ERROR = "APPLY_ERROR"  # PostgreSQL rejected the script
    MISSING_TABLE = "MISSING_TABLE"  # a declared table has no DDL / isn't there after apply
    NO_INSERT_ONLY_GUARD = "NO_INSERT_ONLY_GUARD"  # a handoff table accepts UPDATE/DELETE/TRUNCATE
    FORBIDDEN_STATEMENT = "FORBIDDEN_STATEMENT"  # outside the DDL allowlist
    CONFLICT = "CONFLICT"  # two Sub-Skills define one table differently (never last-write-wins)
    INVALID_TABLE_NAME = "INVALID_TABLE_NAME"  # fails TABLE_NAME, or a PostgreSQL reserved word


class SchemaSource(StrEnum):
    UPLOADED = "UPLOADED"  # the FDE's .sql is the base
    GENERATED = "GENERATED"  # SBAM generated every table


class SchemaSqlSourcePut(BaseModel):
    """`PUT W/schema-sql/source`: this project document (a parsed, clean `.sql`) is the base."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID


class WorkflowSchemaSql(BaseModel):
    """One version of a Workflow's schema.sql with its SCHEMA_SQL verdict. An uploaded base that no
    build has applied yet is PENDING; `sql` is empty until then for a generated one."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: UUID
    version: int = Field(ge=1)
    source: SchemaSource
    source_document_id: UUID | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    tables: list[str] = Field(default_factory=list)
    status: GateStatus
    findings: list[Finding] = Field(default_factory=list)
    sql: str = ""


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/build/`."""
    models: dict[str, type[BaseModel]] = {
        "schema_sql": WorkflowSchemaSql,
        "schema_sql_source_put": SchemaSqlSourcePut,
    }
    return {stem: contract_schema(model, f"build/{stem}") for stem, model in models.items()}
