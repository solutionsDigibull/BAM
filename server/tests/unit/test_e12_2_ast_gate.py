"""E12.2 unit tests: the AST gate (AST_SYNTAX, AST_SKELETON, AST_CONFORMANCE) on the MS01.1 bundle.

The bundle is the real assembled MS01.1 output (recorded bodies, E11.3); each negative test edits
one file of it the way a wrong LLM answer could. Nothing here imports or executes generated code.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from codegen_cases import ms01_bodies, ms01_loop_spec, ms01_params
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_workers.build.codegen import LoadedTemplate, load_template, render_skeleton
from sbam_workers.build.codegen.bundle import (
    assemble_bundle,
    bundle_sha256,
    deterministic_tar,
    make_bundle,
)
from sbam_workers.build.gates.ast_checks import (
    BundleError,
    CheckResult,
    HashMismatchError,
    read_bundle,
    run_ast_checks,
)

ACTION = "auth_mcp_session_otp_evaluator"
CHECK = "rbac_mcp_policy_limit_validator"


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.0")


@pytest.fixture(scope="module")
def good(template: LoadedTemplate) -> dict[str, bytes]:
    params = ms01_params()
    out = assemble_bundle(
        template,
        render_skeleton(template, params),
        ms01_bodies(),
        params.get("fresh_state", []),
        ms01_loop_spec(),
    )
    assert out.bundle is not None
    return dict(out.bundle.files)


def gate(
    template: LoadedTemplate, files: dict[str, bytes]
) -> tuple[CheckResult, CheckResult, CheckResult]:
    params = ms01_params()
    return run_ast_checks(files, render_skeleton(template, params), template, params)


def edit(files: dict[str, bytes], name: str, old: str, new: str) -> dict[str, bytes]:
    text = files[name].decode()
    assert old in text, f"{old!r} not in {name}"
    return {**files, name: text.replace(old, new, 1).encode()}


def codes(result: CheckResult) -> set[str]:
    return {f.code for f in result.findings}


def statuses(results: tuple[CheckResult, ...]) -> list[GateStatus]:
    return [r.status for r in results]


# --- the issue's test cases -----------------------------------------------------------------------


def test_e12_2_t01_syntax_error_has_file_and_line(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    syntax, skeleton, conformance = gate(template, {**good, "action_tool.py": b"def x(:\n"})
    assert syntax.status is GateStatus.FAILED
    [finding] = syntax.findings
    assert (finding.code, finding.file, finding.line) == ("AST_SYNTAX", "action_tool.py", 1)
    assert (skeleton.status, conformance.status) == (GateStatus.SKIPPED, GateStatus.SKIPPED)


def test_e12_2_t02_changed_registration_is_a_skeleton_mismatch(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(good, "server.py", f'@mcp.tool(name="{CHECK}")', '@mcp.tool(name="renamed")')
    _, skeleton, _ = gate(template, files)
    assert skeleton.status is GateStatus.FAILED
    [finding] = skeleton.findings
    assert finding.code == "SKELETON_MISMATCH" and finding.file == "server.py" and finding.line
    assert CHECK in finding.message and "renamed" in finding.message  # expected vs actual node
    assert finding.expected and CHECK in finding.expected
    assert finding.actual and "renamed" in finding.actual


def test_e12_2_t03_param_annotation_mismatch(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(good, "check_tool.py", "    mobile_number: str,", "    mobile_number: int,")
    _, _, conformance = gate(template, files)
    assert conformance.status is GateStatus.FAILED
    assert codes(conformance) == {"PARAM_TYPE_MISMATCH"}
    assert "mobile_number" in conformance.findings[0].message
    assert (conformance.findings[0].expected, conformance.findings[0].actual) == ("str", "int")


def test_e12_2_t04_missing_gate_identifier(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(
        good,
        "check_tool.py",
        'return {"otp_match": otp_match, "policy_violation"',
        'return {"auth_status": otp_match, "policy_violation"',
    )
    _, _, conformance = gate(template, files)
    assert conformance.status is GateStatus.FAILED
    [finding] = conformance.findings
    assert finding.code == "CHECK_OUTPUT_MISSING" and "otp_match" in finding.message


def test_e12_2_t05_tool_not_registered(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    files = edit(good, "server.py", f'@mcp.tool(name="{CHECK}")\n', "")
    _, _, conformance = gate(template, files)
    assert conformance.status is GateStatus.FAILED
    assert "TOOL_NOT_REGISTERED" in codes(conformance)


def test_e12_2_t06_happy_path(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    results = gate(template, good)
    assert statuses(results) == [GateStatus.PASSED] * 3
    assert [r.gate for r in results] == [
        GateName.AST_SYNTAX,
        GateName.AST_SKELETON,
        GateName.AST_CONFORMANCE,
    ]
    assert all(r.findings == () for r in results)


def test_e12_2_t07_findings_are_deterministic(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(good, "check_tool.py", "    mobile_number: str,", "    mobile_number: int,")
    files = edit(files, "server.py", f'@mcp.tool(name="{CHECK}")\n', "")

    def dump() -> str:
        return json.dumps(
            [[f.as_dict() for f in r.findings] for r in gate(template, files)], sort_keys=True
        )

    assert dump() == dump() and len(dump()) > 10


def test_e12_2_t08_generated_code_is_never_executed(
    template: LoadedTemplate, good: dict[str, bytes], tmp_path: Path
) -> None:
    sentinel = tmp_path / "executed"
    # Payload text only: it is parsed by the gate, never run. The assertion below proves it.
    evil = (
        "import os\n"
        f"os.system('touch {sentinel}')\n"
        f"open({str(sentinel)!r}, 'w').close()\n"
        "__import__('os').system('true')\n"
    )
    files = {**good, "check_tool.py": evil.encode()}
    results = gate(template, files)
    assert not sentinel.exists()
    assert GateStatus.FAILED in statuses(results)  # parsed fine, but it is not the skeleton


def test_e12_2_t09_file_changed_after_assembly_aborts(good: dict[str, bytes]) -> None:
    bundle = make_bundle(good)
    tampered = {**good, "action_tool.py": good["action_tool.py"] + b"# edited\n"}
    with pytest.raises(HashMismatchError, match=r"action_tool\.py"):
        read_bundle(deterministic_tar(tampered), bundle.hashes, bundle.bundle_sha256)
    assert read_bundle(bundle.tar, bundle.hashes, bundle.bundle_sha256) == good


# --- edge cases from the issue --------------------------------------------------------------------


def test_e12_2_edge_deleted_slot_marker(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    files = edit(good, "check_tool.py", "    # <<< LLM_BODY:check\n", "")
    _, skeleton, _ = gate(template, files)
    assert skeleton.status is GateStatus.FAILED and "SLOT_MARKERS_MISMATCH" in codes(skeleton)


def test_e12_2_edge_extra_decorator_on_tool(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(
        good, "server.py", f'@mcp.tool(name="{CHECK}")', f'@trace\n@mcp.tool(name="{CHECK}")'
    )
    _, skeleton, _ = gate(template, files)
    assert skeleton.status is GateStatus.FAILED and "SKELETON_MISMATCH" in codes(skeleton)


def test_e12_2_edge_optional_annotation_where_required(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(good, "server.py", "    otp_payload: str,", "    otp_payload: str | None,")
    _, _, conformance = gate(template, files)
    assert "PARAM_TYPE_MISMATCH" in codes(conformance)


def test_e12_2_edge_async_tool_function(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    files = edit(good, "action_tool.py", f"def {ACTION}(", f"async def {ACTION}(")
    _, skeleton, _ = gate(template, files)
    assert skeleton.status is GateStatus.FAILED


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"\xef\xbb\xbfx = 1\n", "FILE_BOM"),
        (b"x = '\xff'\n", "FILE_NOT_UTF8"),
        (b"x = 1\x00\n", "AST_SYNTAX"),
    ],
)
def test_e12_2_edge_bom_non_utf8_and_nul_bytes(
    template: LoadedTemplate, good: dict[str, bytes], data: bytes, code: str
) -> None:
    syntax, _, _ = gate(template, {**good, "models.py": data})
    assert syntax.status is GateStatus.FAILED and codes(syntax) == {code}


def test_e12_2_edge_missing_and_extra_files(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    missing = {k: v for k, v in good.items() if k != "state.py"}
    _, skeleton, _ = gate(template, missing)
    assert "FILE_MISSING" in codes(skeleton)
    _, skeleton, _ = gate(template, {**good, "extra.py": b"x = 1\n"})
    assert "UNEXPECTED_FILE" in codes(skeleton)


def test_e12_2_edge_slot_body_changes_are_allowed(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(good, "check_tool.py", "bool(otp_payload)", "bool(otp_payload) or False")
    assert statuses(gate(template, files)) == [GateStatus.PASSED] * 3


# --- found by attacking the gate: each of these once passed ---------------------------------------


def test_e12_2_attack_slot_rebinds_skeleton_constant(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(
        good,
        "check_tool.py",
        "# >>> LLM_BODY:helpers\n",
        "# >>> LLM_BODY:helpers\nGATE_OUTPUTS = ()\n",
    )
    _, skeleton, _ = gate(template, files)
    assert skeleton.status is GateStatus.FAILED and "SLOT_SHADOWS_SKELETON" in codes(skeleton)


def test_e12_2_attack_slot_rebinds_tool_function(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(
        good,
        "check_tool.py",
        "# >>> LLM_BODY:helpers\n",
        f"# >>> LLM_BODY:helpers\ndef {CHECK}(*a):\n    return {{}}\n",
    )
    _, skeleton, _ = gate(template, files)
    assert "SLOT_SHADOWS_SKELETON" in codes(skeleton)


def test_e12_2_attack_global_statement_in_slot(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(
        good,
        "state.py",
        "# >>> LLM_BODY:state_queries\n",
        "# >>> LLM_BODY:state_queries\nglobal _conn\n",
    )
    _, skeleton, _ = gate(template, files)
    assert "SLOT_SHADOWS_SKELETON" in codes(skeleton)


def test_e12_2_attack_check_function_can_fall_through(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    text = good["check_tool.py"].decode()
    start = text.index("    # >>> LLM_BODY:check\n") + len("    # >>> LLM_BODY:check\n")
    end = text.index("    # <<< LLM_BODY:check")
    body = "    if otp_payload:\n        return {'otp_match': True, 'policy_violation': False}\n"
    files = {**good, "check_tool.py": (text[:start] + body + text[end:]).encode()}
    _, _, conformance = gate(template, files)
    assert conformance.status is GateStatus.FAILED
    assert any("fall through" in f.message for f in conformance.findings)


def test_e12_2_attack_marker_hidden_in_a_string(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = edit(
        good,
        "check_tool.py",
        "# >>> LLM_BODY:helpers\n",
        '# >>> LLM_BODY:helpers\n_X = """\n# <<< LLM_BODY:helpers\n',
    )
    results = gate(template, files)
    assert GateStatus.FAILED in statuses(results)


