"""E12.2 stage tests: `ast_gate` against a real (SQLite) database and a disk storage backend.

`assemble` (E11.3) runs first and leaves the stored bundle and the ASSEMBLED row; the gate is then
run on it, or on it after the stored bytes or the row were tampered with.
"""

import asyncio
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from codegen_cases import ms01_loop_spec, ms01_params
from llm_db import llm_sessions
from sbam_api.artifacts.models import Artifact
from sbam_api.builds.models import CodeAttempt, CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.models import GateResultRow
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_workers.build.assemble import Assemble, AssembleInputs, SqlBundleStore
from sbam_workers.build.codegen import LoadedTemplate, load_template, render_skeleton
from sbam_workers.build.codegen.bundle import deterministic_tar
from sbam_workers.build.gates.ast_checks import CONFIG_SHA256, GATE_VERSION
from sbam_workers.build.gates.ast_gate import GATES, INPUT_MISMATCH, AstGate
from sbam_workers.build.gates.errors import BUNDLE_TAMPERED
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_codegen_assemble import ctx, seed, stage_input

ALL_GATES = {g.value for g in GATES}


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.0")


def db() -> sessionmaker[Session]:
    sessions = llm_sessions()
    tables = [CodeAttempt.__table__, Artifact.__table__, GateResultRow.__table__]
    CodeAttempt.metadata.create_all(sessions.kw["bind"], tables=tables)  # type: ignore[arg-type]
    return sessions


def given(template: LoadedTemplate, **over: Any) -> AssembleInputs:
    params = ms01_params()
    values: dict[str, Any] = {
        "template": template,
        "params": params,
        "skeleton": render_skeleton(template, params),
        "loop_spec": ms01_loop_spec(),
    }
    return AssembleInputs(**{**values, **over})


def assembled(
    template: LoadedTemplate,
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    inp: BuildStageInput,
    bodies: dict[str, Any] | None = None,
) -> uuid.UUID:
    attempt_id = seed(sessions, template, inp, bodies)

    async def inputs(_: BuildStageInput) -> AssembleInputs:
        return given(template)

    stage = Assemble(inputs, storage, SqlBundleStore(sessions))
    asyncio.run(ActivityEnvironment().run(stage.run, inp))
    return attempt_id


def gate_stage(
    template: LoadedTemplate,
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    **over: Any,
) -> AstGate:
    async def inputs(_: BuildStageInput) -> AssembleInputs:
        return given(template, **over)

    return AstGate(inputs, storage, sessions)


def run(stage: AstGate, inp: BuildStageInput) -> uuid.UUID:
    return asyncio.run(ActivityEnvironment().run(stage.run, inp)).id


def results(sessions: sessionmaker[Session], inp: BuildStageInput) -> list[GateResultRow]:
    with sessions() as session:
        rows = list(GateResultRepository(session, ctx(inp)).list())
        session.expunge_all()
        return sorted(rows, key=lambda r: r.gate)


def test_e12_2_stage_passing_bundle_records_three_passed_verdicts(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)
    assert run(gate_stage(template, sessions, storage), inp) == attempt_id
    rows = results(sessions, inp)
    assert {r.gate for r in rows} == ALL_GATES
    assert {r.status for r in rows} == {GateStatus.PASSED.value}
    # every verdict points at the attempt whose (single, immutable) bundle it judged, and is keyed
    # by the build stage key the LIBRARIES gate uses
    assert {r.code_attempt_id for r in rows} == {attempt_id}
    with sessions() as session:  # and carries the sha256 of the bundle it judged
        attempt = CodeAttemptRepository(session, ctx(inp)).get(attempt_id)
        assert attempt is not None and attempt.bundle_sha256
        assert {r.bundle_sha256 for r in rows} == {attempt.bundle_sha256}
    assert {(r.sub_skill_id, r.build_no, r.cycle, r.attempt) for r in rows} == {
        (inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt)
    }
    assert {(r.tool_version, r.config_sha256) for r in rows} == {(GATE_VERSION, CONFIG_SHA256)}


