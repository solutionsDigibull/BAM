"""Root JSON Schema helper shared by every contract module (issue E1.2).

Every committed root schema declares JSON Schema draft 2020-12 and a versioned `$id`, so a schema
file always says which dialect and which contracts release it belongs to.
"""

from importlib.metadata import version
from typing import Any, Final

from pydantic import BaseModel

DRAFT: Final = "https://json-schema.org/draft/2020-12/schema"
CONTRACTS_VERSION: Final = version("sbam-contracts")


def schema_id(path: str) -> str:
    """`$id` for the schema stored at `schemas/<path>.schema.json`, e.g. `sow/sow_document`."""
    return f"urn:sbam:contracts:{path}:{CONTRACTS_VERSION}"


def contract_schema(model: type[BaseModel], path: str) -> dict[str, Any]:
    """`model`'s validation JSON Schema with `$schema` and `$id` first."""
    return {"$schema": DRAFT, "$id": schema_id(path), **model.model_json_schema()}
