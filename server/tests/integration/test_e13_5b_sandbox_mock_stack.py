"""E13.5b (#545): the mock API as the sandbox's tool target, through the real `run_sandbox_tests`
activity, the real local `sandbox-runner` container and the real `sbam-mock-api` container.

What is real: Postgres at head, local object storage, the activity (it installs the dataset's stubs
under the sandbox run's id, appends the mock journal to the run logs and removes the run), the
runner's harness and child process (which stamps `X-Sbam-Run-Id` and `X-Sbam-Case` on every call to
the mock, ADR 0023 addendum), FastMCP and the runtime's PostgreSQL, and the built mock image.
The tools are generated MS01.1 bundles whose check body calls the mock with `urllib`.

The containers are a throwaway pair on a private Docker network of their own (the mock under the
alias `sbam-mock-api`), so the stack's own services are never touched. The runner is the local,
non-certifying backend: these runs prove the wiring and never certify.

Not run here, and why: E13.5-T02 (a call to a real host is `EGRESS_BLOCKED`) needs CubeSandbox's
egress enforcement: the local backend cannot block or report egress. It runs when a KVM host is
configured (`SANDBOX_MODE=cubesandbox`, `CUBE_API_URL`, `CUBE_PROXY_NODE_IP`, an egress source, and
`MOCK_API_URL` / `MOCK_API_IP` / `MOCK_CONTROL_TOKEN` reachable from the worker) and skips
otherwise;
see docs/runbooks/sandbox-host.md.

Needs `make up` (Postgres), Docker and `skill-runtime:local` (`make runtime-registry`); not part of
the unit run.
"""

import io
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from codegen_cases import ms01_bodies
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.testgen.models import DatasetRow
from sbam_api.testgen.service import mocks_hashes
from sbam_contracts.build import TESTS_FAILED
from sbam_contracts.mocks import MockDefinition
from sbam_contracts.sandbox import FailureCategory, RunnerOutputs, SandboxMode
from sbam_contracts.testgen import CaseBody, CaseKind, dataset_sha256
from sbam_workers.sandbox.activity import RunSandboxTests
from sbam_workers.sandbox.factory import Sandbox, make_sandbox
from sbam_workers.sandbox.load import SqlSandboxInputs
from sbam_workers.sandbox.local_backend import LocalDockerBackend
from sbam_workers.sandbox.store import SqlRunStore
from sbam_workers.settings import WorkerSettings
from sbam_workers.test.mocks import MockApiClient, definition_bytes
from sqlalchemy import pool, text
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

from .test_db import url
from .test_e13_2_activity_stack import World, run_activity, runs_of, seed
from .test_e13_2_runner_container import (
    DIGEST,
    DOCKER,
    INPUT,
    REPO,
    RUNTIME,
    TOKEN,
    docker,
    free_port,
    good_cases,
    insert_auth,
    insert_device,
)

pytestmark = pytest.mark.integration

DB = f"sbam_e135b_{uuid.uuid4().hex[:10]}"
RUNNER_IMAGE = "sbam-sandbox-runner:e13-5b-test"
MOCK_IMAGE = "sbam-mock-api:e13-5b-test"
CONTROL_TOKEN = (
    "e135b-control-token-0123456789"  # noqa: S105 - a throwaway token, throwaway container
)
MARKER = b"\n--- mock journal ---\n"

CRM = {
    "name": "crm",
    "stubs": [
        {
            "id": "status-ok",
            "method": "GET",
            "path": "/crm/status",
            "responses": [{"status": 200, "body": {"ok": True}}],
        },
        {  # only the case named `retry`: 503 on its first call, 200 afterwards
            "id": "status-retry",
            "method": "GET",
            "path": "/crm/status",
            "case": "retry",
            "responses": [
                {"status": 503, "body": {"error": "busy"}},
                {"status": 200, "body": {"ok": True}},
            ],
        },
    ],
}

# The check body asks the mock how the CRM is and passes only on a 200: a 503 makes the loop retry.
CALL_MOCK = """\
import os
import urllib.error
import urllib.request

request = urllib.request.Request(os.environ["SBAM_MOCK_BASE_URL"] + "/crm/status")
try:
    status = urllib.request.urlopen(request, timeout=5).status
except urllib.error.HTTPError as err:
    status = err.code
return {"otp_match": status == 200, "policy_violation": not _within_device_limit(current)}
"""

