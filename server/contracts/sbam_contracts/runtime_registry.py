"""Runtime registry: what the `skill-runtime` image really contains (TD §20.2, §7.3, E12.1).

Generated inside the image from `importlib.metadata` at build time (the image is the truth, not the
lock file), keyed by the image digest once the image is built. The library check (#103), import
resolution (#120) and AST_IMPORTS read it; the Admin "Registry" page shows it.

Rules worth knowing
  * Package names are PEP 503-normalised (`Psycopg_Binary` -> `psycopg-binary`); the import names a
    distribution provides are `top_level_modules` (`psycopg-binary` -> `psycopg_binary`).
  * `content_sha256` covers everything except `generated_at` (and itself): two builds of the same
    image give the same hash (E12.1-T02). It must match the content, so a tampered file is rejected.
  * Sorting: packages by name, modules alphabetically, so the JSON is byte-stable.
"""

from datetime import datetime
from typing import Annotated, Any, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.canonical import HASH_PATTERN, sha256_of
from sbam_contracts.isatvon14 import normalize_package

IMAGE_DIGEST_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"
PACKAGE_NAME_PATTERN: Final = r"^[a-z0-9]+(-[a-z0-9]+)*$"  # PEP 503 normalised
MODULE_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]*$"


class RuntimePackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(pattern=PACKAGE_NAME_PATTERN, max_length=200)]
    version: Annotated[str, Field(min_length=1, max_length=100)]
    top_level_modules: list[Annotated[str, Field(pattern=MODULE_PATTERN)]]

    @model_validator(mode="after")
    def _sorted(self) -> Self:
        if self.top_level_modules != sorted(set(self.top_level_modules)):
            raise ValueError(f"{self.name}: top_level_modules must be sorted and unique")
        return self


class RuntimeRegistry(BaseModel):
    """`runtime_registry.json` as the generator writes it and the sync loads it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image_digest: Annotated[str | None, Field(pattern=IMAGE_DIGEST_PATTERN)] = None
    python_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    packages: list[RuntimePackage] = Field(min_length=1)
    generated_at: Annotated[datetime, Field(strict=False)]
    content_sha256: Annotated[str, Field(pattern=HASH_PATTERN.pattern)]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        names = [p.name for p in self.packages]
        if names != sorted(set(names)):
            raise ValueError("packages must be sorted by name and unique")
        if self.content_sha256 != registry_sha256(self):
            raise ValueError("content_sha256 does not match the registry content")
        return self

    def package(self, name: str) -> RuntimePackage | None:
        wanted = normalize_package(name)
        return next((p for p in self.packages if p.name == wanted), None)


def hashed_content(data: dict[str, Any]) -> dict[str, Any]:
    """The part of the registry `content_sha256` covers (no generated_at, no hash itself)."""
    return {k: v for k, v in data.items() if k not in {"generated_at", "content_sha256"}}


def registry_sha256(registry: RuntimeRegistry) -> str:
    return sha256_of(hashed_content(registry.model_dump(mode="json")))


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/runtime_registry/`."""
    return {
        "runtime_registry": contract_schema(RuntimeRegistry, "runtime_registry/runtime_registry")
    }
