"""E11.4 unit tests: `sbam-corrector` findings -> ISATVON patch / body edit -> next attempt.

The Sub-Skill is MS01.1 (tests/fixtures/isa14/ms01_1_structured.json), its template and bodies are
the same fixtures E11.2/E11.3 use. A `FakeBifrost` replays one or more recorded-shaped completions
per test, so these never touch a real Bifrost / OpenRouter endpoint (TD §19.3: unit tests use
fakes).
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from codegen_cases import ms01_bodies, ms01_params
from fixtures import fixture_path
from llm_db import TENANT, FakeBifrost, completion, llm_sessions, make_client
from sbam_api.builds.models import CodeAttempt, CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.models import GateResultRow
from sbam_api.manifests.repository import GateResultRepository
from sbam_contracts.build import (
    MAX_CORRECTIONS,
    CorrectionInput,
    CorrectionResult,
    IsatvonPatchClass,
)
from sbam_workers.build.codegen.bodies import CodeBodies
from sbam_workers.build.codegen.correction import ALIAS as CORRECTOR_ALIAS
from sbam_workers.build.codegen.store import LoadedTemplate, load_template
from sbam_workers.build.correct import (
    AUDIT_ACTION,
    AUDIT_ENTITY,
    INPUT_MISMATCH,
    Correct,
    CorrectionInputs,
    SqlCorrectionStore,
    stage_key,
)
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def sub_skill() -> dict[str, Any]:
    data = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def db() -> sessionmaker[Session]:
    sessions = llm_sessions()
    from sbam_api.audit.models import AuditLog  # local import: avoid module import cycle at top

    bind = sessions.kw["bind"]
    CodeAttempt.metadata.create_all(bind, tables=[CodeAttempt.__table__])  # type: ignore[list-item]
    AuditLog.metadata.create_all(bind, tables=[AuditLog.__table__])  # type: ignore[list-item]
    GateResultRow.metadata.create_all(bind, tables=[GateResultRow.__table__])  # type: ignore[list-item]
    return sessions


def history(sessions: sessionmaker[Session], inp: CorrectionInput) -> list[Any]:
    """The audit trail for this Sub-Skill (empty: no ISATVON patch was accepted)."""
    from sbam_api.audit import repository as audit

    with sessions() as session:
        return audit.entity_history(session, inp.tenant_id, AUDIT_ENTITY, str(inp.sub_skill_id))


def ctx(inp: CorrectionInput) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def correction_input(**over: Any) -> CorrectionInput:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "project_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "correlation_id": "corr-1",
        "workflow_id": uuid.uuid4(),
        "sub_skill_id": uuid.uuid4(),
        "build_no": 1,
        "cycle": 0,
        "attempt": 0,
        "failed_gate": "AST_CONFORMANCE",
        "finding_ids": [],
    }
    return CorrectionInput(**{**values, **over})


def seed_failed_attempt(
    sessions: sessionmaker[Session],
    template: LoadedTemplate,
    inp: CorrectionInput,
    findings: list[dict[str, Any]],
    bodies: dict[str, Any] | None = None,
    *,
    attempt: int | None = None,
    correction_reason: str | None = None,
) -> uuid.UUID:
    """The failed pass's `code_attempts` row the correction reads from."""
    with sessions() as session:
        row = CodeAttemptRepository(session, ctx(inp)).record(
            {
                "workflow_id": inp.workflow_id,
                "sub_skill_id": inp.sub_skill_id,
                "manifest_id": None,
                "build_no": inp.build_no,
                "cycle": inp.cycle,
                "attempt": inp.attempt if attempt is None else attempt,
                "template_name": template.name,
                "template_version": template.version,
                "template_sha256": template.sha256,
                "status": CodeAttemptStatus.PRECHECK_FAILED,
                "bodies": ms01_bodies() if bodies is None else bodies,
                "llm_call_ids": [],
                "trace_id": "trace-1",
                "findings": findings,
                "correction_reason": correction_reason,
                "created_by": "sbam-worker",
            }
        )
        session.commit()
        return row.id


def finding(**over: Any) -> dict[str, Any]:
    values = {
        "gate": "AST_CONFORMANCE",
        "code": "STATE_QUERY_INVALID",
        "message": "missing return field used by success_gate",
        "file": "action_tool.py",
        "line": 12,
    }
    return {**values, **over}


