# ruff: noqa: E501 - a test-only driver: long status lines and comments are easier to read unwrapped
"""TEST ONLY (WG-31, #908): the live Staging proof, run inside `worker-certify` of the staging-runtime stack.

    docker compose ... exec -T -e SEED_USER_PASSWORD worker-certify \
        python /app/server/tests/staging_proof/driver.py --issuer http://localhost:18080/realms/bullm \
        --image-v1 sha256:<id> --image-v2 sha256:<id>

`scripts/staging-proof.sh` builds the two images on the Docker host, runs this, then checks the host side
(the egress proxy's log, the old container stopped).

What is real: Keycloak logins, the API and its auth chain, the database, the storage volume, the real
`DeployerStagingRuntime` + `DockerCliDeployer` talking to the daemon through the socket proxy, the Staging
network and the egress proxy. What is faked, because the product cannot do it yet (and never in production code):

* the WORKFLOW certificate row (#130 is not merged; PR #902 is open). Seeded here, once per version.
* the Workflow server: `workflow_server.py`, not a generated one.

Commit itself is real since #909 (WG-32): the driver starts the `WorkflowCommit` workflow on Temporal and the real
worker runs recheck, freeze, start Staging and register (the real Bifrost, at the container's own address). There
is no stand-in registry any more.

The output is one `CHECK <id> PASS|FAIL <detail>` line per check and a final `RESULT {json}` line. No token, key
or password is printed. Exit 1 when any check fails.
"""

import argparse
import asyncio
import base64
import hashlib
import html
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

sys.path.insert(0, "/app/server/tests")  # the `fixtures` package (golden manifests)

from fixtures import fixture_path
from sbam_api.audit import repository as audit
from sbam_api.builds import workflow_ui
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationLevel, CertificationRow
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.manifests.models import WorkflowManifestRow
from sbam_api.manifests.ui_bundle import record_ui_bundle_gate
from sbam_api.projects.models import Project
from sbam_api.sow.models import Sow, SowVersion
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_api.workflows.models import Workflow
from sbam_contracts.canonical import canonical_json
from sbam_contracts.manifest import (
    McpServerEntry,
    McpToolEntry,
    TemplateRef,
    UiBundleEntry,
    WorkflowManifest,
)
from sbam_contracts.workflow_commit import StagingStartStatus
from sbam_workers import mirror
from sbam_workers.commit.activities import make_runtime
from sbam_workers.repo_fetch.report import bundle_tar
from sbam_workers.settings import WorkerSettings
from sbam_workers.staging.runtime import DeployerStagingRuntime

HERE = Path(__file__).resolve().parent
TOOLS = ("MS01_1__action", "MS01_1__check")
SKILL = "MS01.1_Investor_Auth_Manager"
ALLOWED_HOST = "api.example.com"
BLOCKED_HOST = "example.org"
RESULTS: list[tuple[str, bool, str]] = []


