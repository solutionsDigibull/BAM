"""Spike E0.11 (#29): automated versions of test cases E0.11-T01..T08 (mock LLM, no Docker, no key).

Run:  make -C spikes/codegen test
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import BODIES, MS01, SK_BOM
from jinja2 import meta
from pydantic_ai.exceptions import UnexpectedModelBehavior
from sbam_contracts.manifest import SkillManifest

from codegen import assemble as asm
from codegen import ast_gates, bodies, render
from codegen.cli import main
from codegen.context import ManifestError, build_context, load_manifest

INJECTION = '{{ 7*7 }} """ import os'


def ctx_for(path: Path = MS01) -> dict[str, Any]:
    return build_context(load_manifest(path), asm.load_spec()["version"])


def generate(tmp: Path, fixture: str, manifest: Path = MS01, static: bool = False) -> int:
    args = ["generate", "--manifest", str(manifest), "--bodies", str(BODIES / fixture)]
    args += ["--out", str(tmp)] + ([] if static else ["--no-static"])
    return main(args)


def gates(tmp: Path) -> dict[str, dict[str, Any]]:
    report = json.loads((tmp / "gates.json").read_text(encoding="utf-8"))
    return {g["check"]: g for g in report["gates"]}


def only_failed(tmp: Path) -> list[str]:
    return [name for name, g in gates(tmp).items() if not g["passed"]]


# --- T01 deterministic render ----------------------------------------------------------------


def test_e0_11_t01_render_twice_byte_identical(tmp_path: Path) -> None:
    ctx = ctx_for()
    assert asm.render_skeleton(ctx) == asm.render_skeleton(ctx)
    for run in ("a", "b"):
        assert main(["render", "--manifest", str(MS01), "--out", str(tmp_path / run)]) == 0
    a, b = ((tmp_path / r / "bundle.json").read_text() for r in ("a", "b"))
    assert a == b
    assert len(json.loads(a)["files"]) == 7  # 6 generated files + __init__.py


def test_e0_11_t01_bundle_hash_matches_files(tmp_path: Path) -> None:
    main(["render", "--manifest", str(MS01), "--out", str(tmp_path)])
    bundle = json.loads((tmp_path / "bundle.json").read_text())
    for name, digest in bundle["files"].items():
        data = (tmp_path / bundle["package"] / name).read_bytes()
        assert b"\r\n" not in data
        assert hashlib.sha256(data).hexdigest() == digest


# --- T02 end-to-end generation ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("manifest", "fixture"), [(MS01, "ms01_1_good.yaml"), (SK_BOM, "sk_bom_good.yaml")]
)
def test_e0_11_t02_generate_all_gates_pass(tmp_path: Path, manifest: Path, fixture: str) -> None:
    assert generate(tmp_path, fixture, manifest, static=True) == 0
    assert only_failed(tmp_path) == []
    assert set(gates(tmp_path)) == {
        "AST_SKELETON",
        "AST_CONFORMANCE",
        "AST_DECOUPLING",
        "AST_NO_MANUAL_LOOP",
        "AST_HANDOFF",
        "RUFF",
        "BLACK",
        "MYPY",
    }
    pkg = tmp_path / json.loads((tmp_path / "bundle.json").read_text())["package"]
    for name in asm.load_spec()["files"]:
        ast.parse((pkg / name["output"]).read_text(encoding="utf-8"))


