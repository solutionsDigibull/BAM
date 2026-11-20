"""#761 (WG-13): the import gate on the sandbox adapter, and the licence, dependency vulnerability
and MCP compliance rows certification needs.

- WG13-T01: the import gate's verdict is a `gate_results` row on the bundle hash, run through the
  sandbox adapter (a VM of the pinned runtime, no egress, destroyed afterwards).
- WG13-T02: an import side effect fails the gate.
- WG13-T03: certification refuses without the licence result (and without the vulnerability one).
- WG13-T04: an MCP compliance failure blocks certification.
"""

import asyncio
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from sbam_api.manifests.models import GateResultRow
from sbam_api.storage.base import StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus, Severity
from sbam_contracts.repo_scan import (
    DependencyLicence,
    FetchDecision,
    LicenceFinding,
    LicenceOverride,
    LicenceVerdict,
    RefusalReason,
    RepoScanReport,
    VulnFinding,
)
from sbam_contracts.repo_scan import Severity as VulnSeverity
from sbam_contracts.sandbox import InfraReason, SandboxMode, template_alias
from sbam_workers.build.codegen import LoadedTemplate
from sbam_workers.build.gates.compliance import (
    NO_REPOSITORY,
    ComplianceGates,
    RepoRef,
    licence_findings,
    mcp_findings,
    source_repos,
    status_of,
    storage_report_finder,
    verdicts_of,
    vulnerability_findings,
)
from sbam_workers.build.gates.errors import GATE_INPUT_MISMATCH, IMPORT_GATE_INFRA
from sbam_workers.build.gates.import_gate import ImportGate
from sbam_workers.build.gates.sandbox_import_runner import (
    LIMITS,
    PYTHON,
    SandboxImportRunner,
    probe_argv,
    vm_files,
)
from sbam_workers.build.gates.static_gate import GATES as STATIC_GATES
from sbam_workers.build.gates.stored_bundle import Verdict, find_assembled, record_verdicts
from sbam_workers.certify.checklist import REQUIRED_GATES, evaluate
from sbam_workers.repo_fetch.report import store_bundle, store_report
from sbam_workers.sandbox.backend import (
    ExecResult,
    SandboxInfraError,
    VmHandle,
    VmInfo,
    VmSpec,
)
from sbam_workers.sandbox.executor import BUNDLE_DIR
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_codegen_assemble import stage_input
from test_e12_2_ast_gate_stage import assembled, db, gate_stage, results, template  # noqa: F401
from test_e14_1_checklist import evidence, gate

DIGEST = "sha256:" + "ab" * 32
IMAGE = f"skill-runtime@{DIGEST}"
COMMIT = "c" * 40
URL = "https://github.com/acme/costing"
OK_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


def manifest(**tech: Any) -> dict[str, Any]:
    stack = {"action_tool": "do_it", "fixed_check_tool": "check_it", **tech}
    return {
        "skill_metadata": {"skill_id": "MS01.1_Login"},
        "isatvon_definition": {"t_tech_stack": stack},
        "contracts": {"input_schema": OK_SCHEMA, "output_schema": OK_SCHEMA},
    }


def with_repo(commit: str | None = COMMIT) -> dict[str, Any]:
    repo = {"url": URL, "reason": "costing engine"} | ({"commit": commit} if commit else {})
    return manifest(source_repos=[repo])


def report(**over: Any) -> RepoScanReport:
    values: dict[str, Any] = {
        "url": URL,
        "host": "github.com",
        "commit": COMMIT,
        "decision": FetchDecision.VENDORED,
        "licence": LicenceFinding(spdx="MIT", source="LICENSE", verdict=LicenceVerdict.ALLOWED),
    }
    return RepoScanReport.model_validate({**values, **over})


REPO = RepoRef(URL, COMMIT)


def codes(found: Sequence[Mapping[str, Any]]) -> list[str]:
    return [f["code"] for f in found]


# --- the licence rule ---------------------------------------------------------------------------


