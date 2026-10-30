"""E12.4: the static tools (Ruff, Black, MyPy --strict, Bandit) on source text, and the
`static_gate` stage with ClamAV against a real (SQLite) database and a disk storage backend."""

import asyncio
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from clamd_fake import FakeClamd, version_string
from sbam_api.audit.models import AuditLog
from sbam_api.scan.clamd import ClamdClient
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import Finding, GateName, GateStatus
from sbam_workers.build.gates.errors import STATIC_GATE_INFRA
from sbam_workers.build.gates.static_gate import GATES, StaticGate
from sbam_workers.build.gates.static_tools import (
    TOOLS,
    ToolFailure,
    ToolResult,
    run_bandit,
    run_black,
    run_mypy,
    run_ruff,
)
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_codegen_assemble import stage_input
from test_e12_2_ast_gate_stage import assembled, db, results, template  # noqa: F401

CLEAN = (
    "import json\n\n\n"
    "def action(value: str) -> str:\n"
    '    return json.dumps({"value": value})\n'
)


def files(source: str) -> dict[str, bytes]:
    return {"action.py": source.encode()}


def codes(result: ToolResult) -> set[str]:
    for f in result.findings:
        Finding.model_validate(f)  # what the gates endpoint will do with the stored row
    return {f["code"] for f in result.findings}


def test_e12_4_clean_code_passes_every_tool() -> None:
    for tool in TOOLS:
        result = tool(files(CLEAN))
        assert (result.status, result.findings) == (GateStatus.PASSED, []), result


def test_e12_4_t01_ruff_error_unused_variable() -> None:
    source = "def action() -> None:\n    unused = 1\n"
    result = run_ruff(files(source))
    assert result.status is GateStatus.FAILED and "F841" in codes(result)
    (hit,) = [f for f in result.findings if f["code"] == "F841"]
    assert (hit["file"], hit["line"]) == ("action.py", 2)


def test_e12_4_t02_black_diff_on_modified_spacing() -> None:
    result = run_black(files("x  =  1\n"))
    assert result.status is GateStatus.FAILED and codes(result) == {"BLACK_DIFF"}
    assert result.findings[0]["file"] == "action.py"


def test_e12_4_t03_mypy_strict_missing_return_annotation() -> None:
    result = run_mypy(files("def action(value):\n    return value\n"))
    assert result.status is GateStatus.FAILED and "no-untyped-def" in codes(result)


def test_e12_4_t04_bandit_high_blocks() -> None:
    source = (
        "import hashlib\nimport subprocess\n\n\n"
        "def action(data: bytes, cmd: str) -> None:\n"
        "    hashlib.md5(data)\n"
        "    subprocess.run(cmd, shell=True)\n"
    )
    result = run_bandit(files(source))
    assert result.status is GateStatus.FAILED
    assert {"B324", "B602"} <= codes(result)
    assert {f["severity"] for f in result.findings} >= {"HIGH"}


def test_e12_4_t05_bandit_medium_only_passes_with_the_finding_reported() -> None:
    source = (
        "import pickle\n\n\n"
        "def action(blob: bytes) -> object:\n"
        "    return pickle.loads(blob)\n"  # B301 is MEDIUM
    )
    result = run_bandit(files(source))
    assert result.status is GateStatus.PASSED
    assert result.findings and {f["severity"] for f in result.findings} <= {"MEDIUM", "LOW"}


def test_e12_4_t08_two_runs_give_the_same_findings_versions_and_config_hashes() -> None:
    source = "import os\n\n\ndef action(value):\n    unused = 1\n    return value\n"
    for tool in TOOLS:
        first, second = tool(files(source)), tool(files(source))
        assert first == second


