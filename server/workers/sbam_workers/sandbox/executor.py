"""Run one code attempt against one dataset in one sandbox (E13.2, ADR 0021).

`execute` is the whole life of a run, in the order the safety checks need:

  1. verify every input hash (no VM exists yet: a mismatch is `HashMismatch`)
  2. plan the network (resolve and pin the allowlist; a non-public answer is EGRESS_BLOCKED)
  3. create the VM, then `try:` everything below and `finally:` destroy it
  4. check the VM's own digest, put the files in, run the runner, read `outputs.json`
  5. classify from evidence and hand back the artifacts for the activity to store

It does no storage and no database work: the activity owns both, so this module can be driven by
`FakeBackend` in unit tests and by the real backends unchanged.

Rules worth knowing
  * Infrastructure trouble never raises out of `execute` after the VM exists. It becomes a
    `Classified` INFRA_ERROR, so the activity decides between retry and TESTING_BLOCKED.
  * `HashMismatch` and a backend that fails in `create` do raise: no VM exists, and the activity
    records the reason from the exception.
  * Destroy failing is reported (`destroy_error`), never swallowed: a leaked VM is the reaper's job
    and the run row keeps the VM id so it can find it.
  * The command is fixed. Nothing from the bundle, the dataset or the manifest reaches an argv or a
    shell: they travel as files.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sbam_contracts.sandbox import (
    EgressEvent,
    EgressPolicy,
    FailureCategory,
    InfraReason,
    RunnerOutputs,
    RunnerRequest,
    SandboxLimits,
    SandboxMode,
    normalize_digest,
    template_alias,
)
from sbam_contracts.testgen import CaseBody, CaseKind, case_canonical, dataset_sha256

from sbam_workers.sandbox.backend import (
    ExecResult,
    SandboxBackend,
    SandboxInfraError,
    VmHandle,
    VmSpec,
)
from sbam_workers.sandbox.classify import Classified, classify, infra
from sbam_workers.sandbox.policy import (
    EgressPolicyError,
    MockApi,
    NetworkPlan,
    Resolver,
    plan_network,
    render_hosts,
    system_resolver,
)
from sbam_workers.sandbox.verify import HashMismatch, same_hash, verify_bundle, verify_sha

log = logging.getLogger("sbam_workers.sandbox")

WORK_DIR: Final = "/work"
BUNDLE_DIR: Final = f"{WORK_DIR}/bundle"
REQUEST_PATH: Final = f"{WORK_DIR}/request.json"
SCHEMA_PATH: Final = f"{WORK_DIR}/schema.sql"
OUTPUTS_PATH: Final = f"{WORK_DIR}/out/outputs.json"
HOSTS_PATH: Final = "/etc/hosts"
RUNNER_ARGV: Final = ("python", "-I", "/opt/sbam/harness/run_cases.py", REQUEST_PATH)
MAX_REQUEST_BYTES: Final = 4 * 1024 * 1024
BOOT_GRACE_S: Final = 300  # the host's own backstop beyond the run (ADR 0021, decision 10)
MAX_LOG_BYTES: Final = 10 * 1024 * 1024

EgressReader = Callable[[str], Awaitable[list[EgressEvent]]]


@dataclass(frozen=True)
class DatasetInput:
    """A dataset as E13.4 stores it, plus the hash recorded for it (`test_datasets.sha256`)."""

    cases: Sequence[tuple[CaseKind, CaseBody]]
    files_hashes: Mapping[str, str]
    mocks_hashes: Mapping[str, str]
    expected_sha256: str


@dataclass(frozen=True)
class RunInput:
    run_id: UUID
    mode: SandboxMode  # from the worker's environment only
    runtime_digest: str
    limits: SandboxLimits
    policy: EgressPolicy
    mock_api: MockApi | None
    bundle_tar: bytes
    bundle_files: Mapping[str, str]  # name -> sha256, as `code_attempts` recorded
    bundle_sha256: str
    tool_module: str
    loop_spec: dict[str, Any]
    handoff_table: str
    schema_sql: bytes
    schema_sql_sha256: str  # manifest `build.schema_sql_sha256`
    dataset: DatasetInput
    environment: str = "unknown"


@dataclass(frozen=True)
class ExecutionResult:
    classified: Classified
    vm_id: str | None
    exit_code: int | None
    outputs_json: bytes | None  # the runner's file as read, for hashing and storage
    logs: bytes  # stdout then stderr, labelled, capped
    truncated: bool
    egress: tuple[EgressEvent, ...]
    destroy_error: str | None = None


def _cases_for_runner(dataset: DatasetInput) -> list[dict[str, Any]]:
    """Each case in the one canonical shape the hash covers, plus its id (its name)."""
    out = []
    for kind, case in sorted(dataset.cases, key=lambda pair: pair[1].name):
        item = case_canonical(kind, case)
        item["case_id"] = case.name
        out.append(item)
    return out


def verify_inputs(run: RunInput) -> dict[str, bytes]:
    """Check every hash and return the verified bundle files. Raises `HashMismatch`."""
    files = verify_bundle(run.bundle_tar, run.bundle_files, run.bundle_sha256)
    verify_sha(run.schema_sql, run.schema_sql_sha256, "schema.sql")
    actual = dataset_sha256(run.dataset.cases, run.dataset.files_hashes, run.dataset.mocks_hashes)
    if not same_hash(actual, run.dataset.expected_sha256):
        raise HashMismatch("dataset")
    return files


def build_request(run: RunInput) -> bytes:
    request = RunnerRequest(
        limits=run.limits,
        mode=run.mode,
        tool_module=run.tool_module,
        loop_spec=run.loop_spec,
        handoff_table=run.handoff_table,
        cases=_cases_for_runner(run.dataset),
        egress_probe=list(run_probe(run)),
    )
    body = request.model_dump_json().encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise SandboxInfraError(InfraReason.DATASET_INVALID, "the request is too large")
    return body


def run_probe(run: RunInput) -> tuple[str, ...]:
    entries = list(run.policy.allow)
    if run.mock_api is not None:
        entries.append(f"{run.mock_api.host}:{run.mock_api.port}")
    return tuple(entries)


def _logs(result: ExecResult | None) -> tuple[bytes, bool]:
    if result is None:
        return b"", False
    out = b"--- stdout ---\n" + result.stdout + b"\n--- stderr ---\n" + result.stderr + b"\n"
    return out[:MAX_LOG_BYTES], result.truncated or len(out) > MAX_LOG_BYTES


def _parse_outputs(raw: bytes | None) -> RunnerOutputs | None:
    if raw is None:
        return None
    try:
        return RunnerOutputs.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None  # a file the runner wrote badly is the same as no file: RUNNER_FAILED


async def execute(
    run: RunInput,
    backend: SandboxBackend,
    *,
    egress_reader: EgressReader | None = None,
    resolver: Resolver = system_resolver,
) -> ExecutionResult:
    if backend.mode is not run.mode:
        # the activity read the mode from config and built the backend from the same value;
        # a disagreement is a wiring bug and must never run
        raise SandboxInfraError(InfraReason.VM_CREATE_FAILED, "backend and run disagree on mode")
    if run.mode.certifying and egress_reader is None:
        raise SandboxInfraError(InfraReason.EGRESS_LOG_UNAVAILABLE, "no egress evidence source")
    normalize_digest(run.runtime_digest)

    bundle_files = verify_inputs(run)  # before any VM exists
    request_body = build_request(run)
    try:
        plan = await plan_network(run.policy, run.mock_api, resolver)
    except EgressPolicyError as exc:
        verdict = Classified(
            FailureCategory.EGRESS_BLOCKED, None, notes=(f"{exc.host}: {exc.why}",)
        )
        return ExecutionResult(verdict, None, None, None, b"", False, ())

    vm = await backend.create(_spec(run, plan))
    result: ExecResult | None = None
    outputs_raw: bytes | None = None
    egress: list[EgressEvent] = []
    classified: Classified
    try:
        classified, result, outputs_raw, egress = await _run_in_vm(
            run, backend, vm, plan, bundle_files, request_body, egress_reader
        )
    except SandboxInfraError as exc:
        classified = infra(exc.reason, str(exc))
    except Exception:  # a backend bug is infrastructure, never a test failure
        log.exception("sandbox run failed unexpectedly", extra={"run_id": str(run.run_id)})
        classified = infra(InfraReason.RUNNER_FAILED, "unexpected error in the executor")
    finally:
        # shielded: a second cancel must not abandon a running VM
        destroy_error = await asyncio.shield(_destroy(backend, vm))

    logs, truncated = _logs(result)
    return ExecutionResult(
        classified,
        vm.vm_id,
        result.exit_code if result is not None else None,
        outputs_raw,
        logs,
        truncated or any(c.truncated for c in _parsed_cases(outputs_raw)),
        tuple(egress),
        destroy_error,
    )


def _parsed_cases(raw: bytes | None) -> list[Any]:
    parsed = _parse_outputs(raw)
    return list(parsed.cases) if parsed is not None else []


def _spec(run: RunInput, plan: NetworkPlan) -> VmSpec:
    deadline = run.limits.case_timeout_s * max(1, len(run.dataset.cases)) + BOOT_GRACE_S
    return VmSpec(
        run_id=run.run_id,
        template_alias=template_alias(run.runtime_digest),
        runtime_digest=normalize_digest(run.runtime_digest),
        limits=run.limits,
        allow_out=plan.allow_out,
        hosts=plan.hosts,
        ttl_seconds=deadline,
        metadata={"sbam_run": str(run.run_id), "environment": run.environment},
    )


async def _run_in_vm(
    run: RunInput,
    backend: SandboxBackend,
    vm: VmHandle,
    plan: NetworkPlan,
    bundle_files: Mapping[str, bytes],
    request_body: bytes,
    egress_reader: EgressReader | None,
) -> tuple[Classified, ExecResult | None, bytes | None, list[EgressEvent]]:
    seen = await backend.read_runtime_digest(vm)
    try:
        same = normalize_digest(seen.strip()) == normalize_digest(run.runtime_digest)
    except ValueError:
        same = False
    if not same:
        wrong = infra(InfraReason.TEMPLATE_DIGEST_MISMATCH, "the VM runs another runtime")
        return wrong, None, None, []

    await backend.put_files(vm, {HOSTS_PATH: render_hosts(plan.hosts)}, system=True)
    files = {f"{BUNDLE_DIR}/{name}": data for name, data in bundle_files.items()}
    files[SCHEMA_PATH] = run.schema_sql
    files[REQUEST_PATH] = request_body
    await backend.put_files(vm, files)

    total_timeout = float(run.limits.case_timeout_s * len(run.dataset.cases) + 30)
    result = await backend.exec(
        vm,
        RUNNER_ARGV,
        timeout_s=total_timeout,
        env={"SBAM_MODE": run.mode.value},
        max_output_bytes=run.limits.max_output_bytes,
    )
    outputs_raw = await backend.get_file(vm, OUTPUTS_PATH, max_bytes=run.limits.max_output_bytes)
    alive = await _alive(backend, vm)
    egress = await egress_reader(vm.vm_id) if egress_reader is not None else []
    classified = classify(
        mode=run.mode,
        expected_digest=run.runtime_digest,
        expected_case_ids=[case.name for _, case in run.dataset.cases],
        exec_result=result,
        outputs=_parse_outputs(outputs_raw),
        vm_alive=alive,
        egress=egress,
    )
    return classified, result, outputs_raw, egress


async def _alive(backend: SandboxBackend, vm: VmHandle) -> bool:
    try:
        return any(info.vm_id == vm.vm_id for info in await backend.list_vms())
    except SandboxInfraError:
        return False


async def _destroy(backend: SandboxBackend, vm: VmHandle) -> str | None:
    try:
        await backend.destroy(vm.vm_id)
    except Exception as exc:  # reported to the activity, which keeps the VM id
        log.error("sandbox destroy failed", extra={"vm_id": vm.vm_id})
        return type(exc).__name__
    return None


__all__ = [
    "DatasetInput",
    "ExecutionResult",
    "RunInput",
    "build_request",
    "execute",
    "verify_inputs",
]