def test_e12_2_stage_retry_is_idempotent(template: LoadedTemplate, tmp_path: Path) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    assembled(template, sessions, storage, inp)
    stage = gate_stage(template, sessions, storage)
    run(stage, inp)
    first = [(r.id, r.status) for r in results(sessions, inp)]
    run(stage, inp)
    assert [(r.id, r.status) for r in results(sessions, inp)] == first and len(first) == 8


def test_e12_2_stage_failing_bundle_raises_gate_failed_and_keeps_findings(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)
    # the gate is handed a skeleton the stored bundle no longer matches
    skeleton = render_skeleton(template, ms01_params())
    files = dict(skeleton.files)
    files["server.py"] = files["server.py"].replace("mcp.tool(", "mcp.resource(", 1)
    other = replace(skeleton, files=files)
    stage = gate_stage(template, sessions, storage, skeleton=other)
    with pytest.raises(ApplicationError) as caught:
        run(stage, inp)
    assert caught.value.type == GATE_FAILED and caught.value.non_retryable
    assert caught.value.details[0] == [str(attempt_id)]
    by_gate = {r.gate: r for r in results(sessions, inp)}
    assert by_gate[GateName.AST_SKELETON.value].status == GateStatus.FAILED.value
    assert by_gate[GateName.AST_SKELETON.value].findings
    with pytest.raises(ApplicationError) as again:  # the same bundle gives the same answer
        run(stage, inp)
    assert again.value.type == GATE_FAILED and len(results(sessions, inp)) == 8


