"""#922 (WG-35, ADR 0040): imports are a suggestion, not a restriction.

- IMP-T01: `datetime` and the rest of the standard library pass the pre-check and the AST gate.
- IMP-T02: a denied module (`subprocess`, `os`, `socket`, ...) still fails both.
- IMP-T03 (owner update): an undeclared import is not resolved and adds no dependency.
- IMP-T04 (as changed): an unresolvable import does not block certification.
- IMP-T05: a declared dependency with a known vulnerability still fails `DEPENDENCY_VULN`, and a
  declared repository still gets its `REPO_LICENCE` evidence.
- IMP-T06: the corrector does not loop on a module that is merely not suggested.
- IMP-T07: the prompts call the list a suggestion and still carry it; the golden codegen and
  correction sets and the eval gate tests (test_e15_5_*) run unchanged and pass.
- IMP-T08: certification passes without any `IMPORT_RESOLUTION` row, and a frozen manifest that
  holds one still validates with an unchanged hash.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.canonical import deterministic_hash
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.repo_scan import Severity as VulnSeverity
from sbam_workers.build.codegen import LoadedTemplate
from sbam_workers.build.codegen.bodies import (
    FORBIDDEN_MODULES,
    BodyFindingCode,
    denied_import,
    import_allowlist,
)
from sbam_workers.build.gates.compliance import verdicts_of
from sbam_workers.certify.checklist import REQUIRED_GATES, evaluate
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.sandbox.load import OPTIONAL_GATES
from test_codegen_bodies import edge_check, template  # noqa: F401
from test_codegen_correction import (
    attempt_row,
    build,
    correction_input,
    corrector_reply,
    db,
    finding,
    run,
    seed_failed_attempt,
)
from test_codegen_correction import template as correction_template  # noqa: F401
from test_e12_3_ast_rules import codes, in_action, in_helpers, only_failed, rules
from test_e12_3_ast_rules import good as good
from test_e14_1_checklist import evidence, gate
from test_wg13_import_and_compliance import (
    REPO,
    report,
    vuln,
    with_repo,
)

STDLIB = ("datetime", "decimal", "re", "math", "collections", "itertools", "statistics", "zoneinfo")


# --- IMP-T01 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("module", STDLIB)
def test_imp_t01_a_standard_library_import_passes_the_pre_check(
    template: LoadedTemplate, module: str  # noqa: F811
) -> None:
    source = f"import {module}\nreturn {{}}"
    assert edge_check(template, action_body=source) == set()
    assert edge_check(template, helpers=f"import {module}\n") == set()


def test_imp_t01_datetime_is_not_in_the_suggested_list_and_still_passes(
    template: LoadedTemplate,  # noqa: F811
) -> None:
    """The live-run failure: `'datetime' is not an allowed import`."""
    assert "datetime" not in import_allowlist(template.base_imports, ())
    body = "from datetime import date\ntoday = date.fromisoformat('2026-11-20')\nreturn {}"
    assert BodyFindingCode.IMPORT_NOT_ALLOWED not in edge_check(template, action_body=body)
    assert edge_check(template, action_body=body) == set()


def test_imp_t01_the_ast_gate_agrees(
    template: LoadedTemplate, good: dict[str, bytes]  # noqa: F811
) -> None:
    files = in_helpers(good, "import datetime\nimport decimal\nimport re\n")
    assert "IMPORT_NOT_ALLOWED" not in codes(rules(template, files), GateName.AST_IMPORTS)
    assert GateName.AST_IMPORTS not in only_failed(rules(template, files))


def test_imp_t01_a_third_party_import_that_is_not_suggested_passes_too(
    template: LoadedTemplate,  # noqa: F811
) -> None:
    assert edge_check(template, action_body="import dateutil\nreturn {}") == set()


# --- IMP-T02 ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    ["subprocess", "os", "os.path", "sys", "socket", "pickle", "ctypes", "importlib", "shutil",
     "multiprocessing", "urllib.request", "http.client"],
)  # fmt: skip
def test_imp_t02_a_denied_module_still_fails_the_pre_check(
    template: LoadedTemplate, module: str  # noqa: F811
) -> None:
    assert BodyFindingCode.FORBIDDEN_IMPORT in edge_check(
        template, action_body=f"import {module}\nreturn {{}}"
    )
    assert BodyFindingCode.FORBIDDEN_IMPORT in edge_check(
        template, action_body=f"from {module} import x\nreturn {{}}"
    )


@pytest.mark.parametrize("module", ["subprocess", "os", "socket", "pickle", "urllib.request"])
def test_imp_t02_a_denied_module_still_fails_the_ast_gate(
    template: LoadedTemplate, good: dict[str, bytes], module: str  # noqa: F811
) -> None:
    files = in_action(good, f"    import {module}\n")
    found = codes(rules(template, files), GateName.AST_IMPORTS)
    assert "FORBIDDEN_IMPORT" in found
    assert GateName.AST_IMPORTS in only_failed(rules(template, files))


def test_imp_t02_free_neighbours_of_denied_names_stay_free() -> None:
    for module in ("urllib.parse", "http", "http.cookies", "osmium", "systemd", "json", "datetime"):
        assert not denied_import(module, ())
    assert denied_import("subprocess", ())
    assert not denied_import("subprocess", ("subprocess",))  # a declared library may bring it
    assert FORBIDDEN_MODULES >= {"os", "sys", "socket", "pickle", "ctypes", "importlib"}


# --- IMP-T03 and IMP-T04 (changed by the owner update) -----------------------------------------


def test_imp_t03_an_undeclared_import_is_not_resolved_and_adds_no_dependency(
    template: LoadedTemplate, good: dict[str, bytes]  # noqa: F811
) -> None:
    before = import_allowlist(template.base_imports, ())
    files = in_helpers(good, "import httpx\n")
    assert GateName.AST_IMPORTS not in only_failed(rules(template, files))
    # nothing resolves it: the suggested list (and so the declared dependencies) is unchanged
    assert import_allowlist(template.base_imports, ()) == before and "httpx" not in before


def test_imp_t04_an_unresolvable_import_does_not_block_certification(
    template: LoadedTemplate, good: dict[str, bytes]  # noqa: F811
) -> None:
    files = in_helpers(good, "import a_package_that_does_not_exist\n")
    assert GateName.AST_IMPORTS not in only_failed(rules(template, files))
    assert evaluate(evidence()).certified  # and certification asks nothing about imports


# --- IMP-T05 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("severity", [VulnSeverity.HIGH, VulnSeverity.CRITICAL])
def test_imp_t05_a_vulnerable_declared_dependency_fails_whatever_the_model_imported(
    severity: VulnSeverity,
) -> None:
    rows = verdicts_of(with_repo(), {REPO: report(vulnerabilities=[vuln(severity)])})
    status = {v.gate: v.status for v in rows}
    assert status[GateName.DEPENDENCY_VULN] is GateStatus.FAILED
    gates = [
        gate(g.gate, status=GateStatus.FAILED) if g.gate is GateName.DEPENDENCY_VULN else g
        for g in evidence().gates
    ]
    verdict = evaluate(evidence(gates=gates))
    assert not verdict.certified and verdict.reasons == (f"GATE_FAILED:{GateName.DEPENDENCY_VULN}",)


def test_imp_t05_a_declared_repository_still_gets_licence_and_vulnerability_evidence() -> None:
    rows = verdicts_of(with_repo(), {REPO: report()})
    assert {v.gate for v in rows} == {
        GateName.REPO_LICENCE,
        GateName.DEPENDENCY_VULN,
        GateName.MCP_COMPLIANCE,
    }
    assert GateName.IMPORT_RESOLUTION not in {v.gate for v in rows}


# --- IMP-T06 ------------------------------------------------------------------------------------


def test_imp_t06_the_corrector_does_not_loop_on_an_unsuggested_import(
    correction_template: LoadedTemplate,  # noqa: F811
) -> None:
    from codegen_cases import ms01_bodies
    from sbam_api.builds.models import CodeAttemptStatus
    from sbam_contracts.build import GATE_FAILED  # noqa: F401

    sessions = db()
    inp = correction_input(attempt=0, failed_gate="AST_CONFORMANCE")
    seed_failed_attempt(sessions, correction_template, inp, [finding()])
    answer = ms01_bodies(helpers="import datetime\nimport re\n")
    impl = build(correction_template, sessions, [corrector_reply(bodies=answer)])
    run(impl, inp)
    row = attempt_row(sessions, inp, 1)
    assert row.status == CodeAttemptStatus.BODIES_GENERATED  # not PRECHECK_FAILED
    assert [f["code"] for f in row.findings] == []


# --- IMP-T07 ------------------------------------------------------------------------------------


def test_imp_t07_the_prompts_call_the_list_a_suggestion_and_older_versions_are_untouched() -> None:
    registry = PromptRegistry()
    coder = registry.get("codegen-bodies")
    assert coder.version == "1.5.0"  # 1.4.0 (#922) imports, 1.5.0 (#923) tables
    assert "is a suggestion, not a limit" in coder.text and "`datetime`" in coder.text
    assert "there is no `datetime`" not in coder.text
    assert "Import only modules listed" not in coder.text
    corrector = registry.get("correct-code")
    assert corrector.version == "1.5.0" and "is a suggestion, not a limit" in corrector.text
    released = registry.get("codegen-bodies", "1.3.0")  # released: as it was
    assert "there is no `datetime`" in released.text
    assert "Import only modules listed" in registry.get("correct-code", "1.2.0").text


def test_imp_t07_the_suggested_list_is_still_given_to_the_model(
    template: LoadedTemplate,  # noqa: F811
) -> None:
    from sbam_workers.build.codegen.bodies import request_context
    from test_codegen_bodies import build as build_case

    built = build_case(template, "ms01_1")
    context = request_context(
        built.params, built.skeleton, import_allowlist(template.base_imports, built.modules)
    )
    assert json.loads(context["allowed_imports"]) == list(
        import_allowlist(template.base_imports, built.modules)
    )


# --- IMP-T08 ------------------------------------------------------------------------------------


def test_imp_t08_certification_passes_without_any_import_resolution_row() -> None:
    assert GateName.IMPORT_RESOLUTION not in REQUIRED_GATES
    assert GateName.IMPORT_RESOLUTION not in OPTIONAL_GATES
    gates = [g for g in evidence().gates if g.gate is not GateName.IMPORT_RESOLUTION]
    assert all(g.gate is not GateName.IMPORT_RESOLUTION for g in gates)
    verdict = evaluate(evidence(gates=gates))
    assert verdict.certified and verdict.reasons == ()


def test_imp_t08_an_old_import_resolution_row_neither_helps_nor_blocks() -> None:
    old = gate(GateName.IMPORT_RESOLUTION)
    assert evaluate(evidence(gates=[*evidence().gates, old])).certified
    failed = replace(old, status=GateStatus.FAILED)
    assert evaluate(evidence(gates=[*evidence().gates, failed])).certified


def test_imp_t08_the_gate_name_stays_so_old_rows_still_read() -> None:
    assert GateName("IMPORT_RESOLUTION") is GateName.IMPORT_RESOLUTION


def test_imp_t08_a_frozen_manifest_that_holds_an_import_row_still_validates_with_its_hash() -> None:
    path: Path = fixture_path("isa14", "ms01_1_manifest.json")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    pinned = deterministic_hash(data)
    old_row = {
        "gate": "IMPORT_RESOLUTION",
        "status": "PASSED",
        "findings": [],
        "tool_version": "import-gate/1.0.0",
    }
    data["sbam_build_record"]["evidence"]["gates"] = [old_row]
    model = SkillManifest.model_validate(data)
    assert deterministic_hash(data) == pinned  # the evidence is outside the hashed body
    assert model.computed_hash() == pinned
    assert model.sbam_build_record is not None
    recorded = model.sbam_build_record.evidence
    assert recorded is not None and recorded.gates == [old_row]
