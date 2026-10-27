"""The `ast_gate` build stage (E12.2): AST_SYNTAX, AST_SKELETON, AST_CONFORMANCE on a bundle.

The verdict is keyed by (code attempt, gate, `bundle_sha256`): a retry reads the rows it already
wrote, a changed bundle is judged afresh, and a verdict is never reused for other bytes. The stage
re-reads the bundle from `StorageBackend` and verifies it against the hashes recorded at assembly
before looking at a line of code; a bundle that no longer matches fails the activity (retryable
on a read error, non-retryable `BUNDLE_TAMPERED` on a hash mismatch) and is never reported as a
pass. Any failed gate raises the non-retryable `GateFailed`, details[0] = the attempt id, whose
`gate_results` rows hold the findings (the correction loop, E13, reads them).

Not registered yet, like `Assemble`: whoever wires the build stages (#107) registers
`AstGate(...).run`; `stubs.ast_gate` stays registered meanwhile.
"""

import ast
import uuid
from collections.abc import Awaitable, Callable, Mapping

from sbam_api.builds.models import CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository, GateResultRepository
from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import StorageBackend, StorageIntegrityError
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.pipeline import IdResult
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.assemble import AssembleInputs
from sbam_workers.build.gates.ast_checks import (
    CONFIG_SHA256,
    GATE_VERSION,
    BundleError,
    CheckResult,
    HashMismatchError,
    parse_file,
    read_bundle,
    run_ast_checks,
)
from sbam_workers.build.gates.ast_rules import run_rule_checks

STAGE = "ast_gate"
INPUT_MISMATCH = "AST_GATE_INPUT_MISMATCH"
BUNDLE_TAMPERED = "BUNDLE_TAMPERED"
RULE_GATES = (
    GateName.AST_DECOUPLING,
    GateName.AST_NO_MANUAL_LOOP,
    GateName.AST_HANDOFF,
    GateName.AST_IMPORTS,
    GateName.AST_SQL_SAFETY,
)
GATES = (GateName.AST_SYNTAX, GateName.AST_SKELETON, GateName.AST_CONFORMANCE, *RULE_GATES)


def _fail(message: str, attempt_id: uuid.UUID, code: str) -> ApplicationError:
    return ApplicationError(message, [str(attempt_id)], type=code, non_retryable=True)


def gate_failed(attempt_id: uuid.UUID) -> ApplicationError:
    return _fail("generated code failed the AST gate", attempt_id, GATE_FAILED)


class AstGate:
    """`ast_gate` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        inputs: Callable[[BuildStageInput], Awaitable[AssembleInputs]],
        storage: StorageBackend,
        sessions: sessionmaker[Session],
    ) -> None:
        self.inputs = inputs
        self.storage = storage
        self.sessions = sessions

    def _ctx(self, inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def _record(
        self,
        inp: BuildStageInput,
        attempt_id: uuid.UUID,
        bundle_sha: str,
        results: list[CheckResult],
    ) -> list[GateStatus]:
        with self.sessions() as session:
            repo = GateResultRepository(session, self._ctx(inp))
            stored = [
                repo.record(
                    {
                        "code_attempt_id": attempt_id,
                        "gate": r.gate.value,
                        "status": r.status.value,
                        "findings": [f.as_dict() for f in r.findings],
                        "tool_version": GATE_VERSION,
                        "config_sha256": CONFIG_SHA256,
                        "bundle_sha256": bundle_sha,
                    }
                )
                for r in results
            ]
            session.commit()
            return [GateStatus(row.status) for row in stored]

    def _rules(
        self, files: Mapping[str, bytes], given: AssembleInputs, syntax: CheckResult
    ) -> list[CheckResult]:
        """E12.3: the five rule gates; SKIPPED when a file does not parse (no tree to judge)."""
        if syntax.status is not GateStatus.PASSED:
            return [CheckResult(g, GateStatus.SKIPPED) for g in RULE_GATES]
        trees: dict[str, ast.Module] = {}
        for name in sorted(files):
            if name.endswith(".py"):
                tree, _ = parse_file(name, files[name])
                if tree is not None:
                    trees[name] = tree
        return list(run_rule_checks(trees, given.params, given.template, given.modules))

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        with self.sessions() as session:
            row = CodeAttemptRepository(session, self._ctx(inp)).find(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
            if row is None:  # assemble always runs first; a missing row is a wiring bug
                raise ApplicationError(
                    f"no code_attempts row for {inp.sub_skill_id} build {inp.build_no}",
                    type=INPUT_MISMATCH,
                    non_retryable=True,
                )
            attempt_id, status = row.id, row.status
            template_ref = (row.template_name, row.template_version, row.template_sha256)
            manifest_id: uuid.UUID | None = row.manifest_id
            bundle_key: str | None = row.bundle_key
            hashes: dict[str, str] = dict(row.files or {})
            bundle_sha: str | None = row.bundle_sha256
        if status != CodeAttemptStatus.ASSEMBLED or not bundle_key or not bundle_sha:
            message = f"code_attempts {attempt_id} is {status}, not assembled"
            raise _fail(message, attempt_id, INPUT_MISMATCH)
        given = await self.inputs(inp)
        t = given.template
        if template_ref != (t.name, t.version, t.sha256) or manifest_id != given.manifest_id:
            raise _fail("skeleton does not match the code attempt", attempt_id, INPUT_MISMATCH)
        try:  # a read failure is retryable and gives no verdict; a corrupt object never will pass
            tar = self.storage.get(bundle_key, verify=True)
        except StorageIntegrityError as exc:
            message = "stored bundle fails its content hash"
            raise _fail(message, attempt_id, BUNDLE_TAMPERED) from exc
        try:
            files = read_bundle(tar, hashes, bundle_sha)
        except HashMismatchError as exc:
            raise _fail(str(exc), attempt_id, BUNDLE_TAMPERED) from exc
        except BundleError as exc:
            raise _fail(str(exc), attempt_id, INPUT_MISMATCH) from exc
        results = list(run_ast_checks(files, given.skeleton, given.template, given.params))
        results += self._rules(files, given, results[0])
        recorded = self._record(inp, attempt_id, bundle_sha, results)
        if any(s is not GateStatus.PASSED for s in recorded):
            failed = zip(GATES, recorded, strict=True)
            bad = [g.value for g, s in failed if s is not GateStatus.PASSED]
            activity.logger.info("generated code failed the AST gate", extra={"gates": bad})
            raise gate_failed(attempt_id)
        return IdResult(id=attempt_id)