def test_e0_11_t02_generated_skill_runs(tmp_path: Path) -> None:
    """The MS01.1 bundle imports, both tools work over FastMCP, and the gates evaluate."""
    from fastmcp import Client
    from sbam_contracts.gate_expr import evaluate, parse

    assert generate(tmp_path, "ms01_1_good.yaml") == 0
    sys.path.insert(0, str(tmp_path))
    try:
        server = importlib.import_module("ms01_1_investor_auth_manager.server")
        state = importlib.import_module("ms01_1_investor_auth_manager.state")
        handoff = importlib.import_module("ms01_1_investor_auth_manager.state_handoff")
    finally:
        sys.path.remove(str(tmp_path))
    db = sqlite3.connect(":memory:", check_same_thread=False)  # FastMCP runs sync tools in a thread
    db.execute(
        "CREATE TABLE investor_profiles (mobile_number TEXT, failed_login_attempts INT,"
        " active_devices INT, user_status TEXT, otp_sha256 TEXT)"
    )
    db.execute("CREATE TABLE auth_event_audit_handoff (skill_id TEXT, payload TEXT)")
    otp = hashlib.sha256(b"123456").hexdigest()
    db.execute("INSERT INTO investor_profiles VALUES ('9000000001', 0, 2, 'ACTIVE', ?)", (otp,))
    state.configure(db)
    handoff.configure(db)
    v = load_manifest(MS01).dump()["sbam_build_record"]["isatvon_detail"]["v_variables"]

    async def call(otp_payload: str) -> tuple[dict[str, Any], dict[str, Any]]:
        args = {"mobile_number": "9000000001", "otp_payload": otp_payload}
        args["device_fingerprint"] = "fp-abc"
        async with Client(server.mcp) as client:
            act = await client.call_tool("auth_mcp_session_otp_evaluator", args)
            chk = await client.call_tool("rbac_mcp_policy_limit_validator", args)
        return act.structured_content or {}, chk.structured_content or {}

    action, check = asyncio.run(call("123456"))
    assert action["otp_match"] is True
    assert check == {"otp_match": True, "policy_violation": False}
    assert evaluate(parse(v["success_gate"]), {}, state.read("9000000001"), check) is True
    _, check = asyncio.run(call("000000"))
    assert evaluate(parse(v["success_gate"]), {}, {}, check) is False
    rows = db.execute("SELECT payload FROM auth_event_audit_handoff").fetchall()
    assert [json.loads(r[0])["otp_match"] for r in rows] == [True, False]


# --- T03 template injection ------------------------------------------------------------------


def test_e0_11_t03_manifest_text_never_evaluated(tmp_path: Path) -> None:
    args = ["render", "--manifest", str(MS01), "--out", str(tmp_path), "--action-step", INJECTION]
    assert main(args) == 0
    code = (tmp_path / "ms01_1_investor_auth_manager" / "action_tool.py").read_text("utf-8")
    assert "49" not in code
    assert "{{ 7*7 }}" in code
    fn = next(n for n in ast.parse(code).body if isinstance(n, ast.FunctionDef))
    assert (ast.get_docstring(fn) or "").startswith(INJECTION)


def test_e0_11_t03_renderer_self_check() -> None:
    assert render.main() == 0


def test_e0_11_t03_sandbox_and_strict_undefined() -> None:
    from jinja2.exceptions import SecurityError, UndefinedError

    with pytest.raises(UndefinedError):
        render.render_string("{{ missing }}", {})
    with pytest.raises(SecurityError):
        render.render_string("{{ ''.__class__.__mro__[1].__subclasses__() }}", {})


# --- T04-T07 seeded violations ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "gate", "file", "text"),
    [
        ("t04_decoupling.yaml", "AST_DECOUPLING", "check_tool.py", "action_tool"),
        ("t05_while_loop.yaml", "AST_NO_MANUAL_LOOP", "action_tool.py", "while loop"),
        ("t06_tamper_signature.yaml", "AST_CONFORMANCE", "action_tool.py", "redefines tool"),
        ("t07_no_handoff.yaml", "AST_HANDOFF", "action_tool.py", "iteration_pass, device_fing"),
    ],
    ids=["t04", "t05", "t06", "t07"],
)
def test_e0_11_t04_t07_seeded_violation_detected(
    tmp_path: Path, fixture: str, gate: str, file: str, text: str
) -> None:
    assert generate(tmp_path, fixture) == 1
    assert only_failed(tmp_path) == [gate]
    finding = gates(tmp_path)[gate]["findings"][0]
    assert finding["file"] == file
    assert finding["line"] > 0
    assert text in finding["message"]