# The tool reads the run's journal without a token: the answer must be 401, and no token may be in
# its environment. It fails the case (a RUNTIME_ERROR) when it is not.
READ_CONTROL = """\
import os
import urllib.error
import urllib.request

if any(name.startswith("MOCK_CONTROL_TOKEN") for name in os.environ):
    raise RuntimeError("the control token is visible to the tool")
base, run = os.environ["SBAM_MOCK_BASE_URL"], os.environ["SBAM_RUN_ID"]
try:
    urllib.request.urlopen(base + "/_control/runs/" + run + "/journal", timeout=5)
    code = 200
except urllib.error.HTTPError as err:
    code = err.code
if code != 401:
    raise RuntimeError("the control surface answered " + str(code) + " without a token")
return {"otp_match": True, "policy_violation": not _within_device_limit(current)}
"""

CALL_REAL_HOST = """\
import urllib.request

urllib.request.urlopen("https://api.realcrm.com/v1/accounts/42", timeout=5)
return {"otp_match": True, "policy_violation": not _within_device_limit(current)}
"""


def bodies(check_body: str) -> dict[str, Any]:
    return ms01_bodies(check_body=check_body)


def case(name: str, kind: CaseKind, terminal: str, *seed: str) -> tuple[CaseKind, CaseBody]:
    body = {
        "name": name,
        "input": INPUT,
        "initial_state": list(seed),
        "expected_terminal": terminal,
    }
    return kind, CaseBody.model_validate(body)


# the dataset needs PASS_FIRST and ESCALATION and three cases; `retry` is the one with a 503 stub
T01 = [
    case("pass-first", CaseKind.PASS_FIRST, "SUCCESS", insert_auth(0, "active")),
    case("retry", CaseKind.PASS_AFTER_RETRY, "SUCCESS", insert_auth(0, "active")),
    case(
        "escalates",
        CaseKind.ESCALATION,
        "ESCALATED",
        insert_auth(3, "active"),
        *[insert_device()] * 5,
    ),
]


# --- the throwaway containers ----------------------------------------------------------------


@dataclass
class Net:
    name: str
    mock_url: str  # as the host reaches the mock's control surface
    mock_ip: str
    agents: list[str]  # the local runners' URLs, as the host reaches them


def _agent_ready(agent: str, name: str) -> None:
    deadline = time.monotonic() + 60
    while True:
        try:
            if httpx.get(
                f"{agent}/v1/runtime", headers={"Authorization": f"Bearer {TOKEN}"}
            ).is_success:
                return
        except httpx.TransportError:
            pass
        assert time.monotonic() < deadline, docker("logs", name, check=False).stderr
        time.sleep(0.5)


@pytest.fixture(scope="module")
def net() -> Iterator[Net]:
    if shutil.which(DOCKER) is None:
        pytest.skip("Docker is not available")
    if docker("image", "inspect", RUNTIME, check=False).returncode != 0:
        pytest.skip(f"{RUNTIME} is not built (make runtime-registry)")
    docker(
        "build", "-q", "-f", str(REPO / "compose/docker/sandbox-runner.Dockerfile"),
        "-t", RUNNER_IMAGE,
        "--build-arg", f"RUNTIME_IMAGE={RUNTIME}", str(REPO),
    )  # fmt: skip
    docker(
        "build", "-q", "-f", str(REPO / "compose/docker/mock-api.Dockerfile"),
        "-t", MOCK_IMAGE, str(REPO),
    )  # fmt: skip
    tag = uuid.uuid4().hex[:8]
    network, names = f"sbx-e135b-{tag}", []
    docker("network", "create", network)
    try:
        mock_port = free_port()
        mock = f"sbx-e135b-mock-{tag}"
        docker(
            "run", "-d", "--name", mock, "--network", network, "--network-alias", "sbam-mock-api",
            "-p", f"127.0.0.1:{mock_port}:8090", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "-e", f"MOCK_CONTROL_TOKEN={CONTROL_TOKEN}", MOCK_IMAGE,
        )  # fmt: skip
        names.append(mock)
        mock_url = f"http://127.0.0.1:{mock_port}"
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"{mock_url}/health", timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, docker("logs", mock, check=False).stderr
            time.sleep(0.3)
        ip = docker(
            "inspect",
            "-f",
            f'{{{{(index .NetworkSettings.Networks "{network}").IPAddress}}}}',
            mock,
        ).stdout.strip()
        agents = []
        for index in range(2):  # two runners, so two runs can really overlap (T05)
            port, name = free_port(), f"sbx-e135b-run{index}-{tag}"
            docker(
                "run", "-d", "--name", name, "--network", network, "-p", f"127.0.0.1:{port}:8091",
                "--read-only", "--tmpfs", "/tmp:size=512m,mode=1777",  # noqa: S108
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--user", "10001:10001", "--memory", "1g", "--cpus", "1", "--pids-limit", "128",
                "-e", f"SANDBOX_AGENT_TOKEN={TOKEN}", "-e", f"SANDBOX_RUNTIME_DIGEST={DIGEST}",
                "-e", "SANDBOX_AGENT_NPROC=0",
                RUNNER_IMAGE,
            )  # fmt: skip
            names.append(name)
            agents.append(f"http://127.0.0.1:{port}")
            _agent_ready(agents[-1], name)
        yield Net(network, mock_url, ip, agents)
    finally:
        for name in names:
            docker("rm", "-f", name, check=False)
        docker("network", "rm", network, check=False)


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield make_sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskBackend:
    return LocalDiskBackend(tmp_path)


