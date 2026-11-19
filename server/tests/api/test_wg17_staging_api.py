# ruff: noqa: F811 - the fixtures `h` and `client` are imported from the Commit tests
"""WG-17 (#765) the Staging runtime and its FDE-only routes, through the real app and auth chain.

T01 FDE-only access (status, bundle, MCP); T02 another tenant, an unassigned FDE and a Workflow that
was never committed get 404; T03 an undeclared host is blocked and logged (the policy and the proxy,
in tests/unit/test_wg17_staging_runtime.py; here the allowlist is the certified manifest's);
T04 Staging is not destroyed by a time limit and is superseded by a new version; T05 secrets are not
in the trace. Also: the digest is checked against the registry and the manifest, the bundle against
its digest, a start is idempotent and a failed start is recorded.

The deployer is a fake that records what it was asked, the "container" is an httpx mock transport,
Bifrost is a fake and Temporal is never reached: no test here calls a model or a real service.
"""

import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.builds import workflow_ui
from sbam_api.connections.crypto import KeyRing
from sbam_api.db.base import utcnow
from sbam_api.staging import records
from sbam_api.workflow_commit import service as commit_service
from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_contracts.workflow_commit import StagingStartStatus
from sbam_workers.repo_fetch.report import bundle_tar
from sbam_workers.staging.deployer import Deployed, DeployError, DeploySpec
from sbam_workers.staging.runtime import DeployerStagingRuntime, StagingConfig
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_project_workspace import Harness
from test_wg16_connections_api import (
    KEYS,
    LEDGER,
    SECRET,
    WORKFLOW,
    put_value,
    set_test_target,
)
from test_wg16_connections_api import base as connections_base
from test_wg18_commit_api import (  # noqa: F401 - the fixtures and the Commit helpers
    BUNDLE,
    FakeClient,
    FakeRuntime,
    certify,
    client,
    commit_input,
    ctx,
    h,
    run_all,
    seed,
    steps,
    storage,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_wg12_workflow_registry import TOOLS, FakeBifrost


@dataclass
class FakeDeployer:
    """Records what it was asked. `fail` makes the next start raise it."""

    name: str = "fake"
    started: list[DeploySpec] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    running: set[str] = field(default_factory=set)
    fail: DeployError | None = None

    def start(self, spec: DeploySpec) -> Deployed:
        if self.fail is not None:
            raise self.fail
        self.started.append(spec)
        self.running.add(spec.name)
        return Deployed(spec.name, f"http://{spec.name}:8000")

    def stop(self, name: str) -> None:
        self.stopped.append(name)
        self.running.discard(name)

    def is_running(self, name: str) -> bool:
        return name in self.running


def staging_url(h: Harness, tail: str = "", workflow: uuid.UUID = WORKFLOW) -> str:
    return f"/projects/{h.project}/workflows/{workflow}/staging{tail}"


def runtime_for(
    h: Harness, deployer: FakeDeployer, config: StagingConfig | None = None
) -> DeployerStagingRuntime:
    return DeployerStagingRuntime(
        h.sessions,
        storage(h),
        lambda: KeyRing.parse(KEYS, allow_placeholder=True),
        deployer,
        config,
    )


def commit(h: Harness, deployer: FakeDeployer, *, secrets: tuple[str, ...] = ()) -> str:
    """Certify, then run the whole WorkflowCommit with the REAL runtime on a fake deployer."""
    seed(h, *secrets)
    sha = certify(h, recorded=True)
    return sha


def run_commit(h: Harness, sha: str, deployer: FakeDeployer) -> Any:
    return run_all(
        steps(h, FakeBifrost(TOOLS), runtime_for(h, deployer)),  # type: ignore[arg-type]
        commit_input(h, sha),
    )


def container(handler: Any = None) -> list[httpx.Request]:
    """The Staging container as a mock transport; returns the list of requests it received."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if handler is not None:
            return handler(request)  # type: ignore[no-any-return]
        return httpx.Response(406, json={"error": "needs text/event-stream"})

    container.transport = httpx.MockTransport(answer)  # type: ignore[attr-defined]
    return seen


def attach(h: Harness, handler: Any = None) -> list[httpx.Request]:
    seen = container(handler)
    h.client.app.state.staging_transport = container.transport  # type: ignore[attr-defined]
    return seen


def trail(h: Harness, action: str) -> list[AuditLog]:
    with h.sessions() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        return list(rows)


# --- the runtime starts the certified digest ----------------------------------------------------


def test_wg17_commit_starts_the_certified_digest_on_the_staging_network(
    h: Harness, client: FakeClient
) -> None:
    deployer = FakeDeployer()
    sha = commit(h, deployer)
    result = run_commit(h, sha, deployer)
    assert result.staging.status is StagingStartStatus.STARTED
    [spec] = deployer.started
    assert spec.image_digest == result.image_digest == "sha256:" + "a" * 64
    assert spec.network == "sbam-staging" and spec.name.startswith("stg-")
    [row] = trail(h, records.STARTED)
    after = row.after or {}
    assert after["image_digest"] == spec.image_digest and after["egress_hosts"] == [
        "api.example.com"
    ]
    assert after["egress_enforced"] is False  # no proxy configured: no outside access at all
    assert (
        h.call("GET", f"/projects/{h.project}/workflows/{WORKFLOW}/commit", h.fde()).json()[
            "staging"
        ]["status"]
        == "STARTED"
    )


def test_wg17_a_start_again_answers_the_running_container(h: Harness, client: FakeClient) -> None:
    deployer = FakeDeployer()
    sha = commit(h, deployer)
    run_commit(h, sha, deployer)
    again = run_commit(h, sha, deployer)
    assert again.staging.status is StagingStartStatus.STARTED
    assert again.staging.detail == "already running" and len(deployer.started) == 1
    assert len(trail(h, records.STARTED)) == 1


def test_wg17_the_egress_proxy_and_allowlist_reach_the_container_when_configured(
    h: Harness, client: FakeClient, tmp_path: Path
) -> None:
    from sbam_workers.staging.egress_proxy import PolicyDir

    deployer = FakeDeployer()
    sha = commit(h, deployer)
    policies = PolicyDir(tmp_path / "egress")
    config = StagingConfig(proxy_url="http://egress:3128", policy_dir=policies)
    steps_ = steps(h, FakeBifrost(TOOLS), runtime_for(h, deployer, config))  # type: ignore[arg-type]
    run_all(steps_, commit_input(h, sha))
    [spec] = deployer.started
    proxy = spec.plain_env["HTTPS_PROXY"]
    token = proxy.removeprefix("http://").split("@")[0].split(":")[0]
    assert proxy.endswith("@egress:3128") and spec.plain_env["SBAM_EGRESS_ALLOWLIST"]
    found = policies.read(token)
    assert found is not None and found[1].decide("api.example.com").allowed
    assert not found[1].decide("evil.example.org").allowed  # WG17-T03: an undeclared host
    [row] = trail(h, records.STARTED)
    assert (row.after or {})["egress_enforced"] is True


# --- digest checks ----------------------------------------------------------------------------


def _register_only(h: Harness, sha: str) -> None:
    s = steps(h, FakeBifrost(TOOLS), runtime_for(h, FakeDeployer()))  # type: ignore[arg-type]
    inp = commit_input(h, sha)
    s.recheck(inp)
    s.freeze(inp)
    s.register(inp)


def test_wg17_a_digest_that_differs_from_the_manifest_is_refused_and_nothing_starts(
    h: Harness, client: FakeClient
) -> None:
    sha = commit(h, FakeDeployer())
    _register_only(h, sha)
    with h.sessions() as s:
        entry = s.scalars(select(RegisteredWorkflowRow)).one()
        entry.image_digest = "sha256:" + "d" * 64  # the registry no longer says what was certified
        s.commit()
    deployer = FakeDeployer()
    inp = commit_input(h, sha)
    with pytest.raises(ApplicationError) as caught:
        steps(h, FakeBifrost(TOOLS), runtime_for(h, deployer)).start_staging(inp)  # type: ignore[arg-type]
    assert caught.value.type == "StagingStartFailed"
    assert deployer.started == []
    [row] = trail(h, records.FAILED)
    assert "DIGEST_MISMATCH" in (row.after or {})["detail"]
    res = h.call("GET", staging_url(h), h.fde())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "FAILED" and body["current"]["detail"].startswith("DIGEST_MISMATCH")


def test_wg17_a_bundle_that_changed_in_storage_is_not_started_or_served(
    h: Harness, client: FakeClient
) -> None:
    sha = commit(h, FakeDeployer())
    _register_only(h, sha)
    record = workflow_ui.newest(h.sessions(), TENANT_A, WORKFLOW)
    assert record is not None
    path = storage(h)._path(record.tar_key)  # type: ignore[attr-defined]
    path.chmod(0o644)
    path.write_bytes(bundle_tar({**BUNDLE, "app.js": b"console.log(2)"}))
    deployer = FakeDeployer()
    result = runtime_for(h, deployer).start(
        commit_service.staging_request(h.sessions(), ctx(h), WORKFLOW, "fde-a1")
    )
    assert result.status is StagingStartStatus.FAILED and "UI_BUNDLE_CHANGED" in (
        result.detail or ""
    )
    assert deployer.started == []
    res = h.call("GET", staging_url(h, "/ui/index.html"), h.fde())
    problem(res, 409, "CONFLICT")
    assert "UI_BUNDLE_CHANGED" in res.json()["detail"]


def test_wg17_a_deployer_that_cannot_start_is_recorded_and_commit_can_run_again(
    h: Harness, client: FakeClient
) -> None:
    deployer = FakeDeployer(fail=DeployError("IMAGE_MISSING", "this host does not hold it"))
    sha = commit(h, deployer)
    with pytest.raises(ApplicationError) as caught:
        run_commit(h, sha, deployer)
    assert caught.value.type == "StagingStartFailed"
    body = h.call("GET", staging_url(h), h.fde()).json()
    assert body["state"] == "FAILED" and "IMAGE_MISSING" in body["current"]["detail"]
    deployer.fail = None
    again = run_commit(h, sha, deployer)  # the run again finds everything and starts the container
    assert again.staging.status is StagingStartStatus.STARTED
    assert h.call("GET", staging_url(h), h.fde()).json()["state"] == "RUNNING"


def test_wg17_with_no_staging_host_configured_commit_completes_as_pending(
    h: Harness, client: FakeClient
) -> None:
    from sbam_workers.staging.deployer import DisabledDeployer

    sha = commit(h, FakeDeployer())
    steps_ = steps(h, FakeBifrost(TOOLS), runtime_for(h, DisabledDeployer()))  # type: ignore[arg-type]
    result = run_all(steps_, commit_input(h, sha))
    assert result.staging.status is StagingStartStatus.PENDING_RUNTIME
    body = h.call("GET", staging_url(h), h.fde()).json()
    assert body["state"] == "PENDING_RUNTIME" and body["mcp_url"] is None


# --- WG17-T01 / T02: FDE-only access -------------------------------------------------------------


def test_wg17_t01_the_assigned_fde_sees_the_status_the_bundle_and_the_mcp_server(
    h: Harness, client: FakeClient
) -> None:
    deployer = FakeDeployer()
    sha = commit(h, deployer)
    run_commit(h, sha, deployer)
    seen = attach(h)
    body = h.call("GET", staging_url(h), h.fde()).json()
    assert (body["state"], body["health"]) == ("RUNNING", "HEALTHY")
    assert body["current"]["image_digest"] == "sha256:" + "a" * 64
    assert body["ui_url"].endswith(f"/workflows/{WORKFLOW}/staging/ui/")
    assert body["mcp_url"].endswith(f"/workflows/{WORKFLOW}/staging/mcp")
    assert "endpoint" not in json.dumps(body) and "http://stg-" not in json.dumps(body)
    index = h.call("GET", staging_url(h, "/ui/"), h.fde())
    assert index.status_code == 200 and index.content == BUNDLE["index.html"]
    assert "connect-src 'self' https://api.example.com" in index.headers["content-security-policy"]
    assert index.headers["x-content-type-options"] == "nosniff"
    js = h.call("GET", staging_url(h, "/ui/app.js"), h.fde())
    assert js.content == BUNDLE["app.js"] and "javascript" in js.headers["content-type"]
    problem(h.call("GET", staging_url(h, "/ui/nope.js"), h.fde()), 404, "NOT_FOUND")
    problem(
        h.call("GET", staging_url(h, "/ui/%2e%2e/%2e%2e/etc/passwd"), h.fde()), 404, "NOT_FOUND"
    )
    seen.clear()
    mcp = h.call(
        "POST",
        staging_url(h, "/mcp"),
        h.fde(),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json"},
    )
    assert mcp.status_code == 406  # whatever the workflow server answered is passed through
    [forwarded] = seen
    assert forwarded.url.host.startswith("stg-") and forwarded.url.path == "/mcp"
    assert "authorization" not in forwarded.headers  # the FDE's token never reaches the container


def test_wg17_an_admin_cannot_reach_staging(h: Harness, client: FakeClient) -> None:
    deployer = FakeDeployer()
    run_commit(h, commit(h, deployer), deployer)
    attach(h)
    for method, tail in (("GET", ""), ("GET", "/ui/"), ("POST", "/mcp")):
        res = h.call(method, staging_url(h, tail), h.admin_a())
        assert res.status_code in (403, 404)


def test_wg17_t02_other_tenants_unassigned_fdes_and_uncommitted_workflows_get_404(
    h: Harness, client: FakeClient
) -> None:
    seed(h)
    attach(h)
    # Certified but never committed: nothing runs, and the answer does not say anything more.
    problem(h.call("GET", staging_url(h, "/ui/"), h.fde()), 404, "NOT_FOUND")
    problem(h.call("POST", staging_url(h, "/mcp"), h.fde(), json={}), 404, "NOT_FOUND")
    assert h.call("GET", staging_url(h), h.fde()).json()["state"] == "NOT_DEPLOYED"
    deployer = FakeDeployer()
    run_commit(h, certify(h, recorded=True), deployer)
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other_tenant, h.realm.token(FDE_A2)):
        for method, tail in (("GET", ""), ("GET", "/ui/"), ("POST", "/mcp"), ("DELETE", "/mcp")):
            problem(h.call(method, staging_url(h, tail), token), 404, "NOT_FOUND")
    foreign = uuid.uuid4()
    for method, tail in (("GET", ""), ("GET", "/ui/"), ("POST", "/mcp")):
        problem(h.call(method, staging_url(h, tail, foreign), h.fde()), 404, "NOT_FOUND")


def test_wg17_a_container_that_is_not_running_is_409_not_a_hang(
    h: Harness, client: FakeClient
) -> None:
    deployer = FakeDeployer()
    run_commit(h, commit(h, deployer), deployer)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    attach(h, down)
    body = h.call("GET", staging_url(h), h.fde()).json()
    assert (body["state"], body["health"]) == ("RUNNING", "UNREACHABLE")
    problem(h.call("POST", staging_url(h, "/mcp"), h.fde(), json={}), 409, "CONFLICT")


# --- WG17-T04: no time limit; a new version supersedes -------------------------------------------


def test_wg17_t04_staging_is_not_destroyed_by_a_time_limit_and_a_new_version_supersedes_it(
    h: Harness, client: FakeClient
) -> None:
    deployer = FakeDeployer()
    # An older version is still running on this host (the previous Commit's deployment).
    older = records.StartedRecord(
        version="0.0.9",
        registry_name="wf_investor_onboarding_v0_0_9_stg",
        image_digest="sha256:" + "1" * 64,
        manifest_hash="sha256:" + "2" * 64,
        container="stg-old",
        endpoint="http://stg-old:8000",
    )
    sha = commit(h, deployer)
    with h.sessions() as s:
        from sbam_api.audit.repository import record

        record(
            s,
            tenant_id=TENANT_A,
            actor_id="sbam-staging-runtime",
            action=records.STARTED,
            entity_type="workflow",
            entity_id=str(WORKFLOW),
            correlation_id="cid-old",
            after=older.model_dump(mode="json"),
        )
        s.commit()
    deployer.running.add("stg-old")
    run_commit(h, sha, deployer)
    assert deployer.stopped == ["stg-old"]  # the old one was stopped, the new one runs
    body = h.call("GET", staging_url(h), h.fde()).json()
    assert body["state"] == "RUNNING" and body["current"]["version"] == "1.0.0"
    by_version = {d["version"]: d for d in body["history"]}
    assert (
        by_version["0.0.9"]["state"] == "SUPERSEDED"
        and by_version["0.0.9"]["superseded_by"] == "1.0.0"
    )
    assert by_version["0.0.9"]["stopped_at"] is not None  # kept on record, not deleted
    assert [d["version"] for d in body["history"]] == ["1.0.0", "0.0.9"]
    # A year later nothing has stopped it: there is no timer anywhere, only a newer version.
    with h.sessions() as s:
        for row in s.scalars(select(AuditLog).where(AuditLog.action == records.STARTED)):
            row.at = utcnow() - timedelta(days=400)
        s.commit()
    attach(h)
    again = h.call("GET", staging_url(h), h.fde()).json()
    assert again["state"] == "RUNNING" and deployer.stopped == ["stg-old"]
    assert "stg-old" not in deployer.running and len(deployer.running) == 1


def test_wg17_one_deployment_per_version_a_failed_stop_is_retried_by_the_next_commit(
    h: Harness, client: FakeClient
) -> None:
    deployer = FakeDeployer()
    sha = commit(h, deployer)
    with h.sessions() as s:
        from sbam_api.audit.repository import record

        record(
            s,
            tenant_id=TENANT_A,
            actor_id="x",
            action=records.STARTED,
            entity_type="workflow",
            entity_id=str(WORKFLOW),
            correlation_id="cid",
            after=records.StartedRecord(
                version="0.0.9",
                registry_name="wf_investor_onboarding_v0_0_9_stg",
                image_digest="sha256:" + "1" * 64,
                manifest_hash="sha256:" + "2" * 64,
                container="stg-old",
                endpoint="http://stg-old:8000",
            ).model_dump(mode="json"),
        )
        s.commit()

    class Stuck(FakeDeployer):
        block: bool = True

        def stop(self, name: str) -> None:
            if self.block:
                raise DeployError("STOP_FAILED", "daemon busy")
            super().stop(name)

    stuck = Stuck()
    stuck.running.add("stg-old")
    first = run_commit(h, sha, stuck)
    assert first.staging.status is StagingStartStatus.STARTED
    assert "not stopped" in (first.staging.detail or "")
    assert trail(h, records.STOPPED) == []  # nothing is recorded stopped that was not
    stuck.block = False
    run_commit(h, sha, stuck)  # the run again: the new one is running, the old is stopped now
    assert len(trail(h, records.STOPPED)) == 1 and stuck.stopped == ["stg-old"]
    assert len(stuck.started) == 1  # the version itself was not started twice


# --- WG17-T05: secrets are injected at start and are nowhere else --------------------------------


def test_wg17_t05_secrets_reach_the_container_environment_and_nothing_else(
    h: Harness, client: FakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    deployer = FakeDeployer()
    seed(h, LEDGER)
    sha = certify(h, recorded=True)
    assert put_value(h).status_code == 200
    set_test_target(h)
    assert h.call("POST", f"{connections_base(h)}/{LEDGER}/test", h.fde()).json()["outcome"] == "OK"
    run_commit(h, sha, deployer)
    [spec] = deployer.started
    assert spec.env[LEDGER].get_secret_value() == SECRET  # injected at start
    assert SECRET not in repr(spec) and SECRET not in str(spec.env)
    with h.sessions() as s:
        everything = json.dumps(
            [
                [row.action, row.before, row.after, row.actor_id]
                for row in s.scalars(select(AuditLog))
            ],
            default=str,
        )
    assert SECRET not in everything and SECRET not in caplog.text
    [used] = trail(h, "CONNECTION_SECRET_USED")  # the one audited point, name and purpose only
    assert (used.after or {})["name"] == LEDGER and (used.after or {})["purpose"] == "staging-start"
    [started] = trail(h, records.STARTED)
    assert (started.after or {})["secret_names"] == [LEDGER]
    status = h.call("GET", staging_url(h), h.fde())
    assert SECRET not in status.text and status.json()["current"]["secret_names"] == [LEDGER]
    assert SECRET not in h.call("GET", staging_url(h, "/ui/"), h.fde()).text


def test_wg17_a_worker_without_the_keyring_refuses_to_start_with_secrets(
    h: Harness, client: FakeClient
) -> None:
    deployer = FakeDeployer()
    seed(h, LEDGER)
    sha = certify(h, recorded=True)
    put_value(h)
    set_test_target(h)
    h.call("POST", f"{connections_base(h)}/{LEDGER}/test", h.fde())
    keyless = DeployerStagingRuntime(h.sessions, storage(h), lambda: None, deployer)
    s = steps(h, FakeBifrost(TOOLS), keyless)  # type: ignore[arg-type]
    inp = commit_input(h, sha)
    s.recheck(inp)
    s.freeze(inp)
    s.register(inp)
    result = keyless.start(commit_service.staging_request(h.sessions(), ctx(h), WORKFLOW, "u"))
    assert result.status is StagingStartStatus.FAILED and "SECRETS_KEY_MISSING" in (
        result.detail or ""
    )
    assert deployer.started == []
