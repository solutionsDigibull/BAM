"""WG-12 (#760): register a certified, frozen Workflow in Bifrost, supersede, look up (ADR 0028).

The registrar runs against a fake Bifrost and a SQLite database with the real tables. The HTTP
client and the health probe run against `httpx.MockTransport` with the answers the real Bifrost
v2.2.6 gave (checked on a throwaway container for this issue).
"""

import copy
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pipeline_db import pipeline_sessions
from pydantic import AnyHttpUrl, JsonValue
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationLevel, CertificationRow
from sbam_api.core.conventions import ApiError
from sbam_api.core.settings import Settings
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.models import GateResultRow, WorkflowManifestRow
from sbam_api.manifests.ui_bundle import record_ui_bundle_gate
from sbam_api.workflow_registry import health, service
from sbam_api.workflow_registry.bifrost import (
    HttpBifrostRegistry,
    RegistryEntry,
    RegistryUnavailable,
    from_settings,
)
from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_api.workflow_registry.repository import RegisteredWorkflowRepository, version_key
from sbam_api.workflows.models import Workflow
from sbam_contracts.manifest import McpServerEntry, McpToolEntry, UiBundleEntry, WorkflowManifest
from sbam_contracts.workflow_registry import RegisteredWorkflow, RegistryKind, RegistryStatus
from sbam_workers.build.workflow_server import with_server
from sqlalchemy.orm import Session, sessionmaker
from test_manifest import workflow

TENANT = uuid.UUID("6a1f3e2c-0b7d-4c55-9a51-1c2f0a7e4b11")
OTHER_TENANT = uuid.UUID("8d2b9c4e-5f60-4a18-b3c7-2e9d4f1a6c22")
PROJECT = uuid.UUID("0190f3a2-7b1c-7d4e-8f00-000000000001")
WORKFLOW_ID = uuid.UUID("0190f3a2-7b1c-7d4e-8f00-0000000000a1")
CTX = TenantContext(TENANT, PROJECT)
SKILL = "MS01.1_Investor_Auth_Manager"
TOOLS = ("MS01_1__action", "MS01_1__check")
DIGEST = "sha256:" + "a" * 64
NEW_DIGEST = "sha256:" + "b" * 64
UI_DIGEST = "sha256:" + "c" * 64  # the UI bundle (#857)
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
V1_0 = "wf_investor_onboarding_v1_0"
V1_1 = "wf_investor_onboarding_v1_1"


def server_entry(version: str = "1.0.0", digest: str = DIGEST) -> McpServerEntry:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"customer_country": {"type": "string"}},
    }
    return McpServerEntry(
        name="wf_investor_onboarding",
        version=version,
        image_digest=digest,
        tools=[
            McpToolEntry(
                name=name,
                skill_id=SKILL,
                role="action" if name.endswith("action") else "check",
                input_schema=schema,
                output_schema={"type": "object"},
            )
            for name in TOOLS
        ],
    )


def ui_entry(version: str = "1.0.0", digest: str = UI_DIGEST) -> UiBundleEntry:
    return UiBundleEntry(
        version=version,
        digest=digest,
        entry_file="index.html",
        allowed_hosts=["api.example.com"],
        workflow_server="wf_investor_onboarding",
    )


def sealed(
    version: str, digest: str, with_entry: bool = True, ui: UiBundleEntry | None = None
) -> WorkflowManifest:
    base = workflow()
    base["version"] = version
    plain = WorkflowManifest.sealed(base)
    if not with_entry:
        return plain
    built = with_server(plain, server_entry(version, digest))
    bundle = ui if ui is not None else ui_entry(version)
    return WorkflowManifest.sealed({**built.dump(), "ui_bundle": bundle.dump()})