def test_wg13_a_repository_with_an_allowed_licence_passes() -> None:
    assert licence_findings(REPO, report()) == []


def test_wg13_t03_a_repository_without_a_scan_report_fails_closed() -> None:
    found = licence_findings(REPO, None)
    assert codes(found) == ["REPO_SCAN_MISSING"]
    assert status_of(found) is GateStatus.FAILED


@pytest.mark.parametrize("verdict", [LicenceVerdict.REFUSED, LicenceVerdict.UNKNOWN])
def test_wg13_a_licence_off_the_allowlist_blocks_unless_a_person_overrode_it(
    verdict: LicenceVerdict,
) -> None:
    refused = report(licence=LicenceFinding(spdx="GPL-3.0", source="LICENSE", verdict=verdict))
    assert codes(licence_findings(REPO, refused)) == ["REPO_LICENCE_NOT_ALLOWED"]
    override = LicenceOverride(reason="legal signed off", by="admin-a")
    allowed = report(
        licence=LicenceFinding(spdx="GPL-3.0", source="LICENSE", verdict=verdict, override=override)
    )
    assert licence_findings(REPO, allowed) == []


def test_wg13_a_repository_with_no_licence_blocks() -> None:
    assert codes(licence_findings(REPO, report(licence=None))) == ["REPO_LICENCE_NOT_ALLOWED"]


def test_wg13_a_refused_fetch_blocks_and_names_why() -> None:
    refused = report(decision=FetchDecision.REFUSED, refusals=[RefusalReason.MALWARE])
    (hit,) = [f for f in licence_findings(REPO, refused) if f["code"] == "REPO_REFUSED"]
    assert "MALWARE" in hit["message"] and hit["severity"] == "HIGH"


def test_wg13_a_copyleft_dependency_blocks_but_an_unknown_one_is_only_reported() -> None:
    deps = [
        DependencyLicence(
            ecosystem="PyPI",
            name="gplpkg",
            version="1",
            spdx="GPL-3.0",
            verdict=LicenceVerdict.REFUSED,
        ),
        DependencyLicence(
            ecosystem="npm", name="mystery", version="2", verdict=LicenceVerdict.UNKNOWN
        ),
    ]
    found = licence_findings(REPO, report(dependency_licences=deps))
    assert {(f["code"], f["severity"]) for f in found} == {
        ("DEPENDENCY_LICENCE_REFUSED", "HIGH"),
        ("DEPENDENCY_LICENCE_UNKNOWN", "MEDIUM"),
    }
    assert status_of(found) is GateStatus.FAILED
    assert status_of([f for f in found if f["severity"] == "MEDIUM"]) is GateStatus.PASSED


# --- the vulnerability rule ---------------------------------------------------------------------


def vuln(severity: VulnSeverity, ident: str = "GHSA-1") -> VulnFinding:
    return VulnFinding(ecosystem="PyPI", package="libx", version="1.0", id=ident, severity=severity)


@pytest.mark.parametrize("severity", [VulnSeverity.HIGH, VulnSeverity.CRITICAL])
def test_wg13_a_high_or_critical_vulnerability_blocks(severity: VulnSeverity) -> None:
    found = vulnerability_findings(REPO, report(vulnerabilities=[vuln(severity)]))
    assert codes(found) == ["DEPENDENCY_VULNERABILITY"] and status_of(found) is GateStatus.FAILED


@pytest.mark.parametrize("severity", [VulnSeverity.MEDIUM, VulnSeverity.LOW, VulnSeverity.UNKNOWN])
def test_wg13_a_lesser_vulnerability_is_reported_and_never_blocks(severity: VulnSeverity) -> None:
    found = vulnerability_findings(REPO, report(vulnerabilities=[vuln(severity)]))
    assert len(found) == 1 and status_of(found) is GateStatus.PASSED


def test_wg13_no_report_means_no_vulnerability_result() -> None:
    found = vulnerability_findings(REPO, None)
    assert codes(found) == ["REPO_SCAN_MISSING"] and status_of(found) is GateStatus.FAILED