def build(
    template: LoadedTemplate,
    sessions: sessionmaker[Session],
    bifrost_replies: list[Any],
    *,
    trace: str = "",
    fake: Any = None,
) -> Correct:
    params = ms01_params()
    from sbam_workers.build.codegen.render import render_skeleton

    skeleton = render_skeleton(template, params)
    client, _ = make_client(fake or _fake(bifrost_replies), sessions=sessions)

    async def inputs(inp: CorrectionInput) -> CorrectionInputs:
        return CorrectionInputs(
            template=template,
            params=params,
            skeleton=skeleton,
            modules=(),
            sub_skill=sub_skill(),
            manifest_id=None,
            trace=trace,
        )

    return Correct(inputs, client, SqlCorrectionStore(sessions))


def _fake(replies: list[Any]) -> Any:
    from llm_db import FakeBifrost

    return FakeBifrost(replies)


def corrector_reply(**fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"isatvon_patch": None, "bodies": None, "rationale": "fixed it"}
    values.update(fields)
    return completion(json.dumps({**values}), model="sbam-corrector-primary")


def run(impl: Correct, inp: CorrectionInput) -> uuid.UUID:
    return asyncio.run(ActivityEnvironment().run(impl.run, inp)).id


def attempt_row(sessions: sessionmaker[Session], inp: CorrectionInput, attempt: int) -> CodeAttempt:
    with sessions() as session:
        row = CodeAttemptRepository(session, ctx(inp)).find(
            inp.sub_skill_id, inp.build_no, inp.cycle, attempt
        )
        assert row is not None
        session.expunge(row)
        return row


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.0")


# --- E11.4-T01: defect fixed ----------------------------------------------------------------------


def test_e11_4_t01_defect_fixed_adds_missing_field(template: LoadedTemplate) -> None:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="AST_CONFORMANCE")
    seed_failed_attempt(sessions, template, inp, [finding()])
    fixed_bodies = ms01_bodies(check_body=ms01_bodies()["check_body"] + "extra_field = True\n")
    reply = corrector_reply(bodies=fixed_bodies, rationale="added the missing return field")
    impl = build(template, sessions, [reply])
    attempt_id = run(impl, inp)
    row = attempt_row(sessions, inp, 1)
    assert row.id == attempt_id
    assert row.status == CodeAttemptStatus.BODIES_GENERATED
    assert row.findings == []
    assert row.correction_reason is not None and "AST_CONFORMANCE" in row.correction_reason


# --- E11.4-T02: escalated at 2 (covered at the Correct-activity boundary; the 2-attempt cap and
# NEEDS_FDE_REVIEW transition live in the SubSkillBuild workflow (pipeline/, D3-owned, #107), which
# this issue does not touch. What #115 owns and must prove: a third attempt (>= MAX_CORRECTIONS)
# is refused by the activity itself, fail-closed, never trusting the LLM or the caller.) -----------


def test_e11_4_t02_third_attempt_is_refused_fail_closed(template: LoadedTemplate) -> None:
    sessions = db()
    inp = correction_input(attempt=MAX_CORRECTIONS, failed_gate="AST_CONFORMANCE")
    impl = build(template, sessions, [corrector_reply()])
    with pytest.raises(ApplicationError) as caught:
        run(impl, inp)
    assert caught.value.type == INPUT_MISMATCH and caught.value.non_retryable
    with sessions() as session:
        assert CodeAttemptRepository(session, ctx(inp)).list() == []


# --- E11.4-T03: ISATVON patch path ----------------------------------------------------------------