class FakeBifrost:
    """Records every call. `exposed` is what the registered server lists as tools."""

    def __init__(self, exposed: tuple[str, ...] = TOOLS) -> None:
        self.exposed = exposed
        self.listed: dict[str, RegistryEntry] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_disable: set[str] = set()
        self.address = ""

    def register(self, name: str, address: str) -> RegistryEntry:
        self.calls.append(("register", name))
        self.address = address
        entry = self.listed.get(name) or RegistryEntry(f"id-{name}", name, False, self.exposed)
        self.listed[name] = entry
        return entry

    def find(self, name: str) -> RegistryEntry | None:
        return self.listed.get(name)

    def disable(self, entry_id: str) -> None:
        name = next(n for n, e in self.listed.items() if e.id == entry_id)
        self.calls.append(("disable", name))
        if name in self.fail_disable:
            raise RegistryUnavailable("Bifrost did not answer")
        entry = self.listed[name]
        self.listed[name] = RegistryEntry(entry.id, entry.name, True, entry.tools)

    def entries(self) -> list[RegistryEntry]:
        return list(self.listed.values())

    def writes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] in ("register", "disable")]


class World:
    """One Workflow in a SQLite database, and the frozen manifests and certificates it gets."""

    def __init__(self, state: str = "FROZEN") -> None:
        self.sessions: sessionmaker[Session] = pipeline_sessions()
        bind = self.sessions.kw["bind"]
        for model in (
            Workflow,
            WorkflowManifestRow,
            CertificationRow,
            RegisteredWorkflowRow,
            CodeAttempt,  # the foreign key of a gate result
            GateResultRow,
        ):
            model.metadata.create_all(bind, tables=[model.__table__])  # type: ignore[list-item]
        with self.sessions() as session:
            session.add(
                Workflow(
                    id=WORKFLOW_ID,
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    sow_version_id=uuid.uuid4(),
                    major=1,
                    version="1.0.0",
                    name="Investor onboarding",
                    state=state,
                )
            )
            session.commit()

    def freeze(
        self,
        version: str = "1.0.0",
        digest: str = DIGEST,
        *,
        build_no: int = 1,
        certify: bool = True,
        frozen: bool = True,
        server: bool = True,
        ui: UiBundleEntry | None = None,
        ui_gate: bool = True,
    ) -> WorkflowManifest:
        manifest = sealed(version, digest, with_entry=server, ui=ui)
        sha = manifest.integrity.content_sha256
        with self.sessions() as session:
            session.add(
                WorkflowManifestRow(
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    workflow_id=WORKFLOW_ID,
                    build_no=build_no,
                    manifest=json.loads(json.dumps(manifest.dump())),
                    sha256=sha,
                    is_frozen=frozen,
                )
            )
            if certify:
                session.add(certificate(sha))
            if server and ui_gate and manifest.ui_bundle is not None:
                # the UI_BUNDLE evidence of #857: the packaged bundle is what the manifest says
                record_ui_bundle_gate(
                    session, CTX, WORKFLOW_ID, manifest, manifest.ui_bundle.digest
                )
            session.commit()
        return manifest

    def register(self, fake: FakeBifrost, **kwargs: Any) -> RegisteredWorkflow:
        stamp = kwargs.pop("now", NOW)
        kwargs.setdefault("kind", RegistryKind.PRODUCTION)  # WG-26 tests pass STAGING themselves
        with self.sessions() as session:
            done = service.register(
                session, CTX, WORKFLOW_ID, fake, owner="fde-a1", now=stamp, **kwargs
            )
            session.commit()
            return done

    def rows(self) -> list[RegisteredWorkflowRow]:
        with self.sessions() as session:
            rows = RegisteredWorkflowRepository(session, CTX).of_workflow(WORKFLOW_ID)
            session.expunge_all()
            return rows


def certificate(sha: str) -> CertificationRow:
    return CertificationRow(
        tenant_id=TENANT,
        project_id=PROJECT,
        level=CertificationLevel.WORKFLOW.value,
        subject_id=WORKFLOW_ID,
        content_sha256=sha,
        evidence={"manifest_sha256": sha},
        certified_at=NOW,
    )


def detail_of(caught: pytest.ExceptionInfo[Any]) -> str:
    return str(caught.value.detail or "")


