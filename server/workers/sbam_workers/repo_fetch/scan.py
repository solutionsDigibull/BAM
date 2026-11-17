"""One repository, from URL to stored evidence (WG-7, #755).

    approved host? -> pinned clone (no checkout) -> tree as data (symlinks, traversal refused)
    -> licence of the repo and its dependencies -> vulnerabilities -> Bandit and pattern scan
    -> ClamAV on every file -> health -> vendor the tree only if nothing refused it -> store the
    report

Any refusal stops the vendoring and is written in the report; the report is stored either way,
because
what was checked is the evidence. A step that cannot give an answer (git, ClamAV, a service)
raises: it
is retried by Temporal and is never turned into a pass.
"""

import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, BinaryIO, Protocol
from urllib.parse import urlsplit

from sbam_api.scan.clamd import ScanVerdict
from sbam_api.storage.base import StorageBackend, StorageScope
from sbam_contracts.repo_scan import (
    ClamFinding,
    FetchDecision,
    Health,
    LicenceFinding,
    LicenceOverride,
    LicenceVerdict,
    RefusalReason,
    RepoScanReport,
)

from sbam_workers.build.gates.toolchain import Executor
from sbam_workers.repo_fetch.clone import GitError, fetched
from sbam_workers.repo_fetch.deps import read_dependencies
from sbam_workers.repo_fetch.health import health_of
from sbam_workers.repo_fetch.hosts import HostPolicy, HostRefused
from sbam_workers.repo_fetch.http import JsonHttp
from sbam_workers.repo_fetch.licence import licence_of_repo
from sbam_workers.repo_fetch.packages import dependency_licences, vulnerabilities
from sbam_workers.repo_fetch.report import store_bundle, store_report
from sbam_workers.repo_fetch.static import bandit_scan, pattern_scan
from sbam_workers.repo_fetch.tree import read_tree

log = logging.getLogger("sbam_workers.repo_fetch")


class Clam(Protocol):
    def scan(self, stream: BinaryIO) -> ScanVerdict: ...


@dataclass(frozen=True)
class Request:
    url: str
    ref: str | None
    policy: HostPolicy
    override: LicenceOverride | None = None


@dataclass(frozen=True)
class Tools:
    storage: StorageBackend
    scope: StorageScope
    clam: Clam
    http: JsonHttp | None = None
    executor: Executor | None = None
    git_config: Sequence[tuple[str, str]] = ()  # tests only: a local mirror, the `file` protocol
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class Outcome:
    report: RepoScanReport
    report_sha256: str


def _refused(
    request: Request, host: str, reasons: list[RefusalReason], note: str
) -> RepoScanReport:
    return RepoScanReport(
        url=request.url,
        host=host[:300],
        ref=request.ref,
        decision=FetchDecision.REFUSED,
        refusals=reasons,
        notes=[note[:300]],
    )


def fetch_and_scan(request: Request, tools: Tools) -> Outcome:
    try:
        host = request.policy.check_repo(request.url)
    except HostRefused as refusal:
        host = (urlsplit(request.url).hostname or "?").lower()
        report = _refused(request, host, [RefusalReason.HOST_NOT_APPROVED], str(refusal))
        return _done(report, tools)
    try:
        report = _scan(request, tools, host)
    except GitError as exc:
        report = _refused(request, host, [RefusalReason.CLONE_FAILED], str(exc))
    return _done(report, tools)


def _done(report: RepoScanReport, tools: Tools) -> Outcome:
    stored = store_report(tools.storage, tools.scope, report)
    return Outcome(report, stored.sha256)


def _scan(request: Request, tools: Tools, host: str) -> RepoScanReport:
    with fetched(tools.git_config, request.url, request.ref) as (git, repo, commit):
        tree = read_tree(git, repo, commit)
        commit_time = git.run("show", "-s", "--format=%cI", commit, cwd=repo).decode().strip()
    base: dict[str, Any] = {
        "url": request.url,
        "host": host,
        "ref": request.ref,
        "commit": commit,
    }
    if tree.unsafe or tree.too_large:
        reason = RefusalReason.UNSAFE_TREE if tree.unsafe else RefusalReason.TOO_LARGE
        return RepoScanReport(
            **base,
            decision=FetchDecision.REFUSED,
            refusals=[reason],
            unsafe_paths=tree.unsafe[:100],
            notes=["the tree was not read any further"],
        )
    files = tree.files
    notes: list[str] = []

    licence = licence_of_repo(files)
    deps = read_dependencies(files)
    dep_licences = dependency_licences(tools.http, deps) if tools.http else []
    if deps and tools.http is None:
        notes.append("dependency licences and vulnerabilities were not checked: no network client")
    vulns = vulnerabilities(tools.http, deps) if tools.http else []
    static = pattern_scan(files) + bandit_scan(files, tools.executor)

    infected: list[ClamFinding] = []
    for path, data in sorted(files.items()):
        verdict = tools.clam.scan(io.BytesIO(data))
        if verdict.infected:
            infected.append(
                ClamFinding(path=path[:300], signature=(verdict.signature or "unknown")[:300])
            )

    health = health_of(request.url, commit_time, tools.http, tools.now)

    refusals: list[RefusalReason] = []
    not_allowed = licence.verdict != LicenceVerdict.ALLOWED or any(
        d.verdict != LicenceVerdict.ALLOWED for d in dep_licences
    )
    if not_allowed:
        if request.override:
            licence = LicenceFinding(
                spdx=licence.spdx,
                source=licence.source,
                verdict=licence.verdict,
                override=request.override,
            )
            notes.append(
                f"licence accepted by {request.override.by}: {request.override.reason}"[:300]
            )
        else:
            refusals.append(RefusalReason.LICENCE)
    if infected:
        refusals.append(RefusalReason.MALWARE)

    bundle_sha, bundle_files = None, 0
    if not refusals:
        stored = store_bundle(tools.storage, tools.scope, files)
        bundle_sha, bundle_files = stored.sha256, len(files)
    return RepoScanReport(
        **base,
        decision=FetchDecision.REFUSED if refusals else FetchDecision.VENDORED,
        refusals=refusals,
        licence=licence,
        dependency_licences=dep_licences,
        vulnerabilities=vulns,
        static_findings=static,
        clam_findings=infected,
        files_scanned=len(files),
        health=health if isinstance(health, Health) else Health(),
        bundle_sha256=bundle_sha,
        bundle_files=bundle_files,
        notes=notes,
    )
