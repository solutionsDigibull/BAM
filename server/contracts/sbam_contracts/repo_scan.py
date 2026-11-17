"""The scan report of one fetched repository (ADR 0028, decision 2; issue WG-7, #755).

A repository is untrusted input. The fetcher clones it at one pinned commit, copies the files out as
data (no script runs), checks the licence, the known vulnerabilities, the code patterns and malware,
and records the health of the project. This is the evidence of all that. It is stored
content-addressed
(the sha256 of its canonical JSON) and shown to the FDE, so what was checked can always be read
back.

`decision` says whether the tree was vendored. A refused licence, a host that is not approved, an
unsafe
tree (a symlink, a path outside the tree) or an infected file stops it: nothing is vendored, and the
report says why. A person may override a refused licence with a recorded reason
(`licence.override`).
"""

from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import GitCommit, RepoUrl, ShortText
from sbam_contracts.runs import RunInput

# The licences a repository may carry without a person's say-so (SPDX ids, ADR 0028 decision 2).
LICENCE_ALLOWLIST: Final = ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC")

Sha256Hex = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
IsoTime = Annotated[str, Field(strict=True, max_length=40)]  # an ISO 8601 UTC instant
Note = Annotated[str, Field(strict=True, max_length=300)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class LicenceVerdict(StrEnum):
    ALLOWED = "ALLOWED"  # on the allowlist
    REFUSED = "REFUSED"  # copyleft or a licence that is not on the allowlist
    UNKNOWN = "UNKNOWN"  # no licence found, or text nobody can classify (a custom licence)


class LicenceOverride(_Strict):
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    by: ShortText  # the person who accepted the licence


class LicenceFinding(_Strict):
    spdx: ShortText | None = None  # the identifier found, normalised; none when unknown
    source: ShortText  # the file or field it was read from
    verdict: Annotated[LicenceVerdict, Field(strict=False)]
    override: LicenceOverride | None = None


class DependencyLicence(_Strict):
    ecosystem: ShortText  # PyPI or npm
    name: ShortText
    version: ShortText
    spdx: ShortText | None = None
    verdict: Annotated[LicenceVerdict, Field(strict=False)]


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class VulnFinding(_Strict):
    ecosystem: ShortText
    package: ShortText
    version: ShortText
    id: ShortText  # an OSV, GHSA or CVE id
    summary: Note = ""
    severity: Annotated[Severity, Field(strict=False)] = Severity.UNKNOWN


class StaticFinding(_Strict):
    tool: ShortText  # bandit, or pattern for the built-in pattern scan
    rule: ShortText
    path: ShortText
    line: Annotated[int, Field(strict=True, ge=0)] = 0
    severity: Annotated[Severity, Field(strict=False)] = Severity.UNKNOWN
    message: Note = ""


class ClamFinding(_Strict):
    path: ShortText
    signature: ShortText  # the signature clamd reported; the file is quarantined and not vendored


class Health(_Strict):
    last_commit_at: IsoTime | None = None
    last_release_at: IsoTime | None = None
    archived: bool | None = None  # none: the host did not say
    flags: list[ShortText] = Field(default_factory=list, max_length=10)  # ARCHIVED, STALE, ...


class FetchDecision(StrEnum):
    VENDORED = "VENDORED"
    REFUSED = "REFUSED"


class RefusalReason(StrEnum):
    HOST_NOT_APPROVED = "HOST_NOT_APPROVED"
    CLONE_FAILED = "CLONE_FAILED"
    LICENCE = "LICENCE"
    MALWARE = "MALWARE"
    UNSAFE_TREE = "UNSAFE_TREE"  # a symlink, a path outside the tree, a special file, a gitlink
    TOO_LARGE = "TOO_LARGE"


class RepoScanReport(_Strict):
    url: RepoUrl
    host: ShortText
    ref: ShortText | None = None  # the branch or tag that was asked for
    commit: GitCommit | None = (
        None  # the hash it resolved to, once; none if the clone never started
    )
    decision: Annotated[FetchDecision, Field(strict=False)]
    refusals: list[Annotated[RefusalReason, Field(strict=False)]] = Field(
        default_factory=list, max_length=10
    )
    licence: LicenceFinding | None = None
    dependency_licences: list[DependencyLicence] = Field(default_factory=list, max_length=500)
    vulnerabilities: list[VulnFinding] = Field(default_factory=list, max_length=500)
    static_findings: list[StaticFinding] = Field(default_factory=list, max_length=500)
    clam_findings: list[ClamFinding] = Field(default_factory=list, max_length=100)
    files_scanned: Annotated[int, Field(strict=True, ge=0)] = 0
    unsafe_paths: list[ShortText] = Field(default_factory=list, max_length=100)
    health: Health = Field(default_factory=Health)
    bundle_sha256: Sha256Hex | None = (
        None  # the stored tree; none unless the repository was vendored
    )
    bundle_files: Annotated[int, Field(strict=True, ge=0)] = 0
    notes: list[Note] = Field(default_factory=list, max_length=20)


class RepoFetchInput(RunInput):
    """Input of the `fetch_and_scan_repo` activity: IDs and URLs, never content.

    `approved_urls` are the repository URLs the ISATVON, the Admin masters (the repository
    catalog) and
    the FDE gave for this project; a repository host that none of them names is refused."""

    url: RepoUrl
    ref: ShortText | None = None
    approved_urls: list[RepoUrl] = Field(min_length=1, max_length=100)
    licence_override: LicenceOverride | None = None


class RepoFetchOutput(_Strict):
    report_sha256: Sha256Hex  # the stored `RepoScanReport`
    decision: Annotated[FetchDecision, Field(strict=False)]
    commit: GitCommit | None = None
    bundle_sha256: Sha256Hex | None = None


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/repo/`."""
    return {"scan": contract_schema(RepoScanReport, "repo/scan")}
