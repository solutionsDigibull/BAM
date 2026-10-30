"""E13.2 end to end, nothing faked: a generated MS01.1 bundle, the in-VM runner and its vendored
harness inside the hardened `sandbox-runner` container, the real PostgreSQL and FastMCP of the
runtime image, driven by the real executor over the real `LocalDockerBackend`.

Needs Docker and the runtime image (`make runtime-registry` builds `skill-runtime:local`). The
sandbox image is built from it here (layers are cached) and started with the isolation flags of the
compose `sandbox-runner` service. A run executes one bundle and nothing else, so each test changes
the bundle, the dataset or the limits and reads what the platform would read: the classified
outcome.

CubeSandbox is not involved: this is the non-certifying local backend. What only a KVM host can show
(microVM boot, egress enforcement, the host's OOM killer) is the host acceptance suite's job.
"""

import asyncio
import hashlib
import json
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from codegen_cases import ms01_bodies, ms01_loop_spec, ms01_params
from sbam_contracts.sandbox import (
    EgressPolicy,
    FailureCategory,
    InfraReason,
    RunnerOutputs,
    SandboxLimits,
    SandboxMode,
)
from sbam_contracts.testgen import CaseBody, CaseKind, dataset_sha256
from sbam_workers.build.codegen import load_template, render_skeleton
from sbam_workers.build.codegen.bundle import assemble_bundle, make_bundle
from sbam_workers.sandbox.executor import DatasetInput, ExecutionResult, RunInput, execute
from sbam_workers.sandbox.inputs import harness_config
from sbam_workers.sandbox.local_backend import LocalDockerBackend

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker") or "docker"
REPO = Path(__file__).resolve().parents[3]  # the build context of the sandbox image
RUNTIME = "skill-runtime:local"
IMAGE = "sbam-sandbox-runner:e13-2-test"
TOKEN = "e132-runner-token-0123456789"  # noqa: S105 - a throwaway token for a throwaway container
DIGEST = "sha256:" + "a" * 64
M = "+919800000001"
INPUT = {"mobile_number": M, "otp_payload": "123456", "device_fingerprint": "fp-1"}
SCHEMA = b"""
CREATE TABLE investor_auth (mobile_number text PRIMARY KEY,
  failed_login_attempts int NOT NULL DEFAULT 0, user_status text NOT NULL);
CREATE TABLE investor_devices (id serial PRIMARY KEY, mobile_number text NOT NULL);
CREATE TABLE auth_event_audit_handoff (id bigserial PRIMARY KEY, skill_id text NOT NULL,
  payload jsonb NOT NULL);
CREATE FUNCTION handoff_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'handoff table is append-only'; END $$;
CREATE TRIGGER handoff_append_only BEFORE UPDATE OR DELETE ON auth_event_audit_handoff
  FOR EACH ROW EXECUTE FUNCTION handoff_append_only();
"""
UI = [
    "Status: Verifying -> Looping [Pass 2/3]: Incorrect OTP -> Authenticated",
    "Status: Escalated - Account Locked",
]