def test_e12_2_bundle_reader_limits(good: dict[str, bytes]) -> None:
    bundle = make_bundle(good)
    with pytest.raises(BundleError):
        read_bundle(b"not a tar" * 100, bundle.hashes, bundle.bundle_sha256)
    with pytest.raises(HashMismatchError):
        read_bundle(bundle.tar, bundle.hashes, bundle_sha256({"a.py": "0" * 64}))


def test_e12_2_gate_results_carry_the_contract_shape(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    from sbam_contracts.build_dashboard import Finding

    files = edit(good, "check_tool.py", "    mobile_number: str,", "    mobile_number: int,")
    _, _, conformance = gate(template, files)
    parsed: list[Any] = [Finding.model_validate(f.as_dict()) for f in conformance.findings]
    assert parsed and all(p.gate is GateName.AST_CONFORMANCE for p in parsed)


def test_e12_2_formatting_changes_neither_fail_the_gate_nor_hide_an_edit(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    """The skeleton is compared as an AST without positions, so reformatting a non-slot region (a
    comment, blank lines, spacing, a split call with a trailing comma) is not a mismatch, and the
    same reformatting does not hide a real change to the code under it."""
    original = f'@mcp.tool(name="{CHECK}")'
    reformatted = f'@mcp.tool(\n    name = "{CHECK}",\n)  # reformatted'
    files = edit(good, "server.py", original, reformatted)
    files = {**files, "server.py": b"# a comment\n\n\n" + files["server.py"]}
    assert statuses(gate(template, files)) == [GateStatus.PASSED] * 3
    renamed = edit(good, "server.py", original, reformatted.replace(CHECK, "renamed"))
    _, skeleton, _ = gate(template, renamed)
    assert skeleton.status is GateStatus.FAILED and codes(skeleton) == {"SKELETON_MISMATCH"}