def test_e12_4_a_tool_that_cannot_run_is_a_failure_to_give_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    def boom(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(ToolFailure):
        run_ruff(files(CLEAN))


# --- the stage ------------------------------------------------------------------------------------


def passing(gate: GateName):  # type: ignore[no-untyped-def]
    def tool(_: Any) -> ToolResult:
        return ToolResult(gate, GateStatus.PASSED, [], "1", "cfg")

    return tool


FAKE_TOOLS = tuple(passing(g) for g in GATES[:4])


def stage_with(
    storage: LocalDiskBackend, sessions: Any, clamd: FakeClamd | None, **over: Any
) -> StaticGate:
    host, port = (clamd.host, clamd.port) if clamd else ("127.0.0.1", 1)
    client = ClamdClient(host, port, 2.0)
    return StaticGate(storage, sessions, client, **{"tools": FAKE_TOOLS, **over})


def run_stage(stage: StaticGate, inp: BuildStageInput) -> uuid.UUID:
    return asyncio.run(ActivityEnvironment().run(stage.run, inp)).id


def test_e12_4_stage_records_five_verdicts_on_the_bundle_and_is_idempotent(
    template: Any, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)
    with FakeClamd() as clamd:
        stage = stage_with(storage, sessions, clamd)
        assert run_stage(stage, inp) == attempt_id
        first = [(r.id, r.status) for r in results(sessions, inp)]
        run_stage(stage, inp)
    rows = results(sessions, inp)
    assert [(r.id, r.status) for r in rows] == first
    assert {r.gate for r in rows} == {g.value for g in GATES}
    assert {r.code_attempt_id for r in rows} == {attempt_id}


def test_e12_4_t06_eicar_fails_clamav_quarantines_the_bundle_and_alerts(
    template: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture  # noqa: F811
) -> None:
    # a short root: the quarantine key is long and Windows stops paths at 260 characters
    root = Path(tempfile.mkdtemp(prefix="sbam-"))
    sessions, storage, inp = db(), LocalDiskBackend(root), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)
    with FakeClamd(behaviour="infected") as clamd:
        stage = stage_with(storage, sessions, clamd)
        with pytest.raises(ApplicationError) as caught:
            run_stage(stage, inp)
    assert caught.value.type == GATE_FAILED and caught.value.non_retryable
    assert caught.value.details[0] == [str(attempt_id)]
    verdicts = {r.gate: r for r in results(sessions, inp)}
    assert verdicts["CLAMAV"].status == GateStatus.FAILED.value
    assert verdicts["CLAMAV"].findings[0]["severity"] == "HIGH"
    with sessions() as session:
        (audit,) = [
            a for a in session.scalars(select(AuditLog)) if a.action == "BUNDLE_QUARANTINED"
        ]
    assert audit.entity_id == str(attempt_id)
    assert audit.after is not None and audit.after["quarantine_key"]
    assert any(getattr(r, "alert", None) == "operator" for r in caplog.records)


def test_e12_4_t07_clamd_down_is_retryable_infra_and_records_no_verdict(
    template: Any, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    assembled(template, sessions, storage, inp)
    with pytest.raises(ApplicationError) as caught:
        run_stage(stage_with(storage, sessions, None), inp)
    assert caught.value.type == STATIC_GATE_INFRA and not caught.value.non_retryable
    assert results(sessions, inp) == []


def test_e12_4_stale_signatures_are_infra_not_a_pass(
    template: Any, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    assembled(template, sessions, storage, inp)
    with FakeClamd(version=version_string(age=timedelta(hours=30))) as clamd:
        with pytest.raises(ApplicationError) as caught:
            run_stage(stage_with(storage, sessions, clamd), inp)
    assert caught.value.type == STATIC_GATE_INFRA and not caught.value.non_retryable
    assert results(sessions, inp) == []


def test_e12_4_a_blocking_tool_finding_is_gate_failed_with_the_findings_stored(
    template: Any, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)

    def failing_ruff(_: Any) -> ToolResult:
        found = [{"code": "F841", "file": "x.py", "line": 2, "severity": "MEDIUM", "message": "m"}]
        return ToolResult(GateName.RUFF, GateStatus.FAILED, found, "1", "cfg")

    tools = (failing_ruff, *FAKE_TOOLS[1:])
    with FakeClamd() as clamd, pytest.raises(ApplicationError) as caught:
        run_stage(stage_with(storage, sessions, clamd, tools=tools), inp)
    assert caught.value.type == GATE_FAILED and caught.value.details[0] == [str(attempt_id)]
    ruff = next(r for r in results(sessions, inp) if r.gate == "RUFF")
    assert ruff.status == "FAILED" and ruff.findings[0]["code"] == "F841"


def test_e12_4_the_real_tools_pass_the_generated_skeleton(
    template: Any, tmp_path: Path  # noqa: F811
) -> None:
    """The bundle the template gives for MS01.1 must satisfy the pinned configs, or every build
    would fail this gate before the model wrote a line."""
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = assembled(template, sessions, storage, inp)
    with FakeClamd() as clamd:
        stage = stage_with(storage, sessions, clamd, tools=TOOLS)
        assert run_stage(stage, inp) == attempt_id
