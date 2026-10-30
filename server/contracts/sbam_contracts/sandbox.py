"""Sandbox contracts: modes, failure categories, limits, egress policy, run record (ADR 0023).

What the worker, the API and the in-VM runner agree on (E13.2). Nothing here does I/O.

Rules worth knowing
  * The mode comes from the worker's environment and nowhere else. `SandboxMode.certifying` is the
    only place that says which mode may certify; a run row stores the mode, never a flag that could
    disagree with it.
  * One digest spelling: `normalize_digest` turns `sha256:<hex>` and bare `<hex>` (both appear in
    the manifest contract) into `sha256:<hex>`. The template alias, the registry lookup and the
    in-VM check all go through it, so one digest has one spelling everywhere.
  * An egress allowlist entry is `host:port` with a real DNS name: lowercase ASCII, at least two
    labels, a TLD that starts with a letter, no wildcard, no IP literal in any spelling (dotted,
    decimal, hex, octal, IPv6) and no private-use suffix. Platform entries (the mock API) may be
    one label and are parsed separately, so a manifest can never reach an internal service name.
  * Failure categories split into test failures (the code under test is wrong, a correction is
    spent) and `INFRA_ERROR` (the sandbox is wrong, nothing is spent, the run is retried).
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from sbam_contracts._jsonschema import contract_schema

SANDBOX_USER: Final = "sbam"  # uid 10001 in skill-runtime; tests never run as root
ALIAS_PREFIX: Final = "sbam-rt-"
ALIAS_MAX_LENGTH: Final = 64  # CubeSandbox's alias limit
ALIAS_HEX_LENGTH: Final = ALIAS_MAX_LENGTH - len(ALIAS_PREFIX)  # 56 hex = 224 bits of the digest
VM_TAG_KEY: Final = "sbam_run"  # metadata every VM carries, so the reaper finds only ours
RUNNER_SCHEMA_VERSION: Final = 1

MAX_EGRESS_ENTRIES: Final = 50
MAX_OUTPUT_BYTES: Final = 10 * 1024 * 1024  # per case, TD E13.2: truncate with a flag, still hash
HEARTBEAT_SECONDS: Final = 30

# `fullmatch`, never `$`: Python's `$` also matches before a trailing newline.
_DIGEST = re.compile(r"(?:sha256:)?([0-9a-f]{64})")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TLD = re.compile(r"[a-z][a-z0-9-]*")  # not all-numeric, so 127.1 and 0x7f.1 are not names
_PRIVATE_SUFFIXES: Final = (
    "local",
    "localhost",
    "localdomain",
    "internal",
    "lan",
    "home",
    "corp",
    "intranet",
    "private",
    "arpa",
)


class SandboxMode(StrEnum):
    CUBESANDBOX = "CUBESANDBOX"
    LOCAL_DOCKER_NONCERT = "LOCAL_DOCKER_NONCERT"

    @property
    def certifying(self) -> bool:
        return self is SandboxMode.CUBESANDBOX


class FailureCategory(StrEnum):
    IMPORT_ERROR = "IMPORT_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    OOM = "OOM"
    OUTPUT_SCHEMA_MISMATCH = "OUTPUT_SCHEMA_MISMATCH"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    EGRESS_BLOCKED = "EGRESS_BLOCKED"
    INFRA_ERROR = "INFRA_ERROR"

    @property
    def infrastructure(self) -> bool:
        return self is FailureCategory.INFRA_ERROR


class InfraReason(StrEnum):
    """Why a run is INFRA_ERROR; stored in the run row so an operator sees the cause."""

    NOT_CONFIGURED = "NOT_CONFIGURED"  # no sandbox mode or host settings: nothing can run
    HOST_UNREACHABLE = "HOST_UNREACHABLE"
    HASH_MISMATCH = "HASH_MISMATCH"  # an input changed after the gates: no VM was created
    # an input is not what the build recorded (a gate did not pass, the schema is not the
    # manifest's)
    INPUT_MISMATCH = "INPUT_MISMATCH"
    # no template for this digest, or the wrong one
    TEMPLATE_DIGEST_MISMATCH = "TEMPLATE_DIGEST_MISMATCH"
    VM_CREATE_FAILED = "VM_CREATE_FAILED"
    VM_BOOT_TIMEOUT = "VM_BOOT_TIMEOUT"
    RUNNER_FAILED = "RUNNER_FAILED"  # the in-VM runner died or wrote nothing usable
    DATASET_INVALID = "DATASET_INVALID"
    EGRESS_LOG_UNAVAILABLE = "EGRESS_LOG_UNAVAILABLE"  # certifying mode without an evidence source
    DESTROY_FAILED = "DESTROY_FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"  # a retry of the activity took the run over; this one lost


class SandboxRunStatus(StrEnum):
    STARTED = "STARTED"
    FINISHED = "FINISHED"


class SandboxContractError(ValueError):
    """A value the sandbox contracts refuse."""


def normalize_digest(value: str) -> str:
    """`sha256:<64 hex>` from either spelling; anything else raises."""
    match = _DIGEST.fullmatch(value)
    if match is None:
        raise SandboxContractError("not a sha256 digest")
    return f"sha256:{match.group(1)}"


def template_alias(digest: str) -> str:
    """The CubeSandbox template alias bound to a skill-runtime digest (ADR 0023, decision 4).

    It carries the first 56 hex characters (the alias limit is 64). That is a lookup key, not the
    proof: the in-VM check compares the whole digest, so a prefix collision would still be caught.
    """
    return ALIAS_PREFIX + normalize_digest(digest).removeprefix("sha256:")[:ALIAS_HEX_LENGTH]


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SandboxLimits(_Dto):
    """VM resources and time. Anything outside the policy is rejected, never clamped."""

    cpu: Annotated[int, Field(ge=1, le=2)] = 1
    memory_mb: Annotated[int, Field(ge=512, le=2048)] = 1024
    case_timeout_s: Annotated[int, Field(ge=10, le=300)] = 60
    max_output_bytes: Annotated[int, Field(ge=1024, le=MAX_OUTPUT_BYTES)] = MAX_OUTPUT_BYTES


class EgressTarget(_Dto):
    host: str
    port: Annotated[int, Field(ge=1, le=65535)]

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"


def _check_host(host: str, *, platform: bool) -> None:
    if not host.isascii() or host != host.lower():
        raise SandboxContractError("host must be lowercase ASCII (use punycode for IDNs)")
    if len(host) > 253 or host.endswith(".") or host.startswith("."):
        raise SandboxContractError("host is too long or has an empty label")
    labels = host.split(".")
    if not all(_LABEL.fullmatch(label) for label in labels):
        raise SandboxContractError("host has an invalid label")
    if len(labels) < 2:
        if platform:
            return  # a platform service name such as `sbam-mock-api`
        raise SandboxContractError("host must be a fully qualified name")
    if not _TLD.fullmatch(labels[-1]):
        raise SandboxContractError("host must end in a name, not a number")
    if labels[-1] in _PRIVATE_SUFFIXES:
        raise SandboxContractError("private-use suffixes are not allowed")


def parse_egress_target(text: str, *, platform: bool = False) -> EgressTarget:
    """`host:port` -> a target, or raise `SandboxContractError`.

    `platform` allows one-label names (a platform service such as the mock API)."""
    if text != text.strip() or text.count(":") != 1:
        raise SandboxContractError("an egress entry is exactly host:port")
    host, _, port_text = text.partition(":")
    if not port_text.isascii() or not port_text.isdigit() or port_text.startswith("0"):
        raise SandboxContractError("the port must be a number without leading zeros")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise SandboxContractError("the port is out of range")
    _check_host(host, platform=platform)
    return EgressTarget(host=host, port=port)


class EgressPolicy(_Dto):
    """`runtime.network.allowlist` of a Sub-Skill. The platform adds the mock API on its own."""

    allow: Annotated[list[str], Field(max_length=MAX_EGRESS_ENTRIES)] = Field(default_factory=list)

    @field_validator("allow")
    @classmethod
    def _entries(cls, value: list[str]) -> list[str]:
        targets = []
        for entry in value:
            try:
                targets.append(str(parse_egress_target(entry)))
            except SandboxContractError as exc:
                raise ValueError(f"{entry!r}: {exc}") from None
        if len(set(targets)) != len(targets):
            raise ValueError("duplicate egress entries")
        return targets

    def targets(self) -> list[EgressTarget]:
        return [parse_egress_target(entry) for entry in self.allow]


class EgressVerdict(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"  # an attempt was seen but its verdict is not known to this source


class EgressSource(StrEnum):
    L7_PROXY = "L7_PROXY"  # CubeEgress access.jsonl: HTTP and HTTPS only, authoritative
    GUEST_PROBE = "GUEST_PROBE"  # logged inside the VM: evidence of an attempt, not a control
    HOST_TAP = "HOST_TAP"  # captured on the VM's tap device by the host collector


class EgressEvent(_Dto):
    vm_id: Annotated[str, Field(min_length=1, max_length=128)]
    at: datetime
    dst_host: Annotated[str, Field(min_length=1, max_length=253)]
    dst_port: Annotated[int, Field(ge=0, le=65535)]
    verdict: EgressVerdict
    source: EgressSource
    reason: Annotated[str | None, Field(max_length=100)] = None  # e.g. PRIVATE_DESTINATION


class SandboxRun(_Dto):
    """One row of `sandbox_runs` as the API returns it (TD §7.7)."""

    id: UUID
    code_attempt_id: UUID
    dataset_id: UUID
    run_no: Annotated[int, Field(ge=1)]
    mode: SandboxMode
    status: SandboxRunStatus
    runtime_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    vm_id: str | None = None
    duration_ms: Annotated[int | None, Field(ge=0)] = None
    exit_code: int | None = None
    failure_category: FailureCategory | None = None
    infra_reason: InfraReason | None = None
    egress_log_key: str | None = None
    outputs_key: str | None = None
    logs_key: str | None = None
    truncated: bool = False
    started_at: datetime
    finished_at: datetime | None = None

    @property
    def certifying(self) -> bool:
        """Only a finished CubeSandbox run with no failure may count towards certification."""
        return (
            self.mode.certifying
            and self.status is SandboxRunStatus.FINISHED
            and self.failure_category is None
        )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        finished = self.status is SandboxRunStatus.FINISHED
        if finished != (self.finished_at is not None):
            raise ValueError("finished_at is set exactly when the run is FINISHED")
        if (self.infra_reason is not None) != (
            self.failure_category is FailureCategory.INFRA_ERROR
        ):
            raise ValueError("infra_reason is set exactly for INFRA_ERROR")
        return self


# --- the in-VM runner's files (ADR 0023, decision 7) ---------------------------------------
class RunnerCaseResult(_Dto):
    """One test case's outcome. `trace` is a `HarnessTrace` object, validated by the worker.

    `handoff_rows` is the case's append-only table as the database holds it after the run (the
    "handoff rows dump" of the issue): evidence of what the bundle really wrote, not what the
    harness counted."""

    case_id: Annotated[str, Field(min_length=1, max_length=200)]
    expected_terminal: Literal["SUCCESS", "ESCALATED", "NO_OP"]
    terminal: Literal["SUCCESS", "ESCALATED", "NO_OP"] | None
    passed: bool
    failure_category: FailureCategory | None = None
    truncated: bool = False
    trace: dict[str, JsonValue]
    handoff_rows: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=1000)


class RunnerResources(_Dto):
    wall_ms: Annotated[int, Field(ge=0)]
    cpu_ms: Annotated[int, Field(ge=0)]
    peak_rss_kb: Annotated[int, Field(ge=0)]


class RunnerOutputs(_Dto):
    """`/work/out/outputs.json`, written by the runner as its last act."""

    schema_version: Literal[1] = RUNNER_SCHEMA_VERSION
    runtime_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    cases: list[RunnerCaseResult]
    resources: RunnerResources
    mode: SandboxMode

    @field_validator("cases")
    @classmethod
    def _unique_cases(cls, value: list[RunnerCaseResult]) -> list[RunnerCaseResult]:
        ids = [case.case_id for case in value]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case ids")
        return value


_IDENT = r"^[A-Za-z_][A-Za-z0-9_]{0,99}$"


class HarnessConfig(_Dto):
    """What the harness needs from the Sub-Skill's ISATVON that the loop spec does not carry:
    which two tools drive a pass, how fresh state is keyed, what a handoff row holds and what the
    Sub-Skill says to the user. Built by the worker from the stored Sub-Skill, never from the
    bundle under test."""

    action_tool: Annotated[str, Field(pattern=_IDENT)]
    fixed_check_tool: Annotated[str, Field(pattern=_IDENT)]
    fresh_state_parameters: list[Annotated[str, Field(pattern=_IDENT)]] = Field(max_length=50)
    required_inputs: list[Annotated[str, Field(pattern=_IDENT)]] = Field(max_length=50)
    key_field: Annotated[str, Field(pattern=_IDENT)]  # the input that keys fresh state
    handoff_fields: list[Annotated[str, Field(pattern=_IDENT)]] = Field(min_length=1, max_length=50)
    ui_updates: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=20)

    @model_validator(mode="after")
    def _tools_differ(self) -> Self:
        if self.action_tool == self.fixed_check_tool:
            raise ValueError("the action and the check tool must be different tools")
        return self


class RunnerRequest(_Dto):
    """`/work/request.json`, the only way data reaches the runner besides the files it reads."""

    schema_version: Literal[1] = RUNNER_SCHEMA_VERSION
    limits: SandboxLimits
    mode: SandboxMode
    tool_module: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$", max_length=200)]
    loop_spec: dict[str, JsonValue]
    harness: HarnessConfig
    handoff_table: Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")]
    cases: list[dict[str, JsonValue]] = Field(min_length=1)
    egress_probe: list[str] = Field(default_factory=list, max_length=MAX_EGRESS_ENTRIES)

    @model_validator(mode="after")
    def _probe_entries(self) -> Self:
        for entry in self.egress_probe:
            parse_egress_target(entry, platform=True)
        return self


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/sandbox/`."""
    return {
        "limits": contract_schema(SandboxLimits, "sandbox/limits"),
        "egress_policy": contract_schema(EgressPolicy, "sandbox/egress_policy"),
        "egress_event": contract_schema(EgressEvent, "sandbox/egress_event"),
        "run": contract_schema(SandboxRun, "sandbox/run"),
        "runner_request": contract_schema(RunnerRequest, "sandbox/runner_request"),
        "runner_outputs": contract_schema(RunnerOutputs, "sandbox/runner_outputs"),
    }
