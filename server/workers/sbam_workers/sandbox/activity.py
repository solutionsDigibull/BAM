"""The real `run_sandbox_tests` stage (E13.2, ADR 0023): one code attempt, one dataset, one sandbox
run.

Registered under the stub's name and queue with the same Pydantic in/out (`BuildStageInput` ->
`IdResult`), so `SubSkillBuild` does not change. The id returned is the `sandbox_runs` row, which is
what certification and the test panel point at.

What the workflow's retry policy needs from this activity (TD §13.3, 20 min, 3 attempts):
  * the tests ran and failed  -> non-retryable `TestsFailed`, with the code attempt as details; the
    LOOP_TESTS gate row carries the findings the corrector reads. A correction is spent.
  * the sandbox is the problem -> `SandboxUnavailable`, retryable (non-retryable when retrying
    cannot change anything: a hash mismatch, a bad dataset, a missing template). After the attempts
    the workflow parks the Sub-Skill in TESTING_BLOCKED. No correction is spent, ever.
  * the tests passed -> the run's id.

Rules worth knowing
  * The row is claimed before anything can fail, so every failure ends on a row. A retried activity
    closes its predecessor as SUPERSEDED and never leaves two live runs; a finished test outcome is
    a replay and is answered, not run again.
  * The VM is destroyed by the executor's `finally`. If the worker dies first, the host's own TTL
    and the reaper (`reaper.py`) are the backstops.
  * A heartbeat every 30 s lets Temporal see a dead worker long before the 20 minute limit.
  * Nothing from the dataset, the bundle or the manifest is logged: ids, counts and categories only.
"""

import asyncio
import contextlib
import logging
import math
import time
import uuid
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import ValidationError
from sbam_api.storage.factory import make_backend
from sbam_contracts.build import TESTS_FAILED, BuildStageInput
from sbam_contracts.pipeline import IdResult
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sandbox import (
    HEARTBEAT_SECONDS,
    EgressPolicy,
    FailureCategory,
    InfraReason,
    SandboxLimits,
)
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.wiring import SqlBuildInputs
from sbam_workers.registry import activity_for
from sbam_workers.sandbox.backend import SandboxInfraError
from sbam_workers.sandbox.classify import Classified
from sbam_workers.sandbox.executor import ExecutionResult, RunInput, execute
from sbam_workers.sandbox.factory import Sandbox, make_sandbox
from sbam_workers.sandbox.load import (
    TOOL_MODULE,
    Identity,
    IsatvonOf,
    Loaded,
    SqlSandboxInputs,
    mismatch,
)
from sbam_workers.sandbox.policy import MockApi
from sbam_workers.sandbox.store import Evidence, SqlRunStore
from sbam_workers.sandbox.verify import HashMismatch
from sbam_workers.settings import WorkerSettings
from sbam_workers.test.mocks import MockApiClient

STAGE: Final = "run_sandbox_tests"
SANDBOX_UNAVAILABLE: Final = "SandboxUnavailable"
HASH_MISMATCH: Final = "HashMismatch"
NOT_RETRYABLE: Final = frozenset(
    {
        InfraReason.HASH_MISMATCH,
        InfraReason.INPUT_MISMATCH,
        InfraReason.DATASET_INVALID,
        InfraReason.TEMPLATE_DIGEST_MISMATCH,
    }
)
HEARTBEATS_PER_TIMEOUT: Final = 3
MAX_JOURNAL_BYTES: Final = 64 * 1024
log = logging.getLogger("sbam_workers.sandbox")


def unavailable(reason: InfraReason, detail: str = "") -> ApplicationError:
    message = f"the sandbox is unavailable ({reason.value})" + (f": {detail}" if detail else "")
    return ApplicationError(
        message, type=SANDBOX_UNAVAILABLE, non_retryable=reason in NOT_RETRYABLE
    )


def loop_tests_failed(identity: Identity) -> ApplicationError:
    return ApplicationError(
        "the tests ran and failed",
        [str(identity.attempt_id)],
        type=TESTS_FAILED,
        non_retryable=True,
    )


def limits_for(loop_spec: Mapping[str, Any]) -> SandboxLimits:
    """Room for the bound the Sub-Skill declared: `max_iterations` passes of two tools, each allowed
    `budget_ms`, plus a margin. Never below the default. A Sub-Skill that needs more than the
    policy's
    ceiling can never be tested here, and retrying will not change that: refused, not clamped."""
    seconds = math.ceil(
        int(loop_spec["max_iterations"]) * 2 * int(loop_spec["budget_ms"]) / 1000 + 10
    )
    try:
        return SandboxLimits(case_timeout_s=max(SandboxLimits().case_timeout_s, seconds))
    except ValidationError:
        raise mismatch(
            "the loop's declared bounds need more time per case than the sandbox allows"
        ) from None


def mock_api_of(settings: WorkerSettings) -> MockApi | None:
    if not settings.mock_api_url or not settings.mock_api_ip:
        return None
    parts = urlsplit(settings.mock_api_url)
    if not parts.hostname:
        return None
    return MockApi(parts.hostname, settings.mock_api_ip, parts.port or 80)