# --- the MCP compliance rule --------------------------------------------------------------------


def test_wg13_a_sub_skill_with_two_legal_tools_is_mcp_compliant() -> None:
    assert mcp_findings(manifest()) == []


def test_wg13_t04_the_same_tool_for_action_and_check_is_not_compliant() -> None:
    found = mcp_findings(manifest(fixed_check_tool="do_it"))
    assert codes(found) == ["MCP_TOOLS_NOT_INDEPENDENT"]
    assert status_of(found) is GateStatus.FAILED


def test_wg13_t04_an_invalid_json_schema_is_not_compliant() -> None:
    bad = manifest()
    bad["contracts"]["input_schema"] = {"type": "object", "properties": {"x": {"type": "nope"}}}
    assert codes(mcp_findings(bad)) == ["MCP_INPUT_SCHEMA_INVALID"]
    bad["contracts"]["output_schema"] = {"type": 3}
    assert "MCP_OUTPUT_SCHEMA_INVALID" in codes(mcp_findings(bad))


def test_wg13_t04_a_skill_id_that_gives_no_legal_tool_name_is_not_compliant() -> None:
    bad = manifest()
    bad["skill_metadata"]["skill_id"] = "Login"
    assert codes(mcp_findings(bad)) == ["MCP_TOOL_NAME"]


def test_wg13_the_tool_names_follow_the_workflow_server_rule() -> None:
    """`MS01_1__action` and `MS01_1__check`: no dot, the Sub-Skill's id and the role."""
    assert mcp_findings(manifest()) == []  # McpToolEntry accepts exactly these names


# --- the three rows -------------------------------------------------------------------------------


def test_wg13_a_sub_skill_with_no_repository_records_its_rows_as_passed() -> None:
    assert source_repos(manifest()) == []
    rows = {v.gate: v for v in verdicts_of(manifest(), {})}
    assert set(rows) == {GateName.REPO_LICENCE, GateName.DEPENDENCY_VULN, GateName.MCP_COMPLIANCE}
    assert all(v.status is GateStatus.PASSED for v in rows.values())
    assert rows[GateName.REPO_LICENCE].tool_version == NO_REPOSITORY  # says why it passed


def test_wg13_a_repository_with_a_clean_report_passes_all_three() -> None:
    rows = verdicts_of(with_repo(), {REPO: report()})
    assert source_repos(with_repo()) == [REPO]
    assert [v.status for v in rows] == [GateStatus.PASSED] * 3
    assert rows[0].tool_version != NO_REPOSITORY


def test_wg13_one_bad_row_does_not_hide_the_others() -> None:
    rows = verdicts_of(with_repo(), {REPO: report(vulnerabilities=[vuln(VulnSeverity.HIGH)])})
    assert {v.gate: v.status for v in rows} == {
        GateName.REPO_LICENCE: GateStatus.PASSED,
        GateName.DEPENDENCY_VULN: GateStatus.FAILED,
        GateName.MCP_COMPLIANCE: GateStatus.PASSED,
    }


# --- where the reports are ------------------------------------------------------------------------


def test_wg13_the_report_of_a_repository_is_found_by_url_and_commit(tmp_path: Any) -> None:
    storage = LocalDiskBackend(tmp_path)
    inp = stage_input()
    scope = StorageScope(inp.tenant_id, inp.project_id)
    store_bundle(storage, scope, {"a.py": b"x = 1\n"})  # a vendored tree is not a report
    store_report(storage, scope, report(vulnerabilities=[vuln(VulnSeverity.LOW)]))
    other = RepoRef("https://github.com/acme/other", COMMIT)
    found = storage_report_finder(storage)(inp, [REPO, other, RepoRef(URL, None)])
    pinned = found[REPO]
    assert pinned is not None and pinned.commit == COMMIT
    assert found[other] is None  # another repository
    assert found[RepoRef(URL, None)] is None  # not pinned: nothing to look up


