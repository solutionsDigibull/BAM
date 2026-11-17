"""`fetch_and_scan_repo`: the Temporal activity of the repository fetcher (WG-7, #755).

It registers itself on the build queue (never in ProjectPipeline: D3 wires it when the build asks
for a repository, WG-8). The activity reads the inputs, builds the tools and calls
`scan.fetch_and_scan`. A refused repository is a normal result (a stored report with
`decision: REFUSED`), not a failure; a step that could not answer (git, ClamAV, a service) raises
and Temporal retries it.

The code-level host allowlist is always on. Network containment of the fetcher process itself (a
container with no other egress) is the job of the host acceptance suite and egress collector
(WG-14).
"""

from functools import lru_cache

from sbam_api.scan.clamd import ClamdClient
from sbam_api.storage.base import StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_contracts.repo_scan import RepoFetchInput, RepoFetchOutput
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.repo_fetch.hosts import HostPolicy
from sbam_workers.repo_fetch.http import PolicyHttp
from sbam_workers.repo_fetch.scan import Clam, Request, Tools, fetch_and_scan
from sbam_workers.settings import WorkerSettings


@lru_cache(maxsize=1)
def _settings() -> WorkerSettings:
    return WorkerSettings()  # type: ignore[call-arg]  # values come from the environment


def _storage() -> StorageBackend:
    return make_backend(_settings())


def run(inp: RepoFetchInput, storage: StorageBackend, clam: Clam) -> RepoFetchOutput:
    policy = HostPolicy.for_urls(inp.approved_urls)
    scope = StorageScope(inp.tenant_id, inp.project_id)  # per project: a repository can serve many
    outcome = fetch_and_scan(
        Request(inp.url, inp.ref, policy, inp.licence_override),
        Tools(storage=storage, scope=scope, clam=clam, http=PolicyHttp(policy)),
    )
    report = outcome.report
    return RepoFetchOutput(
        report_sha256=outcome.report_sha256,
        decision=report.decision,
        commit=report.commit,
        bundle_sha256=report.bundle_sha256,
    )


@activity_for(TaskQueue.BUILD)
@activity.defn(name="fetch_and_scan_repo")
def fetch_and_scan_repo(inp: RepoFetchInput) -> RepoFetchOutput:
    settings = _settings()
    clam = ClamdClient(settings.clamav_host, settings.clamav_port, settings.clamav_timeout_seconds)
    return run(inp, _storage(), clam)