def test_e11_4_t03_isatvon_patch_recorded_as_sbam_corrector(template: LoadedTemplate) -> None:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="HARNESS")
    seed_failed_attempt(sessions, template, inp, [finding(code="BUDGET_EXCEEDED")])
    patch = [
        {
            "op": "replace",
            "path": "/isatvon_definition/t_tech_stack/gateway_routing",
            "value": "MCP_Direct_Gateway",
        }
    ]
    reply = corrector_reply(isatvon_patch=patch, rationale="relaxed the gateway routing")
    impl = build(template, sessions, [reply])
    run(impl, inp)
    row = attempt_row(sessions, inp, 1)
    assert row.status == CodeAttemptStatus.BODIES_GENERATED
    with sessions() as session:
        from sbam_api.audit import repository as audit

        key = stage_key(inp, 1)
        hit = audit.find_by_key(session, inp.tenant_id, AUDIT_ENTITY, str(inp.sub_skill_id), key)
        assert hit is not None
        assert hit.action == AUDIT_ACTION and hit.actor_id == CORRECTOR_ALIAS
        assert hit.after is not None
        assert hit.after["sub_skill"]["isatvon_definition"]["t_tech_stack"]["gateway_routing"] == (
            "MCP_Direct_Gateway"
        )
        assert hit.before is not None and hit.before["sub_skill"] != hit.after["sub_skill"]
        assert hit.after["patch"] == patch


# --- E11.4-T04: forbidden patch path (safety check 1) ---------------------------------------------


def test_e11_4_t04_forbidden_patch_path_rejected(template: LoadedTemplate) -> None:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="GOVERNANCE")
    seed_failed_attempt(sessions, template, inp, [finding()])
    patch = [{"op": "replace", "path": "/governance/lifecycle/state", "value": "APPROVED"}]
    reply = corrector_reply(isatvon_patch=patch, rationale="tried to approve itself")
    impl = build(template, sessions, [reply])
    run(impl, inp)
    row = attempt_row(sessions, inp, 1)
    assert row.status == CodeAttemptStatus.PRECHECK_FAILED
    assert [f["code"] for f in row.findings] == ["PATCH_FORBIDDEN"]
    assert history(sessions, inp) == []


@pytest.mark.parametrize(
    "path",
    [
        "/skill_metadata/skill_id",
        "/sbam_build_record/build_no",
        "/srim_runtime_definition/version",
    ],
)
def test_e11_4_t04_other_forbidden_roots_rejected(template: LoadedTemplate, path: str) -> None:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="GOVERNANCE")
    seed_failed_attempt(sessions, template, inp, [finding()])
    reply = corrector_reply(
        isatvon_patch=[{"op": "replace", "path": path, "value": "x"}], rationale="forbidden"
    )
    impl = build(template, sessions, [reply])
    run(impl, inp)
    row = attempt_row(sessions, inp, 1)
    assert [f["code"] for f in row.findings] == ["PATCH_FORBIDDEN"]


# --- E11.4-T05: allowlist widening via helper import (safety check 3 / AST_IMPORTS) ---------------


def test_e11_4_t05_added_pickle_import_counts_as_attempt(template: LoadedTemplate) -> None:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="AST_IMPORTS")
    seed_failed_attempt(sessions, template, inp, [finding()])
    bad_bodies = ms01_bodies(helpers="import pickle\n")
    reply = corrector_reply(bodies=bad_bodies, rationale="tried to widen the allowlist")
    impl = build(template, sessions, [reply])
    attempt_id = run(impl, inp)  # a rejected correction is still a recorded, spent attempt
    row = attempt_row(sessions, inp, 1)
    assert row.id == attempt_id
    assert row.status == CodeAttemptStatus.PRECHECK_FAILED
    assert "FORBIDDEN_IMPORT" in [f["code"] for f in row.findings]


# --- E11.4-T06: invalid patched ISATVON (safety check: patched ISATVON re-validated) --------------


def test_e11_4_t06_action_tool_equals_check_tool_rejected(template: LoadedTemplate) -> None:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="ISATVON_CONTRACT")
    seed_failed_attempt(sessions, template, inp, [finding()])
    same_tool = sub_skill()["isatvon_definition"]["t_tech_stack"]["action_tool"]
    patch = [
        {
            "op": "replace",
            "path": "/isatvon_definition/t_tech_stack/fixed_check_tool",
            "value": same_tool,
        }
    ]
    reply = corrector_reply(isatvon_patch=patch, rationale="collapsed the two tools")
    impl = build(template, sessions, [reply])
    run(impl, inp)
    row = attempt_row(sessions, inp, 1)
    assert row.status == CodeAttemptStatus.PRECHECK_FAILED
    assert [f["code"] for f in row.findings] == ["ISATVON_CONTRACT"]
    assert history(sessions, inp) == []