# --- WG12-T01: an uncertified server cannot register --------------------------------------------


def test_wg12_t01_a_certified_frozen_workflow_registers_with_its_certified_digest() -> None:
    world, fake = World(), FakeBifrost()
    manifest = world.freeze()
    done = world.register(fake)
    assert done.status is RegistryStatus.REGISTERED and done.version == "1.0.0"
    assert done.image_digest == DIGEST == manifest.dump()["mcp_server"]["image_digest"]
    assert done.manifest_hash == manifest.integrity.content_sha256
    assert done.registry_name == V1_0
    assert [t.name for t in done.tools] == list(TOOLS) and done.tools[0].input_schema
    assert done.owner == "fde-a1" and done.registry_id == f"id-{V1_0}"
    assert fake.address == f"http://{V1_0}:8000/mcp"


@pytest.mark.parametrize(
    ("state", "freeze", "why"),
    [
        ("CERTIFIED", {}, "not FROZEN"),  # certified, not frozen yet
        ("BUILDING", {}, "not FROZEN"),
        ("FROZEN", {"certify": False}, "no WORKFLOW certificate"),
        ("FROZEN", {"frozen": False}, "no frozen manifest"),
        ("FROZEN", {"server": False}, "no MCP server"),
    ],
)
def test_wg12_t01_an_uncertified_server_cannot_register(
    state: str, freeze: dict[str, Any], why: str
) -> None:
    world, fake = World(state), FakeBifrost()
    world.freeze(**freeze)
    with pytest.raises(service.NotRegistrable) as caught:
        world.register(fake)
    assert why in detail_of(caught)
    assert fake.calls == [] and world.rows() == []  # nothing reached Bifrost or the table


def test_wg12_t01_a_certificate_on_another_hash_does_not_count() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze(certify=False)
    with world.sessions() as session:
        session.add(certificate("sha256:" + "9" * 64))
        session.commit()
    with pytest.raises(service.NotRegistrable) as caught:
        world.register(fake)
    assert "no WORKFLOW certificate" in detail_of(caught)
    assert fake.calls == []


def test_wg12_the_server_must_expose_every_promised_tool_or_it_is_disabled_again() -> None:
    world, fake = World(), FakeBifrost(exposed=("MS01_1__action",))
    world.freeze()
    with pytest.raises(service.NotRegistrable) as caught:
        world.register(fake)
    assert "MS01_1__check" in detail_of(caught)
    assert fake.writes() == [("register", V1_0), ("disable", V1_0)]  # nothing uncertified stays
    assert world.rows() == []


def test_wg12_another_tenant_cannot_register_a_workflow_it_does_not_own() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    other = TenantContext(OTHER_TENANT, PROJECT)
    with world.sessions() as session, pytest.raises(ApiError) as caught:
        service.register(session, other, WORKFLOW_ID, fake, kind=RegistryKind.PRODUCTION, owner="x")
    assert caught.value.status == 404 and fake.calls == []


# --- WG12-T02: registering again is a no-op -----------------------------------------------------


def test_wg12_t02_registering_again_is_a_no_op() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    first = world.register(fake)
    second = world.register(fake, now=LATER)
    assert second.model_dump(exclude={"registered_at"}) == first.model_dump(
        exclude={"registered_at"}
    )
    assert len(world.rows()) == 1
    assert fake.writes() == [("register", V1_0)]  # Bifrost was called once


def test_wg12_a_frozen_version_registered_with_another_digest_is_refused() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake)
    world.freeze(digest=NEW_DIGEST, build_no=2)  # same version 1.0.0, another image
    with pytest.raises(service.RegistrationConflict):
        world.register(fake)
    assert fake.writes() == [("register", V1_0)]


# --- WG12-T03: a new version supersedes the old one ---------------------------------------------