class RunSandboxTests:
    """`run_sandbox_tests` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        inputs: SqlSandboxInputs,
        store: SqlRunStore,
        isatvon_of: IsatvonOf,
        settings: WorkerSettings,
        sandbox_factory: Callable[[WorkerSettings], Sandbox] = make_sandbox,
        mock: MockApiClient | None = None,
    ) -> None:
        self.inputs = inputs
        self.store = store
        self.isatvon_of = isatvon_of
        self.settings = settings
        self.sandbox_factory = sandbox_factory
        self.mock = mock

    async def _heartbeat(self, run_id: str) -> None:
        """HEARTBEAT_SECONDS is how long Temporal waits (the stage's heartbeat timeout): three beats
        inside it mean one late beat never reads as a dead worker."""
        while True:
            activity.heartbeat(run_id)
            await asyncio.sleep(HEARTBEAT_SECONDS / HEARTBEATS_PER_TIMEOUT)

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        info = activity.info()
        if info.workflow_id is None:
            raise RuntimeError(f"{STAGE} runs only inside a workflow")
        identity = await asyncio.to_thread(self.inputs.identify, inp)
        try:
            sandbox = self.sandbox_factory(self.settings)
        except SandboxInfraError as exc:  # no mode or host settings: there is no row to write yet
            log.error("the sandbox is not configured", extra={"reason": exc.reason.value})
            raise unavailable(exc.reason) from None
        claimed = await asyncio.to_thread(
            self.store.claim,
            inp,
            identity,
            sandbox.mode,
            workflow_id=info.workflow_id,
            activity_id=info.activity_id,
            activity_attempt=info.attempt,
        )
        if claimed.replay:  # the answer is already on the row; the code is not run twice
            if claimed.failure_category is not None:
                raise loop_tests_failed(identity)
            return IdResult(id=claimed.run_id)
        beat = asyncio.create_task(self._heartbeat(str(claimed.run_id)))
        try:
            return await self._run(inp, identity, sandbox, claimed.run_id)
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

    async def _finish_infra(
        self,
        inp: BuildStageInput,
        identity: Identity,
        run_id: uuid.UUID,
        reason: InfraReason,
        *,
        vm_id: str | None = None,
        started: float,
        evidence: Evidence | None = None,
        exit_code: int | None = None,
    ) -> None:
        await asyncio.to_thread(
            self.store.finish,
            inp,
            identity,
            run_id,
            category=FailureCategory.INFRA_ERROR,
            reason=reason,
            exit_code=exit_code,
            vm_id=vm_id,
            duration_ms=round((time.monotonic() - started) * 1000),
            evidence=evidence,
            classified=None,
            bundle_sha256=None,
            dataset_sha256=None,
        )

    async def _run(
        self, inp: BuildStageInput, identity: Identity, sandbox: Sandbox, run_id: uuid.UUID
    ) -> IdResult:
        started = time.monotonic()
        mock_installed = False
        try:
            isatvon = await self.isatvon_of(inp)
            loaded = await asyncio.to_thread(self.inputs.load, inp, identity, isatvon)
            mock_installed = await self._install_mocks(run_id, loaded)

            async def created(vm_id: str) -> None:
                await asyncio.to_thread(self.store.record_vm, inp, run_id, vm_id)

            result = await execute(
                self._run_input(run_id, identity, sandbox, loaded),
                sandbox.backend,
                egress_reader=sandbox.egress_reader,
                on_created=created,
            )
            logs = result.logs + await self._journal(run_id, mock_installed)
        except HashMismatch as exc:
            log.error(
                "a sandbox input does not match its recorded hash: no VM was created",
                extra={
                    "alert": "security",
                    "what": exc.what,
                    "tenant_id": str(inp.tenant_id),
                    "project_id": str(inp.project_id),
                    "sub_skill_id": str(inp.sub_skill_id),
                    "code_attempt_id": str(identity.attempt_id),
                    "correlation_id": inp.correlation_id,
                },
            )
            await self._finish_infra(
                inp, identity, run_id, InfraReason.HASH_MISMATCH, started=started
            )
            raise ApplicationError(
                f"{exc.what} does not match its recorded hash",
                type=HASH_MISMATCH,
                non_retryable=True,
            ) from None
        except ApplicationError:  # only the loaders raise these: an input is not what was recorded
            await self._finish_infra(
                inp, identity, run_id, InfraReason.INPUT_MISMATCH, started=started
            )
            raise
        except SandboxInfraError as exc:
            await self._finish_infra(inp, identity, run_id, exc.reason, started=started)
            raise unavailable(exc.reason) from None
        except asyncio.CancelledError:
            await asyncio.shield(
                self._finish_infra(inp, identity, run_id, InfraReason.CANCELLED, started=started)
            )
            raise
        except Exception as exc:
            log.exception("the sandbox run failed unexpectedly", extra={"run_id": str(run_id)})
            await self._finish_infra(
                inp, identity, run_id, InfraReason.RUNNER_FAILED, started=started
            )
            raise unavailable(InfraReason.RUNNER_FAILED, type(exc).__name__) from None
        finally:
            if mock_installed and self.mock is not None:
                with contextlib.suppress(Exception):  # the control side being down loses nothing
                    await asyncio.to_thread(self.mock.remove, str(run_id))
        return await self._conclude(inp, identity, run_id, loaded, result, logs, started)

    async def _conclude(
        self,
        inp: BuildStageInput,
        identity: Identity,
        run_id: uuid.UUID,
        loaded: Loaded,
        result: ExecutionResult,
        logs: bytes,
        started: float,
    ) -> IdResult:
        classified: Classified = result.classified
        if result.destroy_error is not None:  # the VM id is on the row; the reaper finishes the job
            log.error(
                "a sandbox VM was not destroyed",
                extra={"alert": "operator", "vm_id": result.vm_id, "run_id": str(run_id)},
            )
        evidence = Evidence(result.outputs_json, logs, result.egress, result.truncated)
        if classified.category is FailureCategory.INFRA_ERROR:
            reason = classified.infra_reason or InfraReason.RUNNER_FAILED
            await self._finish_infra(
                inp,
                identity,
                run_id,
                reason,
                vm_id=result.vm_id,
                started=started,
                evidence=evidence,
                exit_code=result.exit_code,
            )
            raise unavailable(reason, "; ".join(classified.notes))
        await asyncio.to_thread(
            self.store.finish,
            inp,
            identity,
            run_id,
            category=classified.category,
            reason=None,
            exit_code=result.exit_code,
            vm_id=result.vm_id,
            duration_ms=round((time.monotonic() - started) * 1000),
            evidence=evidence,
            classified=classified,
            bundle_sha256=loaded.bundle_sha256,
            dataset_sha256=loaded.dataset.expected_sha256,
        )
        activity.logger.info(
            "sandbox run finished",
            extra={
                "run_id": str(run_id),
                "passed": classified.passed,
                "category": classified.category.value if classified.category else None,
                "cases": len(classified.cases),
                "tenant_id": str(inp.tenant_id),
                "project_id": str(inp.project_id),
                "sub_skill_id": str(inp.sub_skill_id),
                "correlation_id": inp.correlation_id,
            },
        )
        if classified.passed:
            return IdResult(id=run_id)
        raise loop_tests_failed(identity)

    def _run_input(
        self, run_id: uuid.UUID, identity: Identity, sandbox: Sandbox, loaded: Loaded
    ) -> RunInput:
        mock = mock_api_of(self.settings) if loaded.mocks else None
        return RunInput(
            run_id=run_id,
            mode=sandbox.mode,
            runtime_digest=identity.runtime_digest,
            limits=limits_for(loaded.loop_spec),
            policy=EgressPolicy(
                allow=[]
            ),  # a Sub-Skill reaches the outside only through the mock API
            mock_api=mock,
            bundle_tar=loaded.bundle_tar,
            bundle_files=loaded.bundle_files,
            bundle_sha256=loaded.bundle_sha256,
            tool_module=TOOL_MODULE,
            loop_spec=loaded.loop_spec,
            harness=loaded.harness,
            handoff_table=loaded.handoff_table,
            schema_sql=loaded.schema_sql,
            schema_sql_sha256=loaded.schema_sql_sha256,
            dataset=loaded.dataset,
            environment=self.settings.sbam_env,
            data_files=loaded.data_files,
        )

    async def _install_mocks(self, run_id: uuid.UUID, loaded: Loaded) -> bool:
        if self.mock is None or not loaded.mocks:
            return False
        await asyncio.to_thread(self.mock.install, str(run_id), loaded.mocks)
        return True

    async def _journal(self, run_id: uuid.UUID, installed: bool) -> bytes:
        """What the tools asked the mock API for, appended to the run's logs (capped)."""
        if not installed or self.mock is None:
            return b""
        try:
            journal = await asyncio.to_thread(self.mock.journal, str(run_id))
        except Exception:  # evidence is nice to have here; the verdict does not depend on it
            return b"\n--- mock journal unavailable ---\n"
        body = journal.model_dump_json().encode()[:MAX_JOURNAL_BYTES]
        return b"\n--- mock journal ---\n" + body + b"\n"


@lru_cache(maxsize=1)
def _stage() -> RunSandboxTests:
    settings, sessions = WorkerSettings(), mirror.sessions()  # type: ignore[call-arg]
    storage = make_backend(settings)
    mock = (
        MockApiClient(settings.mock_api_url, settings.mock_control_token.get_secret_value())
        if settings.mock_api_url and settings.mock_control_token
        else None
    )
    return RunSandboxTests(
        SqlSandboxInputs(sessions, storage),
        SqlRunStore(sessions, storage),
        SqlBuildInputs(sessions).tests_sub_skill,
        settings,
        mock=mock,
    )


@activity_for(TaskQueue.TEST)
@activity.defn(name=STAGE)
async def run_sandbox_tests_stage_activity(inp: BuildStageInput) -> IdResult:
    """Run the dataset against the stored bundle in the configured sandbox (E13.2)."""
    return await _stage().run(inp)


__all__ = ["STAGE", "RunSandboxTests", "run_sandbox_tests_stage_activity"]