# --- edge case: empty patch and identical bodies --------------------------------------------------


def test_e11_4_empty_patch_and_identical_bodies_is_bodies_generated(
    template: LoadedTemplate,
) -> None:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="AST_CONFORMANCE")
    seed_failed_attempt(sessions, template, inp, [finding()])
    reply = corrector_reply(isatvon_patch=None, bodies=None, rationale="no change needed")
    impl = build(template, sessions, [reply])
    run(impl, inp)
    row = attempt_row(sessions, inp, 1)
    assert row.status == CodeAttemptStatus.BODIES_GENERATED
    assert row.bodies == CodeBodies.model_validate(ms01_bodies()).model_dump()
    assert history(sessions, inp) == []


# --- E11.4-T07: replay (worker crash after corrector response) ------------------------------------


def test_e11_4_t07_replay_reuses_cached_answer_one_attempt_row(template: LoadedTemplate) -> None:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="AST_CONFORMANCE")
    seed_failed_attempt(sessions, template, inp, [finding()])
    reply = corrector_reply(rationale="fixed on the first try")
    impl = build(template, sessions, [reply])
    first = run(impl, inp)
    second = run(impl, inp)  # the activity replays: the row already exists, no new LLM call
    assert first == second
    with sessions() as session:
        rows = CodeAttemptRepository(session, ctx(inp)).list()
        assert len([r for r in rows if r.attempt == 1]) == 1


# --- E11.4-T08: Bifrost down -> transport retry, not an attempt -----------------------------------


def test_e11_4_t08_transport_errors_retry_without_spending_an_attempt(
    template: LoadedTemplate,
) -> None:
    import httpx

    sessions = db()
    inp = correction_input(attempt=0, failed_gate="AST_CONFORMANCE")
    seed_failed_attempt(sessions, template, inp, [finding()])
    reply = corrector_reply(rationale="fixed after Bifrost recovered")
    # Two 503s, then a success: llm_transport_attempts=3 absorbs them inside one activity call.
    replies: list[Any] = [httpx.Response(503, text="down"), httpx.Response(503, text="down"), reply]
    impl = build(template, sessions, replies)
    attempt_id = run(impl, inp)
    row = attempt_row(sessions, inp, 1)
    assert row.id == attempt_id and row.status == CodeAttemptStatus.BODIES_GENERATED
    with sessions() as session:
        rows = CodeAttemptRepository(session, ctx(inp)).list()
        assert len(rows) == 2  # the failing attempt 0 (seeded) + the one successful attempt 1


def test_e11_4_t08_exhausted_transport_retries_raise_and_write_no_attempt(
    template: LoadedTemplate,
) -> None:
    import httpx
    from sbam_workers.llm.client import LlmTransportError

    sessions = db()
    inp = correction_input(attempt=0, failed_gate="AST_CONFORMANCE")
    seed_failed_attempt(sessions, template, inp, [finding()])
    replies: list[Any] = [httpx.Response(503, text="down")] * 5  # more than llm_transport_attempts
    impl = build(template, sessions, replies)
    with pytest.raises(LlmTransportError):
        run(impl, inp)
    with sessions() as session:
        rows = CodeAttemptRepository(session, ctx(inp)).list()
        assert [r.attempt for r in rows] == [0]  # no attempt-1 row: the call never answered


# --- A-20 (#446): the result says what the correction did to the ISATVON --------------------------

ROUTING = "/isatvon_definition/t_tech_stack/gateway_routing"
PIN = "/isatvon_definition/t_tech_stack/libraries/0/version_constraint"


def run_result(impl: Correct, inp: CorrectionInput) -> CorrectionResult:
    return asyncio.run(ActivityEnvironment().run(impl.run, inp))


def corrected_with(
    template: LoadedTemplate, patch: list[dict[str, Any]] | None
) -> tuple[sessionmaker[Session], CorrectionInput, CorrectionResult, Correct]:
    sessions = db()
    inp = correction_input(attempt=0, failed_gate="HARNESS")
    seed_failed_attempt(sessions, template, inp, [finding(code="BUDGET_EXCEEDED")])
    impl = build(template, sessions, [corrector_reply(isatvon_patch=patch, rationale="why")])
    return sessions, inp, run_result(impl, inp), impl