def test_wg12_t03_a_new_version_supersedes_the_old_one_and_nothing_is_deleted() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake)
    world.freeze("1.0.1", NEW_DIGEST, build_no=2)
    new = world.register(fake, now=LATER)
    new_row, old_row = world.rows()
    assert new.version == "1.0.1" and new_row.status == "REGISTERED"
    assert old_row.version == "1.0.0" and old_row.status == "SUPERSEDED"
    assert old_row.superseded_by == "1.0.1" and old_row.superseded_at is not None
    assert old_row.superseded_at.replace(tzinfo=None) == LATER.replace(tzinfo=None)
    assert fake.listed[V1_0].disabled is True  # disabled, still listed
    assert fake.listed[V1_1].disabled is False
    assert len(world.rows()) == 2 and len(fake.listed) == 2


def test_wg12_an_older_version_cannot_be_registered_over_a_newer_one() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze("1.0.1", NEW_DIGEST, build_no=2)
    world.register(fake)
    world.freeze("1.0.0", DIGEST, build_no=1)
    with pytest.raises(service.NotRegistrable) as caught:
        world.register(fake, version="1.0.0")
    assert "newer version" in detail_of(caught)


def statuses(world: World) -> dict[str, str]:
    return {row.version: row.status for row in world.rows()}


def test_wg12_a_failed_disable_leaves_the_old_row_current_and_can_be_finished() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake)
    world.freeze("1.0.1", NEW_DIGEST, build_no=2)
    fake.fail_disable.add(V1_0)
    world.register(fake, now=LATER)
    assert statuses(world) == {"1.0.1": "REGISTERED", "1.0.0": "REGISTERED"}
    fake.fail_disable.clear()
    with world.sessions() as session:
        repo = RegisteredWorkflowRepository(session, CTX)
        current = repo.of_version(WORKFLOW_ID, "1.0.1")
        assert current is not None
        assert service.supersede_older(repo, fake, current, LATER) == []
        session.commit()
    assert statuses(world) == {"1.0.1": "REGISTERED", "1.0.0": "SUPERSEDED"}


# --- WG12-T04: the lookup returns the certified digest ------------------------------------------


def lookup(
    world: World, version: str | None = None, ctx: TenantContext = CTX
) -> RegisteredWorkflow:
    with world.sessions() as session:
        return service.lookup(session, ctx, WORKFLOW_ID, version)


def test_wg12_t04_lookup_returns_the_digest_that_was_certified() -> None:
    world, fake = World(), FakeBifrost()
    manifest = world.freeze()
    world.register(fake)
    found = lookup(world, "1.0.0")
    assert found.image_digest == manifest.dump()["mcp_server"]["image_digest"]
    assert found.manifest_hash == manifest.integrity.content_sha256
    assert lookup(world) == found  # no version: the current one


def test_wg12_lookup_with_no_version_is_the_newest_enabled_and_the_old_one_stays_findable() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake)
    world.freeze("1.0.1", NEW_DIGEST, build_no=2)
    world.register(fake, now=LATER)
    assert lookup(world).version == "1.0.1"
    old = lookup(world, "1.0.0")
    assert old.status is RegistryStatus.SUPERSEDED and old.superseded_by == "1.0.1"
    assert old.image_digest == DIGEST  # a running SRIM still finds its own version


def test_wg12_lookup_of_an_unregistered_version_or_another_tenant_is_404() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake)
    calls: list[Callable[[], RegisteredWorkflow]] = [
        lambda: lookup(world, "9.0.0"),
        lambda: lookup(world, ctx=TenantContext(OTHER_TENANT, PROJECT)),
    ]
    for call in calls:
        with pytest.raises(ApiError) as caught:
            call()
        assert caught.value.status == 404


def test_wg12_nothing_in_the_registry_can_delete_a_row() -> None:
    assert not hasattr(RegisteredWorkflowRepository, "delete")
    assert not hasattr(service, "delete")


# --- names and versions -------------------------------------------------------------------------


def test_wg12_registry_names_are_underscores_only_and_at_most_64() -> None:
    name = service.registry_name_for("wf_investor_onboarding", "2.0.7")
    assert name == "wf_investor_onboarding_v2_7"
    long = service.registry_name_for("wf_" + "a" * 70, "12.0.345")
    assert len(long) <= 64 and long.endswith("_v12_345") and "-" not in long