def check(case: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((case, ok, detail))
    print(f"CHECK {case} {'PASS' if ok else 'FAIL'} {detail}"[:400], flush=True)
    return ok


# --- Keycloak: the browser's authorization-code + PKCE login, as the integration tests do ---------------------


def forward(port: int, host: str, target: int) -> None:
    """The tokens carry the issuer `http://localhost:<port>`: make that name reach Keycloak in here."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(host, target)
        except OSError:
            writer.close()
            return

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

    async def serve() -> None:
        server = await asyncio.start_server(handle, "127.0.0.1", port)
        async with server:
            await server.serve_forever()

    threading.Thread(target=lambda: asyncio.run(serve()), daemon=True).start()
    time.sleep(0.5)


def login(issuer: str, web_port: int, client: str, username: str, password: str) -> str:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    callback = (
        f"http://localhost:{web_port}/api/auth/callback/keycloak-{client.removeprefix('sbam-')}"
    )
    cookies: dict[str, str] = {}
    http = httpx.Client(timeout=30)

    def send(method: str, url: str, **kwargs: Any) -> httpx.Response:
        http.cookies.clear()
        header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        answer = http.request(method, url, headers={"Cookie": header} if header else {}, **kwargs)
        for line in answer.headers.get_list(
            "set-cookie"
        ):  # the cookies are `Secure`: carry them by hand
            name, _, rest = line.partition("=")
            cookies[name.strip()] = rest.split(";", 1)[0]
        return answer

    query = {
        "client_id": client,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": callback,
        "state": secrets.token_hex(8),
        "code_challenge": challenge.decode(),
        "code_challenge_method": "S256",
    }
    page = send("GET", f"{issuer}/protocol/openid-connect/auth", params=query)
    code = ""
    for _ in range(4):  # identity-first: the username page, then the password page
        form = re.search(r'<form[^>]*action="([^"]+)"', page.text)
        if page.status_code != 200 or not form:
            break
        page = send(
            "POST", html.unescape(form[1]), data={"username": username, "password": password}
        )
        location = page.headers.get("location", "")
        if location.startswith(callback):
            code = parse_qs(urlsplit(location).query)["code"][0]
            break
    if not code:
        words = " ".join(re.sub(r"<[^>]+>", " ", page.text).split())
        where = page.headers.get("location", "")[:120]
        raise SystemExit(
            f"login for {username} never reached the callback ({page.status_code} {where} {words[-300:]})"
        )
    token = send(
        "POST",
        f"{issuer}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback,
            "client_id": client,
            "code_verifier": verifier,
        },
    )
    token.raise_for_status()
    http.close()
    return str(token.json()["access_token"])


# --- the committed state, as a Commit leaves it (what #908 cannot produce yet is marked above) ----------------


def workflow_manifest(version: str) -> dict[str, Any]:
    """The golden Workflow manifest of the unit tests (tests/unit/test_manifest.py), as a dict."""
    skill = json.loads(fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8"))
    entry = {
        "skill_id": skill["skill_metadata"]["skill_id"],
        "deterministic_hash": skill["governance"]["deterministic_hash"],
        "manifest_sha256": "a" * 64,
        "bundle_sha256": "b" * 64,
        "certification_id": "cert-ms01-1",
    }
    return {
        "schema_version": "sbam-workflow-manifest/1.0",
        "ids": {
            "tenant_id": "tnt-alpha",
            "project_id": "prj-investor-portal",
            "workflow_id": "wf-001",
        },
        "name": "Investor Portal",
        "objective": "Secure investor onboarding and access",
        "version": version,
        "previous_version": None,
        "provenance": skill["sbam_build_record"]["provenance"],
        "isatvon_framework_version": "1.4",
        "macro_skills": [
            {
                "macro_category": "MS01_Investor_Registration_Access",
                "name": "Investor Registration & Access",
                "agent_ref": "AG-AUTH",
                "certification_id": "cert-ms01",
                "skills": [entry],
            }
        ],
        "schema_sql": {"artifact_key": "wf-001/schema.sql", "sha256": "c" * 64},
        "dag": {"nodes": [{"id": "MS01"}], "edges": []},
        "runtime_image_digest": "sha256:" + "d" * 64,
        "evidence_index": [
            {
                "level": "skill",
                "subject": "MS01.1",
                "certification_id": "cert-ms01-1",
                "sha256": "a" * 64,
            }
        ],
        "certification": {
            "state": "FROZEN",
            "certified_at": "2026-06-29T12:00:00Z",
            "frozen_at": None,
        },
        "integrity": {"content_sha256": "sha256:" + "0" * 64},
    }


def ui_files(version: str) -> dict[str, bytes]:
    files = {p.name: p.read_bytes() for p in (HERE / "ui").iterdir() if p.is_file()}
    files["index.html"] = files["index.html"].replace(
        b"Staging</header>", f"Staging {version}</header>".encode()
    )
    return files


class Seed:
    def __init__(self, tenant: uuid.UUID, project: uuid.UUID, actor: str) -> None:
        self.tenant, self.project, self.actor = tenant, project, actor
        self.ctx = TenantContext(tenant, project)
        self.sessions = mirror.sessions()
        self.storage = make_backend(WorkerSettings())  # type: ignore[call-arg]
        self.workflow = uuid.uuid4()
        self.builds = 0
        self.hashes: dict[str, str] = {}
        self.server_name = f"wf_proof_{uuid.uuid4().hex[:8]}"  # registry names are unique per tenant: repeatable runs

    def create_workflow(self, version: str) -> None:
        with self.sessions() as s:
            knowledge = KnowledgeVersion(
                tenant_id=self.tenant,
                project_id=self.project,
                version=1,
                run_id=uuid.uuid4(),
                cognee_dataset="staging-proof",
                state="READY",
            )
            sow = Sow(tenant_id=self.tenant, project_id=self.project)
            s.add_all([knowledge, sow])
            s.flush()
            sow_version = SowVersion(
                tenant_id=self.tenant,
                project_id=self.project,
                sow_id=sow.id,
                version=1,
                source="GENERATED",
                run_id=uuid.uuid4(),
                knowledge_version_id=knowledge.id,
                state="APPROVED",
            )
            s.add(sow_version)
            s.flush()
            s.add(
                Workflow(
                    id=self.workflow,
                    tenant_id=self.tenant,
                    project_id=self.project,
                    sow_version_id=sow_version.id,
                    major=1,
                    version=version,
                    name="Investor Portal (Staging proof)",
                    created_by="SYSTEM",  # the workbench lists Workflows through a contract that requires it
                    state="FROZEN",
                )
            )
            s.flush()
            macro = MacroSkill(  # TEST ONLY: the certified Sub-Skill the Commit checks read
                tenant_id=self.tenant,
                project_id=self.project,
                workflow_id=self.workflow,
                sow_version_id=sow_version.id,
                macro_category="MS01_Investor_Registration_Access",
                name="Investor Registration & Access",
                description="proof",
                order=1,
                sow_refs=[],
                state="FROZEN",
            )
            s.add(macro)
            s.flush()
            sub = SubSkill(
                tenant_id=self.tenant,
                project_id=self.project,
                macro_skill_id=macro.id,
                workflow_id=self.workflow,
                skill_id=SKILL,
                skill_name="Investor Auth Manager",
                order=1,
                isatvon={
                    "skill_metadata": {"skill_id": SKILL, "skill_name": "Investor Auth Manager"},
                    "isatvon_definition": {"v_variables": {"secrets": []}},
                },
                sow_refs=[],
                content_sha256="0" * 64,
                state="FROZEN",
            )
            s.add(sub)
            s.flush()
            s.add(
                CodeAttempt(
                    tenant_id=self.tenant,
                    project_id=self.project,
                    workflow_id=self.workflow,
                    sub_skill_id=sub.id,
                    build_no=1,
                    cycle=0,
                    attempt=0,
                    template_name="proof",
                    template_version="1.0.0",
                    template_sha256="0" * 64,
                    status="ASSEMBLED",
                    bodies={},
                    trace_id="proof",
                    bundle_sha256="e" * 64,
                )
            )
            s.add(
                CertificationRow(
                    tenant_id=self.tenant,
                    project_id=self.project,
                    level=CertificationLevel.SUB_SKILL.value,
                    subject_id=sub.id,
                    content_sha256="sha256:" + "e" * 64,
                    evidence={"seeded_by": "staging-proof (test only)"},
                    certified_at=datetime.now(UTC),
                )
            )
            project = s.get(
                Project, self.project
            )  # a project that has a workflow, as the workbench reads it
            if project is not None:
                project.state = "WORKFLOW_CREATED"
            s.commit()

    def freeze_version(self, version: str, image_digest: str) -> str:
        """A frozen, certified manifest with its built server and UI bundle; returns its hash."""
        self.builds += 1
        files = ui_files(version)
        digests = {p: hashlib.sha256(d).hexdigest() for p, d in sorted(files.items())}
        ui_digest = "sha256:" + hashlib.sha256(canonical_json(digests)).hexdigest()
        stored = self.storage.put(
            bundle_tar(files),
            StorageScope(self.tenant, self.project, self.workflow),
            Kind.CODE,
            "application/x-tar",
        )
        ui_entry = UiBundleEntry(
            version=version,
            digest=ui_digest,
            entry_file="index.html",
            allowed_hosts=[ALLOWED_HOST],
            workflow_server=self.server_name,
        )
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"customer_country": {"type": "string"}},
        }
        server = McpServerEntry(
            name=self.server_name,
            version=version,
            image_digest=image_digest,
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
        sql_bytes = f"-- staging proof schema {version}\n".encode()
        sql = self.storage.put(  # the freeze reads every stored file back: the schema must really exist
            sql_bytes,
            StorageScope(self.tenant, self.project, self.workflow),
            Kind.CODE,
            "application/sql",
        )
        base = WorkflowManifest.sealed(
            {
                **workflow_manifest(version),
                "schema_sql": {
                    "artifact_key": sql.storage_key,
                    "sha256": hashlib.sha256(sql_bytes).hexdigest(),
                },
            }
        )
        sealed = WorkflowManifest.sealed(
            {**base.dump(), "mcp_server": server.dump(), "ui_bundle": ui_entry.dump()}
        )
        sha = sealed.integrity.content_sha256

        record = workflow_ui.WorkflowUiRecord(
            entry=ui_entry,
            template=TemplateRef(name="workflow-ui", version="1.0.0", sha256="a" * 64),
            tar_key=stored.storage_key,
            files=digests,
        )
        with self.sessions() as s:
            s.add(
                WorkflowManifestRow(
                    tenant_id=self.tenant,
                    project_id=self.project,
                    workflow_id=self.workflow,
                    build_no=self.builds,
                    manifest=json.loads(json.dumps(sealed.dump())),
                    sha256=sha,
                    is_frozen=True,
                )
            )
            s.add(  # TEST ONLY: the WORKFLOW certificate producer (#130) is not merged
                CertificationRow(
                    tenant_id=self.tenant,
                    project_id=self.project,
                    level=CertificationLevel.WORKFLOW.value,
                    subject_id=self.workflow,
                    content_sha256=sha,
                    evidence={"seeded_by": "staging-proof (test only)"},
                    certified_at=datetime.now(UTC),
                )
            )
            audit.record(
                s,
                tenant_id=self.tenant,
                actor_id="SYSTEM",
                action=workflow_ui.BUILT_ACTION,
                entity_type="workflow",
                entity_id=str(self.workflow),
                correlation_id=f"proof-{version}",
                after=record.model_dump(mode="json"),
            )
            record_ui_bundle_gate(s, self.ctx, self.workflow, sealed, record.digest)
            s.commit()
        return str(sha)

    def commit(self, api: "Api", token: str, base: str, version: str) -> dict[str, Any]:
        """A real Commit, as the FDE does it: `POST .../commit` with the approved hash. The API starts
        `WorkflowCommit` on Temporal and the real worker runs recheck, freeze, start Staging and register
        (the real Bifrost, at the container's own address). Waits for the run; returns the commit status."""
        status = api.call("GET", f"{base}/commit", token).json()
        approved = str(status["manifest_hash"])
        accepted = api.call(
            "POST",
            f"{base}/commit",
            token,
            headers={"If-Match": f'"{approved}"', "Idempotency-Key": uuid.uuid4().hex},
        )
        if accepted.status_code != 202:
            print(f"commit {version} refused: {accepted.status_code} {accepted.text[:600]}", flush=True)
            return {"state": f"HTTP {accepted.status_code}"}
        self.hashes[version] = approved
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline:
            status = api.call("GET", f"{base}/commit", token).json()
            run = status.get("run") or {}
            if run.get("state") in ("COMPLETED", "FAILED", "CANCELLED", "TERMINATED"):
                break
            time.sleep(2)
        run = status.get("run") or {}
        print(
            f"commit {version}: run {run.get('state')} stage={run.get('current_stage')} "
            f"error={run.get('last_error')}",
            flush=True,
        )
        return status  # type: ignore[no-any-return]


def bifrost_entry(name: str) -> dict[str, Any] | None:
    """The raw Bifrost entry of `name` (its tools and the address Bifrost connects to)."""
    base = str(WorkerSettings().bifrost_url).rstrip("/")  # type: ignore[call-arg]
    token = os.environ["BIFROST_SETUP_TOKEN"]
    found = httpx.get(
        f"{base}/api/mcp/clients",
        params={"limit": 100},
        headers={"X-Bifrost-Setup-Token": token},
        timeout=30,
    )
    found.raise_for_status()
    for item in found.json().get("clients", []):
        if item.get("config", {}).get("name") == name:
            entry: dict[str, Any] = item
            return entry
    return None


# --- the checks ------------------------------------------------------------------------------------------------


class Api:
    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=f"{base}/api/v1", timeout=30)

    def call(self, method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
        headers = {"authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        return self.http.request(method, path, headers=headers, **kwargs)


def wait_running(api: Api, path: str, token: str, version: str) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for _ in range(40):
        answer = api.call("GET", path, token)
        body = answer.json() if answer.status_code == 200 else {}
        current = body.get("current") or {}
        if (
            body.get("state") == "RUNNING"
            and current.get("version") == version
            and body.get("health") == "HEALTHY"
        ):
            return body
        time.sleep(1.5)
    return body


def mcp(api: Api, path: str, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    answer = api.call(
        "POST",
        f"{path}/mcp",
        token,
        headers={"content-type": "application/json", "accept": "application/json"},
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ),
    )
    if answer.status_code != 200:
        return {"http": answer.status_code}
    content: dict[str, Any] = answer.json()["result"]["content"][0]
    return content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--issuer",
        required=True,
        help="the browser-facing issuer, http://localhost:<port>/realms/bullm",
    )
    parser.add_argument("--api", default="http://sbam-api:8000")
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument("--image-v1", required=True)
    parser.add_argument("--image-v2", required=True)
    args = parser.parse_args()
    password = os.environ["SEED_USER_PASSWORD"]
    port = urlsplit(args.issuer).port or 8080
    forward(port, "keycloak", 8080)

    token = {
        "admin": login(args.issuer, args.web_port, "sbam-admin", "admin-a", password),
        "fde": login(args.issuer, args.web_port, "sbam-fde", "fde-a1", password),
        "fde2": login(args.issuer, args.web_port, "sbam-fde", "fde-a2", password),
        "other": login(args.issuer, args.web_port, "sbam-fde", "fde-b1", password),
    }
    api = Api(args.api)
    me = {name: api.call("GET", "/me", tok).json() for name, tok in token.items()}
    tenant = uuid.UUID(me["fde"]["tenant"]["id"])
    fde_id = me["fde"]["user"]["id"]

    project = api.call(
        "POST",
        "/admin/projects",
        token["admin"],
        json={"name": f"Staging proof {uuid.uuid4().hex[:8]}"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    project.raise_for_status()
    project_id = uuid.UUID(project.json()["id"])
    api.call(
        "PUT", f"/admin/projects/{project_id}/members", token["admin"], json={"user_ids": [fde_id]}
    ).raise_for_status()

    seed = Seed(tenant, project_id, fde_id)
    seed.create_workflow("1.0.0")
    runtime = make_runtime(WorkerSettings(), seed.sessions, seed.storage)  # type: ignore[call-arg]
    ok = isinstance(runtime, DeployerStagingRuntime)
    check("WG31-T00", ok, f"runtime={type(runtime).__name__}")
    if not isinstance(runtime, DeployerStagingRuntime):
        return 1  # STAGING_RUNTIME is not docker: the profile is not loaded

    seed.freeze_version("1.0.0", args.image_v1)
    base = f"/projects/{project_id}/workflows/{seed.workflow}"
    committed = seed.commit(api, token["fde"], base, "1.0.0")
    registration = committed.get("registration") or {}
    staging_result = committed.get("staging") or {}
    started = f"{staging_result.get('status')} {staging_result.get('detail') or ''}".strip()
    check("WG31-T02a", started.startswith(StagingStartStatus.STARTED.value), started)
    registry_name = str(registration.get("registry_name", ""))
    check(
        "WG32-T01a",
        committed.get("state") == "COMMITTED"
        and registry_name.endswith("_stg")
        and registration.get("image_digest") == args.image_v1,
        f"Commit {committed.get('state')}: {registry_name} on {str(registration.get('image_digest'))[:19]}",
    )
    entry = bifrost_entry(registry_name) if registry_name else None
    listed = sorted(str(t.get("name")) for t in (entry or {}).get("tools", []))
    shown = ((entry or {}).get("config") or {}).get("connection_string", "")
    address = str(shown.get("value", "") if isinstance(shown, dict) else shown)
    container = f"stg-{seed.workflow.hex[:8]}-{registry_name.replace('_', '-')}"[:63]
    check(
        "WG32-T02",
        listed == sorted(TOOLS) and (not address or container in address),
        f"Bifrost lists {listed} at {address or '(address not shown by Bifrost)'}",
    )

    status = wait_running(api, f"{base}/staging", token["fde"], "1.0.0")
    check(
        "WG31-T02",
        status.get("state") == "RUNNING" and status.get("health") == "HEALTHY",
        json.dumps({k: status.get(k) for k in ("state", "health")}),
    )
    check(
        "WG31-T02b",
        bool((status.get("current") or {}).get("egress_enforced")),
        "egress proxy enforced",
    )

    page = api.call("GET", f"{base}/staging/ui/index.html", token["fde"])
    check(
        "WG31-T02c",
        page.status_code == 200 and b"Staging 1.0.0" in page.content,
        f"ui {page.status_code} csp={'default-src' in page.headers.get('content-security-policy', '')}",
    )
    asset = api.call("GET", f"{base}/staging/ui/app.js", token["fde"])
    check(
        "WG31-T02d",
        asset.status_code == 200 and b'fetch("/mcp"' in asset.content,
        f"app.js {asset.status_code}",
    )
    for who in ("fde2", "other"):
        page2 = api.call("GET", f"{base}/staging/ui/index.html", token[who])
        denied = api.call("GET", f"{base}/staging", token[who])
        check(
            f"WG31-T02e-{who}",
            page2.status_code == 404 and denied.status_code == 404,
            f"ui={page2.status_code} status={denied.status_code}",
        )

    echo = mcp(api, f"{base}/staging", token["fde"], "echo", {"customer_country": "DE"})
    check("WG31-T02f", echo.get("version") == "1.0.0", f"mcp echo -> {json.dumps(echo)}")
    forbidden = api.call("POST", f"{base}/staging/mcp", token["fde2"], content=b"{}")
    check("WG31-T02g", forbidden.status_code == 404, f"other FDE mcp={forbidden.status_code}")

    blocked = mcp(api, f"{base}/staging", token["fde"], "probe", {"host": BLOCKED_HOST})
    allowed = mcp(api, f"{base}/staging", token["fde"], "probe", {"host": ALLOWED_HOST})
    check(
        "WG31-T03",
        blocked.get("status") == 403,
        f"undeclared host {BLOCKED_HOST} -> {blocked.get('status')}",
    )
    check(
        "WG31-T03b",
        allowed.get("status") != 403,
        f"declared host {ALLOWED_HOST} -> {allowed.get('status')} (not refused by policy)",
    )

    seed.freeze_version("1.0.1", args.image_v2)
    committed2 = seed.commit(api, token["fde"], base, "1.0.1")
    result2 = committed2.get("staging") or {}
    started2 = f"{result2.get('status')} {result2.get('detail') or ''}".strip()
    check("WG31-T04a", started2.startswith(StagingStartStatus.STARTED.value), started2)
    status2 = wait_running(api, f"{base}/staging", token["fde"], "1.0.1")
    history = {d["version"]: d for d in status2.get("history", [])}
    old = history.get("1.0.0", {})
    check(
        "WG31-T04",
        status2.get("state") == "RUNNING"
        and old.get("state") == "SUPERSEDED"
        and old.get("superseded_by") == "1.0.1",
        f"current={(status2.get('current') or {}).get('version')} old={old.get('state')} by={old.get('superseded_by')}",
    )
    echo2 = mcp(api, f"{base}/staging", token["fde"], "echo", {})
    check("WG31-T04b", echo2.get("version") == "1.0.1", f"mcp echo -> {json.dumps(echo2)}")

    print(
        "RESULT "
        + json.dumps(
            {
                "project": str(project_id),
                "workflow": str(seed.workflow),
                "tenant": str(tenant),
                "container_prefix": f"stg-{seed.workflow.hex[:8]}",
            }
        ),
        flush=True,
    )
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