def docker(
    *args: str, check: bool = True, timeout: float = 600.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed docker CLI call
        [DOCKER, *args], capture_output=True, text=True, timeout=timeout, check=check
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def agent_url() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker is not available")
    if docker("image", "inspect", RUNTIME, check=False).returncode != 0:
        pytest.skip(f"{RUNTIME} is not built (make runtime-registry)")
    docker(
        "build", "-q", "-f", str(REPO / "compose/docker/sandbox-runner.Dockerfile"), "-t", IMAGE,
        "--build-arg", f"RUNTIME_IMAGE={RUNTIME}", str(REPO),
    )  # fmt: skip
    port, name = free_port(), f"sbx-e132-{uuid.uuid4().hex[:8]}"
    docker(
        "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:8091",
        "--read-only", "--tmpfs", "/tmp:size=512m,mode=1777",  # noqa: S108
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "10001:10001", "--memory", "1g", "--cpus", "1", "--pids-limit", "128",
        "-e", f"SANDBOX_AGENT_TOKEN={TOKEN}", "-e", f"SANDBOX_RUNTIME_DIGEST={DIGEST}", IMAGE,
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while True:  # Docker's port proxy accepts at once; ask the agent itself
        try:
            if httpx.get(
                f"{url}/v1/runtime", headers={"Authorization": f"Bearer {TOKEN}"}
            ).is_success:
                break
        except httpx.TransportError:
            pass
        assert time.monotonic() < deadline, docker("logs", name, check=False).stderr
        time.sleep(0.5)
    yield url
    docker("rm", "-f", name, check=False)


def case(name: str, terminal: str, *seed: str) -> tuple[CaseKind, CaseBody]:
    kind = {"SUCCESS": CaseKind.PASS_FIRST, "ESCALATED": CaseKind.ESCALATION}.get(
        terminal, CaseKind.NO_OP
    )
    body = {
        "name": name,
        "input": INPUT,
        "initial_state": list(seed),
        "expected_terminal": terminal,
    }
    return kind, CaseBody.model_validate(body)


def insert_auth(failed_attempts: int, status: str) -> str:
    return f"INSERT INTO investor_auth VALUES ('{M}', {failed_attempts}, '{status}')"  # noqa: S608


def insert_device() -> str:
    return f"INSERT INTO investor_devices (mobile_number) VALUES ('{M}')"  # noqa: S608


def good_cases() -> list[tuple[CaseKind, CaseBody]]:
    return [
        case("pass-first", "SUCCESS", insert_auth(0, "active")),
        case("escalates", "ESCALATED", insert_auth(3, "active"), *[insert_device()] * 5),
        case("locked-no-op", "NO_OP", insert_auth(0, "LOCKED")),
    ]


def run_input(
    *,
    bodies: dict[str, Any] | None = None,
    files: dict[str, str] | None = None,
    cases: list[tuple[CaseKind, CaseBody]] | None = None,
    limits: SandboxLimits | None = None,
) -> RunInput:
    """A MS01.1 run. `files` replaces whole bundle files after assembly (a bundle that is simply
    wrong)."""
    params = ms01_params()
    template = load_template("fastmcp-loopy", "1.0.0")
    assembled = assemble_bundle(
        template,
        render_skeleton(template, params),
        bodies if bodies is not None else ms01_bodies(),
        params.get("fresh_state", []),
        ms01_loop_spec(),
    )
    assert assembled.bundle is not None, assembled.findings
    contents = {name: data for name, data in assembled.bundle.files.items()}
    for name, text in (files or {}).items():
        contents[name] = text.encode()
    bundle = make_bundle(contents)
    chosen = cases if cases is not None else good_cases()
    return RunInput(
        run_id=uuid.uuid4(),
        mode=SandboxMode.LOCAL_DOCKER_NONCERT,
        runtime_digest=DIGEST,
        limits=limits or SandboxLimits(),
        policy=EgressPolicy(allow=[]),
        mock_api=None,
        bundle_tar=bundle.tar,
        bundle_files=dict(bundle.hashes),
        bundle_sha256=bundle.bundle_sha256,
        tool_module="skill.server",
        loop_spec=json.loads(ms01_loop_spec()),
        harness=harness_config(params, UI),
        handoff_table=params["handoff_table"],
        schema_sql=SCHEMA,
        schema_sql_sha256=hashlib.sha256(SCHEMA).hexdigest(),
        dataset=DatasetInput(chosen, {}, {}, dataset_sha256(chosen)),
    )


def run(url: str, spec: RunInput) -> ExecutionResult:
    return asyncio.run(execute(spec, LocalDockerBackend(url, TOKEN)))


def verdicts(result: ExecutionResult) -> dict[str, tuple[bool, FailureCategory | None]]:
    return {v.case_id: (v.passed, v.category) for v in result.classified.cases}


def with_action(code: str, helpers: str = "") -> dict[str, Any]:
    return ms01_bodies(action_body=code, helpers=helpers or ms01_bodies()["helpers"])


GOOD_RETURN = (
    'return {"auth_status": "DENIED", "user_role": None, "jwt_token": None, '
    '"state_handoff_id": None}\n'
)
APPEND = (
    "state_handoff.append(iteration_pass=1, device_fingerprint=device_fingerprint, "
    'otp_match=True, access_scope="none")\n'
)


def test_e13_2_t02_a_real_bundle_passes_all_three_scenarios_and_leaves_evidence(
    agent_url: str,
) -> None:
    result = run(agent_url, run_input())
    assert result.classified.category is None, result.classified
    assert verdicts(result) == {
        "pass-first": (True, None),
        "escalates": (True, None),
        "locked-no-op": (True, None),
    }
    assert result.exit_code == 0 and result.destroy_error is None
    outputs = RunnerOutputs.model_validate_json(result.outputs_json or b"")
    assert outputs.mode is SandboxMode.LOCAL_DOCKER_NONCERT and not outputs.mode.certifying
    by_id = {c.case_id: c for c in outputs.cases}
    assert [r["access_scope"] for r in by_id["pass-first"].handoff_rows] == ["investor"]
    assert by_id["locked-no-op"].handoff_rows == []  # NO_OP: the action tool is never called
    assert by_id["pass-first"].trace["handoff_rows"] == 1
    assert outputs.resources.wall_ms > 0


def test_e13_2_t05_a_module_missing_from_the_runtime_is_an_import_error(agent_url: str) -> None:
    params = ms01_params()
    template = load_template("fastmcp-loopy", "1.0.0")
    action = assemble_bundle(
        template, render_skeleton(template, params), ms01_bodies(),
        params.get("fresh_state", []), ms01_loop_spec(),
    ).bundle  # fmt: skip
    assert action is not None
    source = action.files["action_tool.py"].decode()
    broken = source.replace(
        "from __future__ import annotations\n",
        "from __future__ import annotations\n\nimport module_the_runtime_lacks\n",
        1,
    )
    assert broken != source
    result = run(agent_url, run_input(files={"action_tool.py": broken}))
    assert result.classified.category is FailureCategory.IMPORT_ERROR
    assert all(cat is FailureCategory.IMPORT_ERROR for _, cat in verdicts(result).values())
    assert "module_the_runtime_lacks" in result.classified.findings()[0].message


def test_e13_2_t06_a_tool_that_overruns_its_budget_is_a_timeout_and_the_run_still_ends(
    agent_url: str,
) -> None:
    started = time.monotonic()
    result = run(
        agent_url,
        run_input(bodies=with_action("time.sleep(70)\n" + GOOD_RETURN, "import time\n")),
    )
    assert result.classified.category is FailureCategory.TIMEOUT, result.classified
    assert time.monotonic() - started < 60  # the 5 s budget cancelled it; nothing waited 70 s
    assert result.destroy_error is None and result.exit_code == 0


def test_e13_2_a_tool_that_prints_instead_of_returning_is_an_output_schema_mismatch(
    agent_url: str,
) -> None:
    result = run(
        agent_url, run_input(bodies=with_action('print("done")\nreturn None  # type: ignore\n'))
    )
    assert result.classified.category in (
        FailureCategory.OUTPUT_SCHEMA_MISMATCH,
        FailureCategory.RUNTIME_ERROR,
    ), result.classified
    assert not result.classified.passed and result.classified.infra_reason is None


def test_e13_2_a_seed_statement_that_fails_is_a_dataset_problem_not_a_test_failure(
    agent_url: str,
) -> None:
    bad = [case("bad-seed", "SUCCESS", "INSERT INTO table_that_does_not_exist VALUES (1)")] * 1
    bad += good_cases()[1:]
    result = run(agent_url, run_input(cases=bad))
    assert result.classified.category is FailureCategory.INFRA_ERROR
    assert result.classified.infra_reason is InfraReason.DATASET_INVALID
    assert result.exit_code == 72


def test_e13_2_a_bundle_that_appends_two_handoff_rows_in_one_pass_fails_the_case(
    agent_url: str,
) -> None:
    result = run(agent_url, run_input(bodies=with_action(APPEND + APPEND + GOOD_RETURN)))
    assert result.classified.category is FailureCategory.RUNTIME_ERROR
    assert "handoff" in result.classified.findings()[0].message


def test_e13_2_a_bundle_that_never_appends_its_handoff_row_fails_the_case(agent_url: str) -> None:
    result = run(agent_url, run_input(bodies=with_action(GOOD_RETURN)))
    assert result.classified.category is FailureCategory.RUNTIME_ERROR
    assert not result.classified.passed


def test_e13_2_a_bundle_cannot_rewrite_the_append_only_evidence(agent_url: str) -> None:
    rewrite = "UPDATE auth_event_audit_handoff SET payload = '{}'"
    tamper = APPEND + f'state._conn.execute("{rewrite}")\n' + GOOD_RETURN
    result = run(agent_url, run_input(bodies=with_action(tamper)))
    assert result.classified.category is FailureCategory.RUNTIME_ERROR
    assert "append-only" in result.logs.decode() + (result.classified.findings()[0].message)


def test_e13_2_an_allocation_bomb_is_an_oom_case_not_a_dead_runner(agent_url: str) -> None:
    bomb = "blob = bytearray(4 * 1024 * 1024 * 1024)\n" + GOOD_RETURN
    limits = SandboxLimits(memory_mb=512)
    result = run(agent_url, run_input(bodies=with_action(bomb), limits=limits))
    assert result.classified.category is FailureCategory.OOM, result.classified
    assert result.exit_code == 0


def test_e13_2_nothing_survives_a_run_the_next_run_starts_clean(agent_url: str) -> None:
    first = run(
        agent_url, run_input(bodies=with_action("time.sleep(70)\n" + GOOD_RETURN, "import time\n"))
    )
    assert first.classified.category is FailureCategory.TIMEOUT
    second = run(agent_url, replace(run_input(), run_id=uuid.uuid4()))
    assert second.classified.category is None, second.classified  # no leftover database or port


@pytest.mark.parametrize(
    "code",
    ["raise SystemExit(3)\n", "import sys\nsys.exit(4)\n", "raise KeyboardInterrupt\n"],
    ids=["SystemExit", "sys.exit", "KeyboardInterrupt"],
)
def test_e13_2_a_tool_that_tries_to_exit_the_runner_fails_its_case_and_not_the_run(
    agent_url: str, code: str
) -> None:
    result = run(agent_url, run_input(bodies=with_action(code + GOOD_RETURN)))
    assert result.classified.category is FailureCategory.RUNTIME_ERROR, result.classified
    assert result.classified.infra_reason is None and result.exit_code == 0


def test_e13_2_a_tool_that_kills_the_runner_outright_is_known_to_be_infrastructure_for_now(
    agent_url: str,
) -> None:
    """`os._exit` cannot be caught: the runner dies and writes nothing. Until the code under test
    runs in its own process (#625) the platform cannot tell this from a dead runner, so it retries
    and then blocks testing. This test records that limit so nobody mistakes it for a verdict."""
    result = run(agent_url, run_input(bodies=with_action("import os\nos._exit(7)\n" + GOOD_RETURN)))
    assert result.classified.category is FailureCategory.INFRA_ERROR
    assert result.classified.infra_reason is InfraReason.RUNNER_FAILED
    assert result.exit_code == 7 and result.destroy_error is None