# --- the world: a dataset that carries the mock definitions ----------------------------------


def world_with_mocks(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    check_body: str,
    chosen: list[tuple[CaseKind, CaseBody]],
) -> World:
    world = seed(sessions, storage, cases=chosen, bodies=bodies(check_body))
    definition = MockDefinition.model_validate(CRM)
    scope = StorageScope(world.inp.tenant_id, world.inp.project_id, world.inp.workflow_id)
    key = storage.put(
        io.BytesIO(definition_bytes(definition.model_dump(mode="json"))),
        scope,
        Kind.TESTS,
        "application/json",
    ).storage_key
    with sessions() as s:
        dataset = (
            s.query(DatasetRow).filter(DatasetRow.sub_skill_id == world.inp.sub_skill_id).one()
        )
        dataset.mocks_keys = [key]
        dataset.sha256 = dataset_sha256(chosen, {}, mocks_hashes([key]))
        s.commit()
    return world


def stage(
    world: World,
    storage: LocalDiskBackend,
    net: Net,
    agent: int,
    monkeypatch: pytest.MonkeyPatch,
    sandbox: Sandbox | None = None,
) -> RunSandboxTests:
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    # what the tool is told (in the private network) and what the worker pins as the egress target
    monkeypatch.setenv("MOCK_API_URL", "http://sbam-mock-api:8090")
    monkeypatch.setenv("MOCK_API_IP", net.mock_ip)
    settings = WorkerSettings()  # type: ignore[call-arg]
    return RunSandboxTests(
        SqlSandboxInputs(world.sessions, storage),
        SqlRunStore(world.sessions, storage),
        world.isatvon_of,
        settings,
        sandbox_factory=lambda _: sandbox
        or Sandbox(
            SandboxMode.LOCAL_DOCKER_NONCERT, LocalDockerBackend(net.agents[agent], TOKEN), None
        ),
        mock=MockApiClient(
            net.mock_url, CONTROL_TOKEN
        ),  # the worker's side, as the host reaches it
    )


def journal_of(storage: LocalDiskBackend, logs_key: str | None) -> list[dict[str, Any]]:
    """The mock journal the activity appended to the run's logs."""
    assert logs_key, "the run kept no logs"
    logs = storage.get(logs_key, verify=True)
    assert MARKER in logs, "no mock journal in the run logs"
    document = json.loads(logs.split(MARKER, 1)[1].strip())
    entries: list[dict[str, Any]] = document["entries"]
    return entries


def outputs_of(storage: LocalDiskBackend, outputs_key: str | None) -> RunnerOutputs:
    assert outputs_key
    return RunnerOutputs.model_validate_json(storage.get(outputs_key, verify=True))


# --- T01 and T04: a real run against the mock ------------------------------------------------


