"""The stored bundle of one code attempt, found, read and verified for a code gate (E12.4).

The AST gate does the same steps inline (it also needs the template and the manifest); this is the
shape for a gate that only needs the bytes. A code attempt has one bundle for ever (`assemble`
writes it once), and the stored object is checked against the hashes recorded at assembly before a
gate looks at it: a mismatch is `BUNDLE_TAMPERED`, never a verdict. The verdicts are `gate_results`
rows, one per gate and build stage key, each carrying the attempt and the `bundle_sha256` it judged;
a row about another attempt or another bundle is refused, never reused.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sbam_api.builds.models import CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.storage.base import StorageBackend, StorageIntegrityError
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

from sbam_workers.build.gates.ast_checks import BundleError, HashMismatchError, read_bundle
from sbam_workers.build.gates.errors import BUNDLE_TAMPERED, GATE_INPUT_MISMATCH


def fail(message: str, attempt_id: uuid.UUID, code: str) -> ApplicationError:
    """Non-retryable; details[0] names the attempt (the same shape `GateFailed` carries)."""
    return ApplicationError(message, [str(attempt_id)], type=code, non_retryable=True)


@dataclass(frozen=True)
class Assembled:
    """What the code attempt row says about its bundle."""

    attempt_id: uuid.UUID
    bundle_key: str
    bundle_sha: str
    hashes: Mapping[str, str]


@dataclass(frozen=True)
class StoredBundle:
    attempt_id: uuid.UUID
    bundle_sha: str
    files: Mapping[str, bytes]
    tar: bytes


@dataclass(frozen=True)
class Verdict:
    """One gate's result on a bundle, as `gate_results` keeps it."""

    gate: GateName
    status: GateStatus
    findings: list[dict[str, Any]]
    tool_version: str
    config_sha256: str


def _ctx(inp: BuildStageInput) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def find_assembled(sessions: sessionmaker[Session], inp: BuildStageInput) -> Assembled:
    with sessions() as session:
        row = CodeAttemptRepository(session, _ctx(inp)).find(
            inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
        )
        if row is None:  # assemble always runs first; a missing row is a wiring bug
            raise ApplicationError(
                f"no code_attempts row for {inp.sub_skill_id} build {inp.build_no}",
                type=GATE_INPUT_MISMATCH,
                non_retryable=True,
            )
        attempt_id, status = row.id, row.status
        bundle_key, bundle_sha = row.bundle_key, row.bundle_sha256
        hashes: dict[str, str] = dict(row.files or {})
    if status != CodeAttemptStatus.ASSEMBLED or not bundle_key or not bundle_sha:
        message = f"code_attempts {attempt_id} is {status}, not assembled"
        raise fail(message, attempt_id, GATE_INPUT_MISMATCH)
    return Assembled(attempt_id, bundle_key, bundle_sha, hashes)


def read_verified(storage: StorageBackend, assembled: Assembled) -> StoredBundle:
    try:  # a read failure is retryable and gives no verdict; a corrupt object never will pass
        tar = storage.get(assembled.bundle_key, verify=True)
    except StorageIntegrityError as exc:
        message = "stored bundle fails its content hash"
        raise fail(message, assembled.attempt_id, BUNDLE_TAMPERED) from exc
    try:
        files = read_bundle(tar, dict(assembled.hashes), assembled.bundle_sha)
    except HashMismatchError as exc:
        raise fail(str(exc), assembled.attempt_id, BUNDLE_TAMPERED) from exc
    except BundleError as exc:
        raise fail(str(exc), assembled.attempt_id, GATE_INPUT_MISMATCH) from exc
    return StoredBundle(assembled.attempt_id, assembled.bundle_sha, files, tar)


def record_verdicts(
    sessions: sessionmaker[Session],
    inp: BuildStageInput,
    bundle: StoredBundle,
    verdicts: Sequence[Verdict],
) -> list[GateStatus]:
    """Insert once per gate and stage key; a retried activity gets the rows it already wrote. A row
    that is about another attempt or another bundle is refused."""
    with sessions() as session:
        repo = GateResultRepository(session, _ctx(inp))
        stored = [
            repo.record_stage(
                {
                    "sub_skill_id": inp.sub_skill_id,
                    "code_attempt_id": bundle.attempt_id,
                    "gate": v.gate.value,
                    "status": v.status.value,
                    "findings": v.findings,
                    "tool_version": v.tool_version,
                    "config_sha256": v.config_sha256,
                    "build_no": inp.build_no,
                    "cycle": inp.cycle,
                    "attempt": inp.attempt,
                    "bundle_sha256": bundle.bundle_sha,
                    "created_by": "SYSTEM",
                }
            )
            for v in verdicts
        ]
        if any(
            row.code_attempt_id != bundle.attempt_id or row.bundle_sha256 != bundle.bundle_sha
            for row in stored
        ):
            message = "a recorded verdict is about another attempt or another bundle"
            raise fail(message, bundle.attempt_id, GATE_INPUT_MISMATCH)
        session.commit()
        return [GateStatus(row.status) for row in stored]