def test_wg13_another_projects_reports_are_never_read(tmp_path: Any) -> None:
    storage = LocalDiskBackend(tmp_path)
    mine, theirs = stage_input(), stage_input(project_id=uuid.uuid4())
    store_report(storage, StorageScope(theirs.tenant_id, theirs.project_id), report())
    assert storage_report_finder(storage)(mine, [REPO]) == {REPO: None}


# --- the import gate on the sandbox adapter (WG13-T01, T02) ------------------------------------


def probe_reply(argv: Sequence[str], **result: Any) -> bytes:
    nonce = argv[-2]
    row = {"ok": True, "type": "", "error": "", "events": [], **result}
    return f"\n{nonce} {json.dumps(row)}\n".encode()


class FakeSandbox:
    """A `SandboxBackend` whose VM answers the import probe the way the real one would."""

    mode = SandboxMode.CUBESANDBOX

    def __init__(self, **result: Any) -> None:
        self.result = result
        self.calls: list[str] = []
        self.created: list[VmSpec] = []
        self.written: dict[str, bytes] = {}
        self.execs: list[tuple[Sequence[str], dict[str, str]]] = []
        self.destroyed: list[str] = []
        self.digest = DIGEST
        self.fail_on: str | None = None
        self.timed_out = False

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_on == name:
            raise SandboxInfraError(InfraReason.HOST_UNREACHABLE, f"{name} refused")

    async def create(self, spec: VmSpec) -> VmHandle:
        self._step("create")
        self.created.append(spec)
        return VmHandle("vm-1")

    async def read_runtime_digest(self, vm: VmHandle) -> str:
        self._step("digest")
        return self.digest

    async def put_files(
        self, vm: VmHandle, files: Mapping[str, bytes], *, system: bool = False
    ) -> None:
        self._step("put_files")
        self.written.update(files)

    async def exec(
        self,
        vm: VmHandle,
        argv: Sequence[str],
        *,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int,
    ) -> ExecResult:
        self._step("exec")
        self.execs.append((argv, dict(env or {})))
        if self.timed_out:
            return ExecResult(-1, b"", b"", timed_out=True)
        return ExecResult(0, probe_reply(argv, **self.result), b"")

    async def get_file(self, vm: VmHandle, path: str, *, max_bytes: int) -> bytes | None:
        return None

    async def destroy(self, vm_id: str) -> None:
        self.calls.append("destroy")
        self.destroyed.append(vm_id)

    async def list_vms(self) -> list[VmInfo]:
        return []


FILES = {"a.py": b"A = 1\n", "pkg/__init__.py": b"", "pkg/b.py": b"from .. import a\n"}


def run_modules(sandbox: FakeSandbox, modules: list[str]) -> Any:
    return SandboxImportRunner(sandbox).run(FILES, modules, IMAGE)


def test_wg13_t01_the_probe_runs_in_a_vm_of_the_pinned_runtime_with_no_egress() -> None:
    sandbox = FakeSandbox()
    outcomes = run_modules(sandbox, ["a", "pkg.b"])
    assert [(o.module, o.ok) for o in outcomes] == [("a", True), ("pkg.b", True)]
    (spec,) = sandbox.created
    assert spec.template_alias == template_alias(DIGEST) and spec.runtime_digest == DIGEST
    assert spec.allow_out == () and dict(spec.hosts) == {}  # no network of any kind
    assert spec.limits == LIMITS and spec.metadata["sbam_import_gate"]
    assert sandbox.calls == ["create", "digest", "put_files", "exec", "exec", "destroy"]
    assert sandbox.destroyed == ["vm-1"]