def audit_after(sessions: sessionmaker[Session], inp: CorrectionInput) -> dict[str, Any] | None:
    from sbam_api.audit import repository as audit

    with sessions() as session:
        row = audit.find_by_key(
            session, inp.tenant_id, AUDIT_ENTITY, str(inp.sub_skill_id), stage_key(inp, 1)
        )
        return None if row is None else row.after


def test_a20_t01_a_behaviour_patch_is_reported_with_the_paths_it_changed(
    template: LoadedTemplate,
) -> None:
    patch = [{"op": "replace", "path": ROUTING, "value": "MCP_Direct_Gateway"}]
    sessions, inp, result, _ = corrected_with(template, patch)
    assert result.isatvon_patch is IsatvonPatchClass.BEHAVIOUR
    assert result.changed_paths == [ROUTING]
    after = audit_after(sessions, inp)
    assert after is not None  # the diff the FDE reviews stays in the audit row
    assert after["patch_class"] == "BEHAVIOUR" and after["changed_paths"] == [ROUTING]
    assert after["patch"] == patch and after["isatvon_sha256"]


def test_a20_t02_a_library_patch_is_neutral(template: LoadedTemplate) -> None:
    patch = [{"op": "replace", "path": PIN, "value": ">=2.0,<9"}]
    sessions, inp, result, _ = corrected_with(template, patch)
    assert result.isatvon_patch is IsatvonPatchClass.NEUTRAL
    assert result.changed_paths == [PIN]
    after = audit_after(sessions, inp)
    assert after is not None and after["patch_class"] == "NEUTRAL"


def test_a20_a_correction_without_a_patch_reports_none(template: LoadedTemplate) -> None:
    sessions, inp, result, _ = corrected_with(template, None)
    assert (result.isatvon_patch, result.changed_paths) == (IsatvonPatchClass.NONE, [])
    assert audit_after(sessions, inp) is None  # nothing was patched, nothing to review


def test_a20_a_rejected_patch_reports_none(template: LoadedTemplate) -> None:
    """A patch the corrector may not make is not accepted, so nothing was changed to hold."""
    patch = [{"op": "replace", "path": "/skill_metadata/skill_id", "value": "X"}]
    sessions, inp, result, _ = corrected_with(template, patch)
    assert result.isatvon_patch is IsatvonPatchClass.NONE
    assert attempt_row(sessions, inp, 1).status == CodeAttemptStatus.PRECHECK_FAILED
    assert audit_after(sessions, inp) is None


def test_a20_a_retried_activity_reports_the_same_class(template: LoadedTemplate) -> None:
    """A retry finds the attempt row it already wrote; the class comes back from the audit row, so
    the workflow still holds the patch."""
    patch = [{"op": "replace", "path": ROUTING, "value": "MCP_Direct_Gateway"}]
    _, inp, first, impl = corrected_with(template, patch)
    assert run_result(impl, inp) == first  # same id, same class, same paths


def test_a20_an_audit_row_from_before_the_class_was_stored_is_classified_from_its_patch(
    template: LoadedTemplate,
) -> None:
    from sbam_api.audit import repository as audit

    patch = [{"op": "replace", "path": ROUTING, "value": "MCP_Direct_Gateway"}]
    sessions, inp, first, impl = corrected_with(template, patch)
    with sessions() as session:
        row = audit.find_by_key(
            session, inp.tenant_id, AUDIT_ENTITY, str(inp.sub_skill_id), stage_key(inp, 1)
        )
        assert row is not None and row.after is not None
        row.after = {
            k: v for k, v in row.after.items() if k not in ("patch_class", "changed_paths")
        }
        session.commit()
    again = run_result(impl, inp)
    assert again.isatvon_patch is IsatvonPatchClass.BEHAVIOUR and again.changed_paths == [ROUTING]
    assert again.id == first.id


# --- #514: the findings of the code gates reach the corrector ---------------------------------


