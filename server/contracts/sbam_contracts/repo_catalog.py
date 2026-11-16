"""The repository catalog of one project (ADR 0028, decision 1; issue WG-6, #754).

The Agents master has a free-text `repo` column and the Tooling master a `links_path` column. The
catalog reads those two columns, read-only, into structured candidates the build can offer. Nothing
here fetches anything: a URL that is not https, carries credentials, points at an address on a
private network, or is malformed is reported in `rejected` and never becomes a candidate. Whether a
host may be fetched is still the fetcher's decision (WG-7).

An `id` is stable: it is derived from the master row and the canonical URL, so the same row yields
the same candidate on every read and a changed URL yields a new one.
"""

from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import RepoUrl, ShortText
from sbam_contracts.repo_decision import RepoSource

NOTES_MAX: Final = 200


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RejectReason(StrEnum):
    NOT_HTTPS = "NOT_HTTPS"  # http, ssh, git@, file and every other scheme
    HAS_CREDENTIALS = "HAS_CREDENTIALS"  # user or token inside the URL: never stored or shown
    HAS_QUERY = "HAS_QUERY"  # a query string (tokens travel there)
    PRIVATE_ADDRESS = "PRIVATE_ADDRESS"  # localhost, an IP address, a private or internal name
    INVALID_URL = "INVALID_URL"  # not a repository URL the contract accepts


class CatalogCandidate(_Strict):
    id: ShortText
    url: RepoUrl
    ref: ShortText | None = None  # a branch or tag written after `#`
    notes: Annotated[str, Field(strict=True, max_length=NOTES_MAX)] = ""
    source: Annotated[RepoSource, Field(strict=False)]  # ADMIN_AGENTS or ADMIN_TOOLING
    master_entry_id: ShortText  # the master row this came from
    stable_id: ShortText
    row_label: ShortText | None = None
    agent_name: ShortText | None = None  # the Agents row's name; none for a Tooling row


class CatalogRejection(_Strict):
    # What was written, clipped and with any user or token part removed.
    value: Annotated[str, Field(strict=True, max_length=NOTES_MAX)]
    reason: Annotated[RejectReason, Field(strict=False)]
    source: Annotated[RepoSource, Field(strict=False)]
    master_entry_id: ShortText
    stable_id: ShortText


class RepoCatalog(_Strict):
    project_id: ShortText
    structure_version: ShortText  # the masters workbook structure the entries follow
    candidates: list[CatalogCandidate] = Field(default_factory=list, max_length=500)
    rejected: list[CatalogRejection] = Field(default_factory=list, max_length=500)


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/repo/`."""
    return {"catalog": contract_schema(RepoCatalog, "repo/catalog")}