def test_wg13_each_module_gets_a_fresh_interpreter_with_the_bundle_on_the_path() -> None:
    sandbox = FakeSandbox()
    run_modules(sandbox, ["a", "pkg.b"])
    assert [argv[-1] for argv, _ in sandbox.execs] == ["a", "pkg.b"]
    for argv, env in sandbox.execs:
        assert argv[:2] == ("env", argv[1]) and PYTHON in argv and "-I" in argv
        assert env == {"SBAM_IMPORT_ROOT": BUNDLE_DIR}
    assert len({argv[-2] for argv, _ in sandbox.execs}) == 2  # a nonce per module
    assert set(sandbox.written) == set(vm_files(FILES))


def test_wg13_the_bundle_lands_as_the_package_skill_under_the_bundle_directory() -> None:
    files = vm_files(FILES)
    assert f"{BUNDLE_DIR}/skill/__init__.py" in files
    assert f"{BUNDLE_DIR}/skill/pkg/b.py" in files
    assert probe_argv("n", "pkg.b")[-3:] == (probe_argv("n", "x")[-3], "n", "pkg.b")


@pytest.mark.parametrize("name", ["../x.py", "/etc/x.py", "a//b.py"])
def test_wg13_a_path_outside_the_package_is_refused(name: str) -> None:
    with pytest.raises(ApplicationError) as err:
        vm_files({name: b""})
    assert err.value.type == GATE_INPUT_MISMATCH


def test_wg13_t02_an_import_side_effect_is_a_finding() -> None:
    sandbox = FakeSandbox(events=["socket.connect"])
    (outcome,) = run_modules(sandbox, ["a"])
    assert outcome.events == ("socket.connect",)


def test_wg13_a_module_that_times_out_is_a_timeout_outcome() -> None:
    sandbox = FakeSandbox()
    sandbox.timed_out = True
    (outcome,) = run_modules(sandbox, ["a"])
    assert outcome.timed_out and not outcome.ok


@pytest.mark.parametrize("step", ["create", "digest", "put_files", "exec"])
def test_wg13_trouble_with_the_vm_is_retryable_infrastructure_and_the_vm_is_destroyed(
    step: str,
) -> None:
    sandbox = FakeSandbox()
    sandbox.fail_on = step
    with pytest.raises(ApplicationError) as err:
        run_modules(sandbox, ["a"])
    assert err.value.type == IMPORT_GATE_INFRA and not err.value.non_retryable
    assert sandbox.destroyed == ([] if step == "create" else ["vm-1"])


def test_wg13_a_template_of_another_runtime_is_infrastructure_not_a_verdict() -> None:
    sandbox = FakeSandbox()
    sandbox.digest = "sha256:" + "cd" * 32
    with pytest.raises(ApplicationError) as err:
        run_modules(sandbox, ["a"])
    assert err.value.type == IMPORT_GATE_INFRA and sandbox.destroyed == ["vm-1"]


def test_wg13_an_image_that_is_not_a_digest_is_refused_before_any_vm() -> None:
    sandbox = FakeSandbox()
    with pytest.raises(ApplicationError) as err:
        SandboxImportRunner(sandbox).run(FILES, ["a"], "skill-runtime:latest")
    assert err.value.type == GATE_INPUT_MISMATCH and sandbox.calls == []


# --- the stages against a database --------------------------------------------------------------


def run_stage(stage: Any, inp: BuildStageInput) -> uuid.UUID:
    return uuid.UUID(str(asyncio.run(ActivityEnvironment().run(stage.run, inp)).id))


def gate_rows(sessions: sessionmaker[Session], inp: BuildStageInput) -> dict[str, GateResultRow]:
    return {r.gate: r for r in results(sessions, inp)}


def bundle_sha(sessions: sessionmaker[Session], inp: BuildStageInput) -> str:
    return find_assembled(sessions, inp).bundle_sha


def passed_ast_and_static(
    template: LoadedTemplate,  # noqa: F811
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    inp: BuildStageInput,
) -> None:
    assembled(template, sessions, storage, inp)
    asyncio.run(ActivityEnvironment().run(gate_stage(template, sessions, storage).run, inp))
    from sbam_workers.build.gates.stored_bundle import read_verified

    bundle = read_verified(storage, find_assembled(sessions, inp))
    verdicts = [Verdict(g, GateStatus.PASSED, [], "t/1", "c") for g in STATIC_GATES]
    record_verdicts(sessions, inp, bundle, verdicts)