def test_e0_11_t06_skeleton_tamper_outside_slot() -> None:
    """Someone edits the protected signature itself (not a slot): both gates fail."""
    ctx = ctx_for()
    skeleton = asm.render_skeleton(ctx)
    good = bodies.CodeBodies(**bodies.load_responses(BODIES / "ms01_1_good.yaml")[0])
    files = asm.assemble(ctx, good)
    files["action_tool.py"] = files["action_tool.py"].replace("mobile_number: str", "phone: str")
    results = {r["check"]: r for r in ast_gates.run_all(files, skeleton, ctx, asm.load_spec())}
    assert not results["AST_SKELETON"]["passed"]
    assert results["AST_SKELETON"]["findings"][0]["file"] == "action_tool.py"
    assert not results["AST_CONFORMANCE"]["passed"]


# --- T08 invalid LLM output ------------------------------------------------------------------


def test_e0_11_t08_invalid_output_reasked_then_nothing_assembled(tmp_path: Path) -> None:
    ctx = ctx_for()
    mock = bodies.MockModel(bodies.load_responses(BODIES / "t08_missing_check_body.yaml"))
    with pytest.raises(UnexpectedModelBehavior):
        bodies.generate(mock.model(), ctx, asm.render_skeleton(ctx))
    assert mock.calls == 1 + bodies.RETRIES
    out = tmp_path / "out"
    assert generate(out, "t08_missing_check_body.yaml") == 2
    assert not out.exists()


def test_e0_11_t08_retry_then_valid_output(tmp_path: Path) -> None:
    assert generate(tmp_path, "t08_retry_then_good.yaml") == 0
    report = json.loads((tmp_path / "gates.json").read_text())
    assert report["attempts"] == 2
    assert only_failed(tmp_path) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"check_body": "return {\n"},
        {"check_body": "# >>> LLM_BODY:check\nreturn {}"},
        {"state_queries": {"x": "DELETE FROM investor_profiles WHERE mobile_number = ?"}},
        {"helpers": "import os\n"},
        {"extra_field": "x"},
    ],
    ids=["syntax", "marker", "write-sql", "helper-import", "extra-key"],
)
def test_e0_11_t08_schema_rejects(bad: dict[str, Any]) -> None:
    good = bodies.load_responses(BODIES / "ms01_1_good.yaml")[0]
    with pytest.raises(ValueError, match="validation error"):
        bodies.CodeBodies(**{**good, **bad})


# --- edge cases ------------------------------------------------------------------------------


def test_e0_11_edge_same_action_and_check_tool_rejected() -> None:
    data = json.loads(MS01.read_text(encoding="utf-8"))
    detail = data["sbam_build_record"]["isatvon_detail"]["t_tech_stack"]
    detail["fixed_check_tool"] = detail["action_tool"]
    manifest = SkillManifest.model_validate(data)
    with pytest.raises(ManifestError, match="must differ"):
        build_context(manifest, "0.1.0")


def test_e0_11_edge_second_skill_needs_no_template_change() -> None:
    ms01, bom = ctx_for(MS01), ctx_for(SK_BOM)
    assert [i["type"] for i in bom["inputs"]] == ["string", "integer", "string", "boolean"]
    assert bom["gate_outputs"] == ["duplicate_parts", "unresolved_lines"]
    assert ms01["gate_outputs"] == ["otp_match", "policy_violation"]


def test_e0_11_template_yaml_matches_templates() -> None:
    tdir = asm.TEMPLATE_DIR
    spec = yaml.safe_load((tdir / "template.yaml").read_text(encoding="utf-8"))
    env = render.make_env(tdir)
    for f in spec["files"]:
        src = (tdir / f["template"]).read_text(encoding="utf-8")
        assert meta.find_undeclared_variables(env.parse(src)) == set(f["context"]), f["output"]
        assert [s for s in f["slots"]] == asm.SLOT_OPEN.findall(src), f["output"]
    assert {p.name for p in tdir.glob("*.j2")} == {f["template"] for f in spec["files"]}
