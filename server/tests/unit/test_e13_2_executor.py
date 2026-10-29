"""E13.2 executor against a fake backend: the order of the safety checks is the thing under test.

`FakeBackend` records every call, so each test can say what must never happen (a VM for a bad
hash) and what must always happen (destroy, even after a crash). The real backends are held to the
same protocol by their own tests.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from sbam_contracts.sandbox import (
    EgressEvent,
    EgressPolicy,
    EgressSource,
    EgressVerdict,
    FailureCategory,
    InfraReason,
    SandboxLimits,
    SandboxMode,
)
from sbam_contracts.testgen import CaseBody, CaseKind, dataset_sha256
from sbam_workers.build.codegen.bundle import make_bundle
from sbam_workers.sandbox.backend import (
    ExecResult,
    SandboxInfraError,
    VmHandle,
    VmInfo,
    VmSpec,
)
from sbam_workers.sandbox.executor import (
    OUTPUTS_PATH,
    REQUEST_PATH,
    DatasetInput,
    RunInput,
    execute,
)
from sbam_workers.sandbox.policy import MockApi
from sbam_workers.sandbox.verify import HashMismatch

DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 10, 29, tzinfo=UTC)
FILES = {"skill_server.py": b"print('ok')\n", "loop_spec.json": b"{}\n"}
SCHEMA = b"CREATE TABLE handoff (id int);\n"


class FakeBackend:
    mode = SandboxMode.CUBESANDBOX

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.created: list[VmSpec] = []
        self.written: dict[str, bytes] = {}
        self.system_written: dict[str, bytes] = {}
        self.exec_argv: Sequence[str] = ()
        self.digest = DIGEST
        self.outputs: bytes | None = None
        self.exec_result = ExecResult(exit_code=0, stdout=b"out", stderr=b"err")
        self.fail_on: str | None = None
        self.alive = True
        self.destroyed: list[str] = []
        self.destroy_raises = False

    def _maybe_fail(self, step: str) -> None:
        self.calls.append(step)
        if self.fail_on == step:
            raise SandboxInfraError(InfraReason.HOST_UNREACHABLE, f"{step} refused")

    async def create(self, spec: VmSpec) -> VmHandle:
        self._maybe_fail("create")
        self.created.append(spec)
        return VmHandle("vm-1")

    async def read_runtime_digest(self, vm: VmHandle) -> str:
        self._maybe_fail("digest")
        return self.digest

    async def put_files(
        self, vm: VmHandle, files: Mapping[str, bytes], *, system: bool = False
    ) -> None:
        self._maybe_fail("put_files")
        (self.system_written if system else self.written).update(files)

    async def exec(
        self,
        vm: VmHandle,
        argv: Sequence[str],
        *,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int,
    ) -> ExecResult:
        self._maybe_fail("exec")
        self.exec_argv = argv
        return self.exec_result

    async def get_file(self, vm: VmHandle, path: str, *, max_bytes: int) -> bytes | None:
        self._maybe_fail("get_file")
        return self.outputs if path == OUTPUTS_PATH else None

    async def destroy(self, vm_id: str) -> None:
        self.calls.append("destroy")
        self.destroyed.append(vm_id)
        if self.destroy_raises:
            raise RuntimeError("kill refused")

    async def list_vms(self) -> list[VmInfo]:
        self.calls.append("list")
        return [VmInfo("vm-1", NOW)] if self.alive else []


def cases() -> list[tuple[CaseKind, CaseBody]]:
    def body(name: str, terminal: str) -> CaseBody:
        return CaseBody.model_validate(
            {"name": name, "input": {"q": name}, "expected_terminal": terminal}
        )

    return [
        (CaseKind.PASS_FIRST, body("pass-first", "SUCCESS")),
        (CaseKind.ESCALATION, body("escalates", "ESCALATED")),
        (CaseKind.EDGE, body("edge", "SUCCESS")),
    ]


def make_run(**changes: Any) -> RunInput:
    bundle = make_bundle(FILES)
    dataset = DatasetInput(cases(), {}, {}, dataset_sha256(cases()))
    base: dict[str, Any] = {
        "run_id": uuid.UUID(int=1),
        "mode": SandboxMode.CUBESANDBOX,
        "runtime_digest": DIGEST,
        "limits": SandboxLimits(),
        "policy": EgressPolicy(allow=["api.example.com:443"]),
        "mock_api": MockApi("sbam-mock-api", "203.0.113.9", 8080),
        "bundle_tar": bundle.tar,
        "bundle_files": dict(bundle.hashes),
        "bundle_sha256": bundle.bundle_sha256,
        "tool_module": "skill_server",
        "loop_spec": {"max_iterations": 3},
        "handoff_table": "handoff",
        "schema_sql": SCHEMA,
        "schema_sql_sha256": hashlib.sha256(SCHEMA).hexdigest(),
        "dataset": dataset,
    }
    base.update(changes)
    return RunInput(**base)


def good_outputs(*, passed: bool = True, digest: str = DIGEST) -> bytes:
    def case(name: str, terminal: str) -> dict[str, Any]:
        return {
            "case_id": name,
            "expected_terminal": terminal,
            "terminal": terminal if passed else None,
            "passed": passed,
            "failure_category": None if passed else "IMPORT_ERROR",
            "trace": {
                "case_id": name,
                "terminal": terminal if passed else None,
                "iterations": 0,
                "passes": [],
                "ui_updates": [],
                "handoff_rows": 0,
                **(
                    {}
                    if passed
                    else {
                        "error": {
                            "category": "IMPORT_ERROR",
                            "pass": 1,
                            "step": "action",
                            "message": "No module named 'x'",
                            "elapsed_ms": 1,
                        }
                    }
                ),
            },
        }

    return json.dumps(
        {
            "runtime_digest": digest,
            "mode": "CUBESANDBOX",
            "resources": {"wall_ms": 1, "cpu_ms": 1, "peak_rss_kb": 1},
            "cases": [
                case("pass-first", "SUCCESS"),
                case("escalates", "ESCALATED"),
                case("edge", "SUCCESS"),
            ],
        }
    ).encode()


async def no_egress(vm_id: str) -> list[EgressEvent]:
    return []


async def public_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


async def private_resolver(host: str) -> list[str]:
    return ["10.0.0.7"]


async def _run(backend: FakeBackend, run_input: RunInput | None = None, **kw: Any) -> Any:
    kw.setdefault("egress_reader", no_egress)
    kw.setdefault("resolver", public_resolver)
    return await execute(run_input or make_run(), backend, **kw)


def run(backend: FakeBackend, run_input: RunInput | None = None, **kw: Any) -> Any:
    return asyncio.run(_run(backend, run_input, **kw))


def test_e13_2_t01_happy_run_passes_and_destroys_the_vm() -> None:
    backend = FakeBackend()
    backend.outputs = good_outputs()
    result = run(backend)
    assert result.classified.passed
    assert result.vm_id == "vm-1"
    assert backend.destroyed == ["vm-1"]
    assert result.outputs_json == backend.outputs
    assert b"out" in result.logs and b"err" in result.logs
    assert list(backend.exec_argv)[:2] == ["python", "-I"]


def test_e13_2_t04_bundle_hash_mismatch_creates_no_vm() -> None:
    backend = FakeBackend()
    bad = make_bundle({**FILES, "skill_server.py": b"import os\n"})
    with pytest.raises(HashMismatch):
        run(backend, make_run(bundle_tar=bad.tar))
    assert backend.calls == []


@pytest.mark.parametrize("what", ["schema", "dataset"])
def test_e13_2_t04_schema_and_dataset_hash_mismatch_create_no_vm(what: str) -> None:
    backend = FakeBackend()
    changes: dict[str, Any] = (
        {"schema_sql_sha256": "0" * 64}
        if what == "schema"
        else {"dataset": DatasetInput(cases(), {}, {}, "0" * 64)}
    )
    with pytest.raises(HashMismatch) as caught:
        run(backend, make_run(**changes))
    assert caught.value.reason is InfraReason.HASH_MISMATCH
    assert backend.calls == []


def test_e13_2_t05_import_error_is_a_test_failure_not_infra() -> None:
    backend = FakeBackend()
    backend.outputs = good_outputs(passed=False)
    result = run(backend)
    assert result.classified.category is FailureCategory.IMPORT_ERROR
    assert result.classified.infra_reason is None
    assert backend.destroyed == ["vm-1"]


def test_e13_2_t06_runner_timeout_is_timeout_and_vm_destroyed() -> None:
    backend = FakeBackend()
    backend.exec_result = ExecResult(exit_code=-9, stdout=b"", stderr=b"", timed_out=True)
    result = run(backend)
    assert result.classified.category is FailureCategory.TIMEOUT
    assert backend.destroyed == ["vm-1"]


def test_e13_2_infra_error_after_create_still_destroys_and_is_infra() -> None:
    backend = FakeBackend()
    backend.fail_on = "exec"
    result = run(backend)
    assert result.classified.category is FailureCategory.INFRA_ERROR
    assert result.classified.infra_reason is InfraReason.HOST_UNREACHABLE
    assert backend.destroyed == ["vm-1"]


def test_e13_2_a_backend_bug_is_infra_never_a_test_failure() -> None:
    backend = FakeBackend()

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise ValueError("sdk changed")

    backend.put_files = boom  # type: ignore[method-assign]
    result = run(backend)
    assert result.classified.category is FailureCategory.INFRA_ERROR
    assert result.classified.infra_reason is InfraReason.RUNNER_FAILED
    assert backend.destroyed == ["vm-1"]


def test_e13_2_create_failure_raises_and_nothing_to_destroy() -> None:
    backend = FakeBackend()
    backend.fail_on = "create"
    with pytest.raises(SandboxInfraError) as caught:
        run(backend)
    assert caught.value.reason is InfraReason.HOST_UNREACHABLE
    assert backend.destroyed == []


def test_e13_2_wrong_runtime_in_the_vm_is_template_digest_mismatch() -> None:
    backend = FakeBackend()
    backend.digest = "sha256:" + "b" * 64
    result = run(backend)
    assert result.classified.infra_reason is InfraReason.TEMPLATE_DIGEST_MISMATCH
    assert "exec" not in backend.calls  # nothing ran in a VM we do not trust
    assert backend.destroyed == ["vm-1"]


def test_e13_2_a_private_allowlist_answer_is_egress_blocked_with_no_vm() -> None:
    backend = FakeBackend()
    result = run(backend, resolver=private_resolver)
    assert result.classified.category is FailureCategory.EGRESS_BLOCKED
    assert result.vm_id is None
    assert backend.calls == []


def test_e13_2_a_failing_destroy_is_reported_not_swallowed() -> None:
    backend = FakeBackend()
    backend.outputs = good_outputs()
    backend.destroy_raises = True
    result = run(backend)
    assert result.destroy_error == "RuntimeError"
    assert result.vm_id == "vm-1"  # the activity keeps it so the reaper can find the VM


def test_e13_2_certifying_mode_refuses_to_run_without_egress_evidence() -> None:
    backend = FakeBackend()
    with pytest.raises(SandboxInfraError) as caught:
        asyncio.run(execute(make_run(), backend, resolver=public_resolver))
    assert caught.value.reason is InfraReason.EGRESS_LOG_UNAVAILABLE
    assert backend.calls == []


def test_e13_2_backend_and_run_must_agree_on_the_mode() -> None:
    backend = FakeBackend()
    with pytest.raises(SandboxInfraError):
        run(backend, make_run(mode=SandboxMode.LOCAL_DOCKER_NONCERT))
    assert backend.calls == []


def test_e13_2_the_vm_gets_only_pinned_addresses_and_the_run_tag() -> None:
    backend = FakeBackend()
    backend.outputs = good_outputs()
    run(backend)
    spec = backend.created[0]
    assert set(spec.allow_out) == {"93.184.216.34", "203.0.113.9"}
    assert spec.metadata["sbam_run"] == str(uuid.UUID(int=1))
    assert spec.template_alias.startswith("sbam-rt-") and len(spec.template_alias) <= 64
    assert b"93.184.216.34 api.example.com" in backend.system_written["/etc/hosts"]


def test_e13_2_nothing_dynamic_reaches_the_command_line() -> None:
    backend = FakeBackend()
    backend.outputs = good_outputs()
    run(backend)
    request = json.loads(backend.written[REQUEST_PATH])
    assert [c["case_id"] for c in request["cases"]] == ["edge", "escalates", "pass-first"]
    joined = " ".join(backend.exec_argv)
    assert "pass-first" not in joined and "skill_server" not in joined


def test_e13_2_blocked_egress_turns_a_failure_into_egress_blocked() -> None:
    backend = FakeBackend()
    backend.outputs = good_outputs(passed=False)

    async def blocked(vm_id: str) -> list[EgressEvent]:
        return [
            EgressEvent(
                vm_id=vm_id,
                at=NOW,
                dst_host="evil.example.net",
                dst_port=443,
                verdict=EgressVerdict.BLOCK,
                source=EgressSource.L7_PROXY,
            )
        ]

    result = run(backend, egress_reader=blocked)
    assert result.classified.category is FailureCategory.EGRESS_BLOCKED
    assert len(result.egress) == 1


def test_e13_2_unparseable_outputs_are_runner_failed() -> None:
    backend = FakeBackend()
    backend.outputs = b"{not json"
    result = run(backend)
    assert result.classified.infra_reason is InfraReason.RUNNER_FAILED
    assert backend.destroyed == ["vm-1"]


def test_e13_2_cancellation_mid_run_still_destroys_the_vm() -> None:
    backend = FakeBackend()
    started = asyncio.Event()

    async def hang(*args: Any, **kwargs: Any) -> ExecResult:
        started.set()
        await asyncio.sleep(3600)
        raise AssertionError("not reached")

    backend.exec = hang  # type: ignore[method-assign]

    async def scenario() -> None:
        task = asyncio.create_task(_run(backend))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert backend.destroyed == ["vm-1"]