def import_gate(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, sandbox: FakeSandbox
) -> ImportGate:
    return ImportGate(storage, sessions, SandboxImportRunner(sandbox), lambda _inp: IMAGE)


def test_wg13_t01_the_import_verdict_is_a_gate_row_on_the_bundle_hash(
    template: LoadedTemplate,  # noqa: F811
    tmp_path: Any,
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    passed_ast_and_static(template, sessions, storage, inp)
    sandbox = FakeSandbox()
    run_stage(import_gate(sessions, storage, sandbox), inp)
    row = gate_rows(sessions, inp)[GateName.IMPORT_RESOLUTION.value]
    assert row.status == GateStatus.PASSED.value
    assert row.bundle_sha256 == bundle_sha(sessions, inp)
    assert sandbox.destroyed == ["vm-1"]  # the VM was torn down


def test_wg13_t02_an_import_side_effect_fails_the_gate(
    template: LoadedTemplate,  # noqa: F811
    tmp_path: Any,
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    passed_ast_and_static(template, sessions, storage, inp)
    sandbox = FakeSandbox(events=["subprocess.Popen"])
    with pytest.raises(ApplicationError) as err:
        run_stage(import_gate(sessions, storage, sandbox), inp)
    assert err.value.type == GATE_FAILED and err.value.non_retryable
    row = gate_rows(sessions, inp)[GateName.IMPORT_RESOLUTION.value]
    assert row.status == GateStatus.FAILED.value
    assert row.findings and {f["code"] for f in row.findings} == {"IMPORT_SIDE_EFFECT"}


def compliance(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    given: Mapping[str, Any],
    reports: Mapping[RepoRef, RepoScanReport | None] | None = None,
) -> ComplianceGates:
    return ComplianceGates(
        storage,
        sessions,
        reports=lambda _inp, repos: {r: (reports or {}).get(r) for r in repos},
        manifest_of=lambda _inp, _attempt: given,
    )


def import_passed(
    template: LoadedTemplate,  # noqa: F811
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    inp: BuildStageInput,
) -> None:
    passed_ast_and_static(template, sessions, storage, inp)
    run_stage(import_gate(sessions, storage, FakeSandbox()), inp)


def test_wg13_the_compliance_stage_records_three_rows_on_the_bundle_hash(
    template: LoadedTemplate,  # noqa: F811
    tmp_path: Any,
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    import_passed(template, sessions, storage, inp)
    run_stage(compliance(sessions, storage, manifest()), inp)
    rows = gate_rows(sessions, inp)
    sha = bundle_sha(sessions, inp)
    for gate_name in (GateName.REPO_LICENCE, GateName.DEPENDENCY_VULN, GateName.MCP_COMPLIANCE):
        row = rows[gate_name.value]
        assert (row.status, row.bundle_sha256) == (GateStatus.PASSED.value, sha)


def test_wg13_a_failing_row_is_gate_failed_and_the_others_are_still_recorded(
    template: LoadedTemplate,  # noqa: F811
    tmp_path: Any,
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    import_passed(template, sessions, storage, inp)
    stage = compliance(sessions, storage, with_repo(), {REPO: None})  # fetched, never scanned
    with pytest.raises(ApplicationError) as err:
        run_stage(stage, inp)
    assert err.value.type == GATE_FAILED and err.value.non_retryable
    rows = gate_rows(sessions, inp)
    assert rows[GateName.REPO_LICENCE.value].status == GateStatus.FAILED.value
    assert rows[GateName.DEPENDENCY_VULN.value].status == GateStatus.FAILED.value
    assert rows[GateName.MCP_COMPLIANCE.value].status == GateStatus.PASSED.value
    assert rows[GateName.REPO_LICENCE.value].findings[0]["code"] == "REPO_SCAN_MISSING"


def test_wg13_the_compliance_stage_no_longer_waits_for_an_import_gate_row(
    template: LoadedTemplate,  # noqa: F811
    tmp_path: Any,
) -> None:
    """#922 (ADR 0040, replaces `..._does_not_run_before_the_import_gate_passed`): SBAM no longer
    produces import evidence, so the stage runs on a bundle with only its AST and static rows."""
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    passed_ast_and_static(template, sessions, storage, inp)  # no import gate row
    run_stage(compliance(sessions, storage, manifest()), inp)
    rows = gate_rows(sessions, inp)
    assert GateName.IMPORT_RESOLUTION.value not in rows
    for gate_name in (GateName.REPO_LICENCE, GateName.DEPENDENCY_VULN, GateName.MCP_COMPLIANCE):
        assert rows[gate_name.value].status == GateStatus.PASSED.value


def test_wg13_a_retried_compliance_stage_writes_no_second_row(
    template: LoadedTemplate,  # noqa: F811
    tmp_path: Any,
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    import_passed(template, sessions, storage, inp)
    stage = compliance(sessions, storage, manifest())
    run_stage(stage, inp)
    run_stage(stage, inp)
    names = [r.gate for r in results(sessions, inp)]
    assert names.count(GateName.REPO_LICENCE.value) == 1


# --- certification (WG13-T03, T04) -------------------------------------------------------------

EVIDENCE_GATES = (GateName.REPO_LICENCE, GateName.DEPENDENCY_VULN, GateName.MCP_COMPLIANCE)


def test_wg13_the_three_rows_are_required_for_certification() -> None:
    assert set(EVIDENCE_GATES) <= set(REQUIRED_GATES)
    assert GateName.IMPORT_RESOLUTION not in REQUIRED_GATES  # #922: no import evidence any more
    assert evaluate(evidence()).certified  # every required row, all PASSED


@pytest.mark.parametrize("missing", EVIDENCE_GATES)
def test_wg13_t03_certification_refuses_without_the_licence_vulnerability_or_mcp_result(
    missing: GateName,
) -> None:
    gates = [g for g in evidence().gates if g.gate is not missing]
    verdict = evaluate(evidence(gates=gates))
    assert not verdict.certified
    assert verdict.reasons == (f"MISSING_EVIDENCE:{missing}",)


def test_wg13_t04_an_mcp_compliance_failure_blocks_certification() -> None:
    gates = [
        gate(g.gate, status=GateStatus.FAILED) if g.gate is GateName.MCP_COMPLIANCE else g
        for g in evidence().gates
    ]
    verdict = evaluate(evidence(gates=gates))
    assert not verdict.certified and verdict.reasons == ("GATE_FAILED:MCP_COMPLIANCE",)


@pytest.mark.parametrize("failed", [GateName.REPO_LICENCE, GateName.DEPENDENCY_VULN])
def test_wg13_a_failed_licence_or_vulnerability_row_blocks_certification(
    failed: GateName,
) -> None:
    gates = [
        gate(g.gate, status=GateStatus.FAILED) if g.gate is failed else g for g in evidence().gates
    ]
    assert evaluate(evidence(gates=gates)).reasons == (f"GATE_FAILED:{failed}",)


def test_wg13_evidence_on_another_bundle_does_not_count() -> None:
    gates = [
        gate(g.gate, bundle_sha256="e" * 64) if g.gate is GateName.REPO_LICENCE else g
        for g in evidence().gates
    ]
    assert evaluate(evidence(gates=gates)).reasons == ("EVIDENCE_HASH_MISMATCH",)


def test_wg13_a_medium_finding_is_reported_without_blocking() -> None:
    rows = verdicts_of(with_repo(), {REPO: report(vulnerabilities=[vuln(VulnSeverity.MEDIUM)])})
    found = next(v for v in rows if v.gate is GateName.DEPENDENCY_VULN)
    assert found.status is GateStatus.PASSED and found.findings[0]["severity"] == Severity.MEDIUM