@pytest.fixture
def first_run(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    net: Net,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[World, Any]:
    world = world_with_mocks(sessions, storage, CALL_MOCK, T01)
    result = run_activity(stage(world, storage, net, 0, monkeypatch), world.inp)
    return world, result


def test_e13_5_t01_a_tool_call_reaches_the_stub_and_the_journal_shows_the_matched_stub_id(
    first_run: tuple[World, Any], storage: LocalDiskBackend
) -> None:
    world, result = first_run
    [row] = runs_of(world)
    assert result.id == row.id and row.status == "FINISHED" and row.failure_category is None
    assert row.mode == "LOCAL_DOCKER_NONCERT"  # a local run: valid evidence, never certifying
    entries = journal_of(storage, row.logs_key)
    first = [e for e in entries if e["case"] == "pass-first"]
    assert [(e["stub_id"], e["status"], e["response_index"]) for e in first] == [
        ("status-ok", 200, 0)
    ]  # the headers reached the mock: its run namespace and the case both match
    assert all(e["method"] == "GET" and e["path"] == "/crm/status" for e in entries)
    assert not any(e["stub_id"] == "UNMATCHED" for e in entries)


def test_e13_5_t04_a_pass_after_retry_case_fails_pass_1_with_a_503_and_succeeds_at_iteration_2(
    first_run: tuple[World, Any], storage: LocalDiskBackend
) -> None:
    world, _ = first_run
    [row] = runs_of(world)
    retry = [e for e in journal_of(storage, row.logs_key) if e["case"] == "retry"]
    assert [(e["stub_id"], e["status"], e["response_index"]) for e in retry] == [
        ("status-retry", 503, 0),
        ("status-retry", 200, 1),
    ]
    by_case = {c.case_id: c for c in outputs_of(storage, row.outputs_key).cases}
    assert by_case["retry"].terminal == "SUCCESS" and by_case["retry"].passed
    assert by_case["retry"].trace["iterations"] == 2
    assert by_case["pass-first"].trace["iterations"] == 1


def test_e13_5_the_runs_stubs_are_removed_after_the_run(
    first_run: tuple[World, Any], net: Net
) -> None:
    world, _ = first_run
    [row] = runs_of(world)
    gone = MockApiClient(net.mock_url, CONTROL_TOKEN)
    with pytest.raises(httpx.HTTPStatusError) as err:
        gone.journal(str(row.id))
    assert err.value.response.status_code == 404


# --- T05: two runs at the same time ----------------------------------------------------------


def test_e13_5_t05_two_concurrent_runs_using_the_same_path_get_their_own_sequences_and_journals(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    net: Net,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worlds = [world_with_mocks(sessions, storage, CALL_MOCK, T01) for _ in range(2)]
    stages = [stage(w, storage, net, i, monkeypatch) for i, w in enumerate(worlds)]
    with ThreadPoolExecutor(max_workers=2) as pool_:  # both runners busy at once
        futures = [
            pool_.submit(run_activity, s, w.inp) for s, w in zip(stages, worlds, strict=True)
        ]
        [f.result(timeout=600) for f in futures]
    rows = [runs_of(w)[0] for w in worlds]
    assert rows[0].id != rows[1].id
    for row in rows:
        entries = journal_of(storage, row.logs_key)
        retry = [(e["status"], e["response_index"]) for e in entries if e["case"] == "retry"]
        assert retry == [(503, 0), (200, 1)]  # each run starts its own sequence at the 503
        assert len(entries) == 4  # its own four calls (1 + 2 + 1) and no one else's
        assert row.status == "FINISHED" and row.failure_category is None


# --- the control token -----------------------------------------------------------------------


def test_e13_5_the_control_token_is_not_readable_from_inside_the_sandbox(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    net: Net,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = world_with_mocks(
        sessions, storage, READ_CONTROL, good_cases(),
    )  # fmt: skip
    run_activity(stage(world, storage, net, 0, monkeypatch), world.inp)
    [row] = runs_of(world)
    # the tool got 401 on /_control/... (it raises otherwise, which would fail its case) and saw
    # no MOCK_CONTROL_TOKEN in its environment
    assert row.status == "FINISHED" and row.failure_category is None


# --- T02: needs CubeSandbox's egress enforcement ---------------------------------------------


def _cube_host_configured() -> str | None:
    if os.environ.get("SANDBOX_MODE") != "cubesandbox" or not os.environ.get("CUBE_API_URL"):
        return "no CubeSandbox host (SANDBOX_MODE, CUBE_API_URL): docs/runbooks/sandbox-host.md"
    for name in ("MOCK_API_URL", "MOCK_API_IP", "MOCK_CONTROL_TOKEN"):
        if not os.environ.get(name):
            return f"{name} is not set for the CubeSandbox host"
    return None


def test_e13_5_t02_a_tool_calling_a_real_api_host_is_blocked_with_egress_blocked(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    net: Net,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    why = _cube_host_configured()
    if why is not None:
        pytest.skip(why)  # the local backend can neither block nor report egress
    world = world_with_mocks(
        sessions, storage, CALL_REAL_HOST, good_cases(),
    )  # fmt: skip
    for name in (
        "MOCK_API_URL",
        "MOCK_API_IP",
    ):  # the host's own pinned values, not the private net's
        monkeypatch.setenv(name, os.environ[name])
    settings = WorkerSettings()  # type: ignore[call-arg]
    token = settings.mock_control_token
    assert token is not None
    activity = RunSandboxTests(
        SqlSandboxInputs(world.sessions, storage),
        SqlRunStore(world.sessions, storage),
        world.isatvon_of,
        settings,
        sandbox_factory=lambda _: make_sandbox(settings),
        mock=MockApiClient(os.environ["MOCK_API_URL"], token.get_secret_value()),
    )
    with pytest.raises(ApplicationError) as failed:
        run_activity(activity, world.inp)
    assert failed.value.type == TESTS_FAILED
    [row] = runs_of(world)
    assert row.failure_category == FailureCategory.EGRESS_BLOCKED.value