def gate_row(
    sessions: sessionmaker[Session],
    inp: CorrectionInput,
    gate: str,
    status: str,
    findings: list[dict[str, Any]],
    *,
    attempt: int | None = None,
    sub_skill_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> None:
    with sessions() as session:
        repo = GateResultRepository(
            session, TenantContext(tenant_id or inp.tenant_id, inp.project_id)
        )
        repo.record_stage(
            {
                "sub_skill_id": sub_skill_id or inp.sub_skill_id,
                "gate": gate,
                "status": status,
                "findings": findings,
                "tool_version": "test",
                "config_sha256": "0" * 64,
                "build_no": inp.build_no,
                "cycle": inp.cycle,
                "attempt": inp.attempt if attempt is None else attempt,
                "created_by": "SYSTEM",
            }
        )
        session.commit()


def failure(code: str, message: str = "a finding") -> dict[str, Any]:
    return {"code": code, "message": message, "file": "server.py", "line": 7}


def sent_to_the_model(fake: FakeBifrost) -> str:
    assert len(fake.requests) == 1
    return json.dumps(fake.requests[0])


def test_a514_t01_the_findings_of_the_failed_gates_of_this_pass_reach_the_corrector(
    template: LoadedTemplate,
) -> None:
    sessions, fake = db(), FakeBifrost([corrector_reply()])
    inp = correction_input(attempt=0, failed_gate="ast_gate")
    attempt_id = seed_failed_attempt(sessions, template, inp, [])  # the AST gate leaves none here
    inp = inp.model_copy(update={"finding_ids": [attempt_id]})  # what GateFailed carries
    gate_row(sessions, inp, "AST_SKELETON", "FAILED", [failure("SKELETON_MISMATCH_X")])
    gate_row(sessions, inp, "AST_NO_MANUAL_LOOP", "FAILED", [failure("WHILE_LOOP_X")])
    gate_row(sessions, inp, "AST_SYNTAX", "PASSED", [failure("PASSED_GATE_X")])  # not a failure
    gate_row(sessions, inp, "AST_IMPORTS", "FAILED", [failure("OTHER_PASS_X")], attempt=1)
    gate_row(
        sessions, inp, "AST_IMPORTS", "FAILED", [failure("OTHER_SUB_X")], sub_skill_id=uuid.uuid4()
    )
    gate_row(
        sessions, inp, "AST_IMPORTS", "FAILED", [failure("OTHER_TENANT_X")], tenant_id=uuid.uuid4()
    )
    run(build(template, sessions, [], fake=fake), inp)
    sent = sent_to_the_model(fake)
    assert "SKELETON_MISMATCH_X" in sent and "WHILE_LOOP_X" in sent
    assert "AST_SKELETON" in sent and "AST_NO_MANUAL_LOOP" in sent  # each finding names its gate
    for left_out in ("PASSED_GATE_X", "OTHER_PASS_X", "OTHER_SUB_X", "OTHER_TENANT_X"):
        assert left_out not in sent


def test_a514_t02_the_attempts_own_findings_still_reach_it_next_to_the_gates(
    template: LoadedTemplate,
) -> None:
    sessions, fake = db(), FakeBifrost([corrector_reply()])
    inp = correction_input(attempt=0, failed_gate="ast_gate")
    attempt_id = seed_failed_attempt(sessions, template, inp, [finding(code="PRECHECK_X")])
    inp = inp.model_copy(update={"finding_ids": [attempt_id]})
    gate_row(sessions, inp, "AST_SKELETON", "FAILED", [failure("SKELETON_MISMATCH_X")])
    run(build(template, sessions, [], fake=fake), inp)
    sent = sent_to_the_model(fake)
    assert "PRECHECK_X" in sent and "SKELETON_MISMATCH_X" in sent


def test_a514_t03_a_pass_with_no_failed_gate_adds_nothing(template: LoadedTemplate) -> None:
    sessions, fake = db(), FakeBifrost([corrector_reply()])
    inp = correction_input(attempt=0, failed_gate="HARNESS")
    attempt_id = seed_failed_attempt(sessions, template, inp, [finding(code="HARNESS_X")])
    inp = inp.model_copy(update={"finding_ids": [attempt_id]})
    gate_row(sessions, inp, "AST_SYNTAX", "PASSED", [])
    run(build(template, sessions, [], fake=fake), inp)
    assert "HARNESS_X" in sent_to_the_model(fake)