def test_wg12_versions_sort_by_major_then_patch() -> None:
    assert sorted(["1.0.10", "2.0.0", "1.0.2"], key=version_key) == ["1.0.2", "1.0.10", "2.0.0"]


# --- reconcile ----------------------------------------------------------------------------------


def test_wg12_reconcile_rebuilds_a_lost_entry_from_the_table() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake)
    fake.listed.clear()  # Bifrost's database was lost
    with world.sessions() as session:
        report = service.reconcile(session, CTX, fake)
        session.commit()
    assert report.reregistered == [V1_0] and V1_0 in fake.listed


def test_wg12_reconcile_reports_a_current_entry_that_was_disabled_by_hand() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake)
    fake.disable(f"id-{V1_0}")
    with world.sessions() as session:
        report = service.reconcile(session, CTX, fake)
    assert report.drift == [V1_0] and report.reregistered == []


# --- the Bifrost HTTP client (answers as the real v2.2.6 gave them) -----------------------------


def listing(*clients: dict[str, Any], total: int | None = None) -> dict[str, Any]:
    return {"clients": list(clients), "count": len(clients), "total_count": total or len(clients)}


def client_json(name: str, cid: str, disabled: bool = False) -> dict[str, Any]:
    return {
        "config": {"client_id": cid, "name": name, "disabled": disabled},
        "tools": [{"name": tool, "parameters": {"type": "object"}} for tool in TOOLS],
        "state": "disabled" if disabled else "healthy",
    }


Handler = Callable[[httpx.Request], httpx.Response]


def http_registry(
    handler: Handler, **kwargs: Any
) -> tuple[HttpBifrostRegistry, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    registry = HttpBifrostRegistry(
        "http://bifrost:8080/", "tok-123", client, sleep=lambda _: None, **kwargs
    )
    return registry, seen


def test_wg12_register_posts_the_server_and_reads_the_id_back_from_the_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"message": "connected", "status": "success"})
        return httpx.Response(200, json=listing(client_json("wf_x_v1_0", "cid-1")))

    registry, seen = http_registry(handler)
    entry = registry.register("wf_x_v1_0", "http://wf_x_v1_0:8000/mcp")
    assert entry == RegistryEntry("cid-1", "wf_x_v1_0", False, TOOLS)
    post = seen[0]
    assert post.url.path == "/api/mcp/client"
    assert post.headers["X-Bifrost-Setup-Token"] == "tok-123"
    assert json.loads(post.content) == {
        "name": "wf_x_v1_0",
        "connection_type": "http",
        "connection_string": "http://wf_x_v1_0:8000/mcp",
        "auth_type": "none",
        "tools_to_execute": ["*"],
    }


def test_wg12_a_409_on_register_is_the_idempotent_case_not_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": {"message": "slug already exists"}})
        return httpx.Response(200, json=listing(client_json("wf_x_v1_0", "cid-1")))

    registry, _ = http_registry(handler)
    assert registry.register("wf_x_v1_0", "http://a/mcp").id == "cid-1"


def test_wg12_disable_puts_disabled_true_and_nothing_is_ever_deleted() -> None:
    registry, seen = http_registry(lambda r: httpx.Response(200, json={"status": "success"}))
    registry.disable("cid-1")
    assert (seen[0].method, seen[0].url.path) == ("PUT", "/api/mcp/client/cid-1")
    assert json.loads(seen[0].content) == {"disabled": True}
    assert not hasattr(registry, "delete")