def test_e12_2_stage_stored_bundle_changed_after_assembly_is_tampering(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    assembled(template, sessions, storage, inp)
    with sessions() as session:
        [row] = CodeAttemptRepository(session, ctx(inp)).list()
        key, hashes = str(row.bundle_key), dict(row.files or {})
    files = {name: b"x = 1\n" for name in hashes}  # a different bundle under the recorded key
    original = storage.get(key, verify=False)
    assert deterministic_tar(files) != original
    path = tmp_path / key
    path.chmod(0o644)  # stored objects are read-only; an attacker with disk access is not stopped
    path.write_bytes(deterministic_tar(files))
    with pytest.raises(ApplicationError) as caught:  # storage's content hash catches it first
        run(gate_stage(template, sessions, storage), inp)
    assert caught.value.type == BUNDLE_TAMPERED and caught.value.non_retryable
    assert results(sessions, inp) == []  # never a verdict on bytes that are not the assembled ones


def test_e12_2_stage_recorded_hashes_changed_is_tampering(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)
    with sessions() as session:
        repo = CodeAttemptRepository(session, ctx(inp))
        row = repo.get(attempt_id)
        assert row is not None
        version = row.row_version
        row.files = {**dict(row.files or {}), "server.py": "0" * 64}
        repo.update(row, version)
        session.commit()
    with pytest.raises(ApplicationError) as caught:
        run(gate_stage(template, sessions, storage), inp)
    assert caught.value.type == BUNDLE_TAMPERED and caught.value.non_retryable
    assert results(sessions, inp) == []


@pytest.mark.parametrize("status", [None, CodeAttemptStatus.BODIES_GENERATED])
def test_e12_2_stage_needs_an_assembled_attempt(
    template: LoadedTemplate, tmp_path: Path, status: CodeAttemptStatus | None
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    if status is not None:
        seed(sessions, template, inp, status=status)
    with pytest.raises(ApplicationError) as caught:
        run(gate_stage(template, sessions, storage), inp)
    assert caught.value.type == INPUT_MISMATCH and caught.value.non_retryable


def test_e12_2_stage_skeleton_must_match_the_attempt(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    assembled(template, sessions, storage, inp)
    stage = gate_stage(template, sessions, storage, manifest_id=uuid.uuid4())
    with pytest.raises(ApplicationError) as caught:
        run(stage, inp)
    assert caught.value.type == INPUT_MISMATCH and caught.value.non_retryable


def test_e12_2_stage_a_verdict_about_another_attempt_is_never_reused(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    """A row at this stage key that points at another attempt is not this bundle's verdict."""
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    assembled(template, sessions, storage, inp)
    with sessions() as session:
        GateResultRepository(session, ctx(inp)).record_stage(
            {
                "sub_skill_id": inp.sub_skill_id,
                "code_attempt_id": uuid.uuid4(),  # some other attempt
                "gate": GateName.AST_SYNTAX.value,
                "status": GateStatus.PASSED.value,
                "findings": [],
                "tool_version": GATE_VERSION,
                "config_sha256": CONFIG_SHA256,
                "build_no": inp.build_no,
                "cycle": inp.cycle,
                "attempt": inp.attempt,
                "created_by": "SYSTEM",
            }
        )
        session.commit()
    with pytest.raises(ApplicationError) as caught:
        run(gate_stage(template, sessions, storage), inp)
    assert caught.value.type == INPUT_MISMATCH and caught.value.non_retryable
    assert len(results(sessions, inp)) == 1  # nothing else was written; the stale row is untouched


def test_e12_2_stage_a_verdict_about_another_bundle_is_never_reused(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    """Same attempt, same stage key, but the row was written for other bytes: it is not this
    bundle's verdict, whatever it says (here: a pass)."""
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)
    with sessions() as session:
        GateResultRepository(session, ctx(inp)).record_stage(
            {
                "sub_skill_id": inp.sub_skill_id,
                "code_attempt_id": attempt_id,
                "gate": GateName.AST_SYNTAX.value,
                "status": GateStatus.PASSED.value,
                "findings": [],
                "tool_version": GATE_VERSION,
                "config_sha256": CONFIG_SHA256,
                "build_no": inp.build_no,
                "cycle": inp.cycle,
                "attempt": inp.attempt,
                "bundle_sha256": "0" * 64,  # not the assembled bundle's hash
                "created_by": "SYSTEM",
            }
        )
        session.commit()
    with pytest.raises(ApplicationError) as caught:
        run(gate_stage(template, sessions, storage), inp)
    assert caught.value.type == INPUT_MISMATCH and caught.value.non_retryable
    assert len(results(sessions, inp)) == 1  # nothing was written next to the stale row


def test_e12_2_stage_the_failure_log_carries_the_ids(
    template: LoadedTemplate, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    assembled(template, sessions, storage, inp)
    skeleton = render_skeleton(template, ms01_params())
    files = dict(skeleton.files)
    files["server.py"] = files["server.py"].replace("mcp.tool(", "mcp.resource(", 1)
    stage = gate_stage(template, sessions, storage, skeleton=replace(skeleton, files=files))
    caplog.set_level(logging.INFO)
    with pytest.raises(ApplicationError):
        run(stage, inp)
    [record] = [r for r in caplog.records if "failed the AST gate" in r.getMessage()]
    assert record.tenant_id == str(inp.tenant_id)  # type: ignore[attr-defined]
    assert record.project_id == str(inp.project_id)  # type: ignore[attr-defined]
    assert record.workflow_id == str(inp.workflow_id)  # type: ignore[attr-defined]
    assert record.sub_skill_id == str(inp.sub_skill_id)  # type: ignore[attr-defined]
    assert record.correlation_id == inp.correlation_id  # type: ignore[attr-defined]
    assert "AST_SKELETON" in record.gates  # type: ignore[attr-defined]


def test_e12_2_stage_verdicts_are_tenant_scoped(template: LoadedTemplate, tmp_path: Path) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    assembled(template, sessions, storage, inp)
    run(gate_stage(template, sessions, storage), inp)
    with sessions() as session:
        other = GateResultRepository(session, TenantContext(uuid.uuid4(), inp.project_id))
        assert other.list() == []
        raw = list(session.scalars(select(GateResultRow)))
        assert {r.tenant_id for r in raw} == {inp.tenant_id}