def test_wg12_a_locked_database_is_retried_then_succeeds() -> None:
    answers = iter([500, 500, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(answers)
        body = {"error": {"message": "database is locked"}} if code == 500 else {"status": "ok"}
        return httpx.Response(code, json=body)

    registry, seen = http_registry(handler)
    registry.disable("cid-1")
    assert len(seen) == 3


def test_wg12_a_bifrost_that_keeps_failing_is_unavailable_without_leaking_the_token() -> None:
    registry, seen = http_registry(lambda r: httpx.Response(500, json={"error": "x"}), attempts=3)
    with pytest.raises(RegistryUnavailable) as caught:
        registry.disable("cid-1")
    assert len(seen) == 3 and "tok-123" not in detail_of(caught)
    assert caught.value.status == 503


def test_wg12_a_refused_token_is_not_retried_and_is_not_echoed() -> None:
    registry, seen = http_registry(lambda r: httpx.Response(403, json={"error": "invalid"}))
    with pytest.raises(RegistryUnavailable) as caught:
        registry.entries()
    assert "refused the setup token" in detail_of(caught)
    assert len(seen) == 1 and "tok-123" not in detail_of(caught)


def test_wg12_a_connection_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    registry, seen = http_registry(handler, attempts=2)
    with pytest.raises(RegistryUnavailable) as caught:
        registry.entries()
    assert "did not answer" in detail_of(caught) and len(seen) == 2


def test_wg12_the_list_is_read_page_by_page() -> None:
    pages = {
        0: listing(client_json("wf_a_v1_0", "1"), client_json("wf_b_v1_0", "2"), total=3),
        2: listing(client_json("wf_c_v1_0", "3"), total=3),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[int(request.url.params["offset"])])

    registry, seen = http_registry(handler)
    assert [e.name for e in registry.entries()] == ["wf_a_v1_0", "wf_b_v1_0", "wf_c_v1_0"]
    assert len(seen) == 2
    assert registry.find("wf_b_v1_0") is not None and registry.find("nope") is None


def test_wg12_no_setup_token_means_nothing_is_registered(tmp_path: Path) -> None:
    settings = Settings(
        database_url="postgresql://x",
        storage_root=tmp_path,
        temporal_host="t:7233",
        keycloak_issuer=AnyHttpUrl("http://keycloak/realms/bullm"),
        keycloak_audience="sbam-api",
    )
    with pytest.raises(RegistryUnavailable) as caught:
        from_settings(settings)
    assert "not configured" in detail_of(caught)


# --- SBAM's own health check --------------------------------------------------------------------


def mcp_server(tools: tuple[str, ...], *, sse: bool) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "initialize":
            reply = {"jsonrpc": "2.0", "id": 1, "result": {}}
            return httpx.Response(200, headers={"mcp-session-id": "s1"}, json=reply)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert request.headers["mcp-session-id"] == "s1"
        result = {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": t} for t in tools]}}
        if sse:
            stream = f"event: message\ndata: {json.dumps(result)}\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=stream)
        return httpx.Response(200, json=result)

    return handler


@pytest.mark.parametrize("sse", [False, True])
def test_wg12_the_probe_lists_the_tools_over_json_or_an_event_stream(sse: bool) -> None:
    transport = httpx.MockTransport(mcp_server(TOOLS, sse=sse))
    probe = health.McpProbe(httpx.Client(transport=transport))
    assert probe.tool_names("http://wf:8000/mcp") == TOOLS


class Down:
    def tool_names(self, address: str) -> tuple[str, ...]:
        raise health.ProbeError("down")


class Partial:
    def tool_names(self, address: str) -> tuple[str, ...]:
        return ("MS01_1__action",)


class Full:
    def tool_names(self, address: str) -> tuple[str, ...]:
        return TOOLS


def test_wg12_the_probe_says_unhealthy_when_a_promised_tool_is_missing_or_it_is_down() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake)
    rows = world.rows()
    assert health.check(rows, Full()) == {V1_0: True}
    assert health.check(rows, Partial()) == {V1_0: False}
    assert health.check(rows, Down()) == {V1_0: False}


def test_wg12_the_manifest_with_its_server_round_trips() -> None:
    manifest = sealed("1.0.0", DIGEST)
    again = WorkflowManifest.model_validate_json(json.dumps(copy.deepcopy(manifest.dump())))
    assert again.mcp_server is not None and again.mcp_server.image_digest == DIGEST
