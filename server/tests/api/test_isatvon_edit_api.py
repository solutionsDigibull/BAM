"""E8.4 edit endpoints through the real app and auth chain: PATCH / DELETE a Sub-Skill, PATCH a
Macro Skill, and the edit history. Edits revalidate, move the state machine and are audited."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from runs_db import FakeClient
from sbam_api.audit.models import AuditLog
from sbam_api.core.conventions import ApiError
from sbam_api.isatvon import edits
from sbam_api.isatvon.models import MacroSkill, SubSkill, SubSkillEdit
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.workflows.models import Workflow
from sbam_contracts.api import API_BASE
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from test_auth import TENANT_B, problem
from test_isatvon_validate_api import IDEF, sample
from test_isatvon_validate_api import Harness as ValidateHarness

BUDGET = IDEF + "v_variables.budget_limit"
B5, B6, B7 = (f"{n}000ms timeout per validation cycle" for n in (5, 6, 7))


class Harness(ValidateHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        SubSkillEdit.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.temporal = FakeClient()
        self.client.app.state.temporal = self.temporal  # type: ignore[attr-defined]

    def sub_path(self, sub_id: uuid.UUID, suffix: str = "") -> str:
        return f"/projects/{self.project}/workflows/{self.wf}/sub-skills/{sub_id}{suffix}"

    def etag_of(self, sub_id: uuid.UUID) -> str:
        with self.sessions() as s:
            row = s.get(SubSkill, sub_id)
            assert row is not None
            return f'"{row.content_sha256}"'

    def patch(
        self,
        sub_id: uuid.UUID,
        body: Any,
        if_match: str | None = "current",
        token: str | None = None,
        path: str | None = None,
    ) -> Any:
        if if_match == "current":
            if_match = self.etag_of(sub_id)
        headers = {} if if_match is None else {"If-Match": if_match}
        return self.call(
            "PATCH", path or self.sub_path(sub_id), token or self.fde(), headers=headers, json=body
        )

    def set_workflow_state(self, state: str) -> None:
        with self.sessions() as s:
            row = s.get(Workflow, self.wf)
            assert row is not None
            row.state = state
            s.commit()

    def orders(self, macro_id: uuid.UUID | None = None) -> dict[uuid.UUID, int]:
        with self.sessions() as s:
            rows = s.scalars(
                select(SubSkill).where(
                    SubSkill.macro_skill_id == (macro_id or self.macro),
                    SubSkill.deleted_at.is_(None),
                )
            ).all()
            return {r.id: r.order for r in rows}

    def edit_rows(self, sub_id: uuid.UUID) -> list[SubSkillEdit]:
        with self.sessions() as s:
            return list(s.scalars(select(SubSkillEdit).where(SubSkillEdit.sub_skill_id == sub_id)))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def other_skill(n: int) -> dict[str, Any]:
    return sample(
        **{
            "skill_metadata.skill_id": f"MS01.{n}_Other_Skill",
            "skill_metadata.skill_name": f"Other {n}",
        }
    )


def test_e8_4_t01_valid_edit_revalidates_and_logs(h: Harness) -> None:
    sub = h.add_sub_skill(sample(), state="VALID")
    before = h.etag_of(sub)
    res = h.patch(sub, {"changes": {BUDGET: B6}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "VALID"
    assert body["sub_skill"]["isatvon_definition"]["v_variables"]["budget_limit"] == B6
    assert res.headers["ETag"] == h.etag_of(sub) != before
    (edit,) = h.edit_rows(sub)
    assert (edit.path, edit.old, edit.new) == (
        BUDGET,
        B5,
        B6,
    )
    with h.sessions() as s:
        actions = [a.action for a in s.scalars(select(AuditLog))]
    assert "ISATVON_EDIT" in actions


def test_e8_4_t01_the_isatvon_prefix_is_read_as_isatvon_definition(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    res = h.patch(
        sub,
        {"changes": {"isatvon.v_variables.budget_limit": B6}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["sub_skill"]["isatvon_definition"]["v_variables"]["budget_limit"] == B6


def test_e8_4_t02_type_error_is_422_with_a_path_and_stores_nothing(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    before = h.etag_of(sub)
    res = h.patch(sub, {"changes": {IDEF + "v_variables.max_iterations": "lots"}})
    assert res.status_code == 422, res.text
    assert h.etag_of(sub) == before
    assert h.edit_rows(sub) == []
    assert h.state_of(sub) == "VALID"


def test_e8_4_t03_rule_violation_stores_the_edit_as_invalid(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    tool = sample()["isatvon_definition"]["t_tech_stack"]["action_tool"]
    res = h.patch(sub, {"changes": {IDEF + "t_tech_stack.fixed_check_tool": tool}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "INVALID"
    assert [e["code"] for e in body["validation_errors"]] == ["TOOLS_NOT_DECOUPLED"]
    assert h.state_of(sub) == "INVALID"
    assert len(h.edit_rows(sub)) == 1


def test_e8_4_an_invalid_sub_skill_can_be_fixed_back_to_valid(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    tool = sample()["isatvon_definition"]["t_tech_stack"]["action_tool"]
    fixed = sample()["isatvon_definition"]["t_tech_stack"]["fixed_check_tool"]
    h.patch(sub, {"changes": {IDEF + "t_tech_stack.fixed_check_tool": tool}})
    res = h.patch(sub, {"changes": {IDEF + "t_tech_stack.fixed_check_tool": fixed}})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "VALID"


def test_e8_8_a_row_carries_the_sow_refs_it_cites(h: Harness) -> None:
    """The drawer's citations (E8.8): the SOW features the Sub-Skill covers, read-only."""
    sub = h.add_sub_skill(sample(), state="VALID")
    res = h.patch(sub, {"changes": {BUDGET: B6}})
    assert res.status_code == 200, res.text
    assert res.json()["sow_refs"] == ["F-ACCESS-1"]
    problem(h.patch(sub, {"changes": {BUDGET: B5}, "sow_refs": ["F-X"]}), 422, "READ_ONLY")


def test_e9_1_an_edit_moves_validated_at_with_the_validation(h: Harness) -> None:
    """The manifest's timestamps (E9.1): set when an edit passes, cleared when it doesn't."""
    sub = h.add_sub_skill(sample(), state="VALID")
    tool = sample()["isatvon_definition"]["t_tech_stack"]["action_tool"]
    fixed = sample()["isatvon_definition"]["t_tech_stack"]["fixed_check_tool"]

    def validated_at() -> object:
        with h.sessions() as s:
            row = s.get(SubSkill, sub)
            assert row is not None
            return row.validated_at

    h.patch(sub, {"changes": {IDEF + "t_tech_stack.fixed_check_tool": tool}})
    assert validated_at() is None
    h.patch(sub, {"changes": {IDEF + "t_tech_stack.fixed_check_tool": fixed}})
    assert validated_at() is not None


def test_e8_4_t04_stale_if_match_is_412_and_missing_is_428(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    body = {"changes": {IDEF + "v_variables.max_iterations": 5}}
    problem(h.patch(sub, body, if_match='"' + "0" * 64 + '"'), 412, "PRECONDITION_FAILED")
    problem(h.patch(sub, body, if_match=None), 428, "PRECONDITION_REQUIRED")
    assert h.edit_rows(sub) == []


@pytest.mark.parametrize(
    "body",
    [
        {"state": "VALID"},
        {"content_sha256": "0" * 64},
        {"changes": {"skill_metadata.skill_id": "MS01.9_Hijack"}},
    ],
)
def test_e8_4_t05_platform_owned_fields_are_read_only(h: Harness, body: dict[str, Any]) -> None:
    sub = h.add_sub_skill(sample())
    problem(h.patch(sub, body), 422, "READ_ONLY")
    assert h.edit_rows(sub) == []


def test_e8_4_t06_there_is_no_create_route(h: Harness) -> None:
    path = f"/projects/{h.project}/workflows/{h.wf}/sub-skills"
    assert h.call("POST", path, h.fde(), json={}).status_code in (404, 405)
    spec = h.client.get(f"{API_BASE}/openapi.json").json()
    assert "post" not in spec["paths"].get(
        f"{API_BASE}{path.replace(h.project, '{project_id}').replace(str(h.wf), '{workflow_id}')}",
        {},
    )


def test_e8_4_t07_frozen_workflow_is_423_and_building_is_409(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    body = {"changes": {IDEF + "v_variables.max_iterations": 5}}
    h.set_workflow_state("FROZEN")
    problem(h.patch(sub, body), 423, "WORKFLOW_FROZEN")
    h.set_workflow_state("BUILDING")
    problem(h.patch(sub, body), 409, "WORKFLOW_BUILDING")
    assert h.edit_rows(sub) == []


def test_e8_4_t08_another_tenant_or_workflow_is_404(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    body = {"changes": {IDEF + "v_variables.max_iterations": 5}}
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.patch(sub, body, token=other), 404, "NOT_FOUND")
    problem(h.patch(uuid.uuid4(), body, if_match='"x"'), 404, "NOT_FOUND")
    other_wf = h.workflow(2, sow_version=h.generated(h.structured))
    wrong = f"/projects/{h.project}/workflows/{other_wf}/sub-skills/{sub}"
    problem(h.patch(sub, body, path=wrong), 404, "NOT_FOUND")


# --- regenerate (E8.4-T09) ------------------------------------------------------------------------


def regenerate(
    h: Harness, entity: uuid.UUID, kind: str, body: Any = None, token: str | None = None, **hdr: str
) -> Any:
    path = f"/projects/{h.project}/workflows/{h.wf}/{kind}/{entity}/regenerate"
    return h.call("POST", path, token or h.fde(), headers=hdr, json=body)


def test_e8_4_t09_macro_regenerate_starts_the_run_and_replays_by_key(h: Harness) -> None:
    h.add_sub_skill(sample())
    key = {"Idempotency-Key": "key-isatvon-regen-1"}
    res = regenerate(h, h.macro, "macro-skills", {"hint": "  Split it in two\x07 "}, **key)
    assert res.status_code == 202, res.text
    run = res.json()["run"]
    assert run["type"] == "IsatvonRegenerate"
    assert run["temporal_workflow_id"] == f"isatvon-regen-{h.macro}"
    [call] = h.temporal.calls
    assert call["task_queue"] == "sbam-llm" and call["id_conflict_policy"].name == "FAIL"
    arg = call["arg"]
    assert (arg.macro_skill_id, arg.sub_skill_id, arg.hint) == (h.macro, None, "Split it in two")
    again = regenerate(h, h.macro, "macro-skills", {"hint": "Split it in two"}, **key)
    assert again.json()["run"]["id"] == run["id"] and len(h.temporal.calls) == 1
    problem(regenerate(h, h.macro, "macro-skills"), 409, "CONFLICT")  # one at a time


def test_e8_4_t09_sub_skill_regenerate_names_the_sub_and_shares_the_macro_run(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    res = regenerate(h, sub, "sub-skills")
    assert res.status_code == 202, res.text
    [call] = h.temporal.calls
    assert (call["arg"].macro_skill_id, call["arg"].sub_skill_id) == (h.macro, sub)
    assert call["id"] == f"isatvon-regen-{h.macro}"


def test_e8_4_t09_regenerate_frozen_is_423_building_is_409_over_long_hint_is_422(
    h: Harness,
) -> None:
    h.add_sub_skill(sample())
    problem(regenerate(h, h.macro, "macro-skills", {"hint": "x" * 2001}), 422, "VALIDATION_ERROR")
    h.set_workflow_state("FROZEN")
    problem(regenerate(h, h.macro, "macro-skills"), 423, "WORKFLOW_FROZEN")
    h.set_workflow_state("BUILDING")
    problem(regenerate(h, h.macro, "macro-skills"), 409, "WORKFLOW_BUILDING")
    assert h.temporal.calls == []


def test_e8_4_t09_a_certified_sub_skill_blocks_regenerate(h: Harness) -> None:
    h.add_sub_skill(sample(), state="CERTIFIED")
    problem(regenerate(h, h.macro, "macro-skills"), 409, "CONFLICT")
    assert h.temporal.calls == []


def test_e8_4_t09_regenerate_of_another_tenant_or_workflow_is_404(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(regenerate(h, h.macro, "macro-skills", token=other), 404, "NOT_FOUND")
    problem(regenerate(h, sub, "sub-skills", token=other), 404, "NOT_FOUND")
    problem(regenerate(h, uuid.uuid4(), "macro-skills"), 404, "NOT_FOUND")
    problem(regenerate(h, uuid.uuid4(), "sub-skills"), 404, "NOT_FOUND")
    assert h.temporal.calls == []


def test_e8_4_reorder_renumbers_the_siblings_contiguously(h: Harness) -> None:
    a = h.add_sub_skill(sample(), order=1)
    b = h.add_sub_skill(other_skill(2), order=2)
    c = h.add_sub_skill(other_skill(3), order=3)
    res = h.patch(c, {"order": 1})
    assert res.status_code == 200, res.text
    assert h.orders() == {c: 1, a: 2, b: 3}


def test_e8_4_t10_deleting_the_last_sub_skill_of_a_macro_is_409(h: Harness) -> None:
    only = h.add_sub_skill(sample())
    problem(h.call("DELETE", h.sub_path(only), h.fde()), 409, "MACRO_REQUIRES_SUB_SKILL")
    assert h.orders() == {only: 1}


def test_e8_4_delete_soft_deletes_and_renumbers(h: Harness) -> None:
    a = h.add_sub_skill(sample(), order=1)
    b = h.add_sub_skill(other_skill(2), order=2)
    c = h.add_sub_skill(other_skill(3), order=3)
    res = h.call("DELETE", h.sub_path(b), h.fde(), headers={"If-Match": h.etag_of(b)})
    assert res.status_code == 204
    assert h.orders() == {a: 1, c: 2}
    with h.sessions() as s:
        row = s.get(SubSkill, b)
        assert row is not None and row.deleted_at is not None
    problem(h.call("DELETE", h.sub_path(b), h.fde()), 404, "NOT_FOUND")


def test_e8_4_delete_with_a_stale_if_match_is_412(h: Harness) -> None:
    h.add_sub_skill(sample(), order=1)
    b = h.add_sub_skill(other_skill(2), order=2)
    res = h.call("DELETE", h.sub_path(b), h.fde(), headers={"If-Match": '"' + "0" * 64 + '"'})
    problem(res, 412, "PRECONDITION_FAILED")


def test_e8_4_history_lists_edits_oldest_first(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    h.patch(sub, {"changes": {BUDGET: B6}})
    h.patch(sub, {"changes": {BUDGET: B7}})
    res = h.call("GET", h.sub_path(sub, "/edits"), h.fde())
    assert res.status_code == 200, res.text
    edits = res.json()["edits"]
    assert [(e["old"], e["new"]) for e in edits] == [
        (B5, B6),
        (B6, B7),
    ]
    assert res.json()["sub_skill_id"] == str(sub)
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.call("GET", h.sub_path(sub, "/edits"), other), 404, "NOT_FOUND")


def test_e8_4_macro_patch_renames_and_uses_its_etag(h: Harness) -> None:
    path = f"/projects/{h.project}/workflows/{h.wf}/macro-skills/{h.macro}"
    problem(h.call("PATCH", path, h.fde(), json={"name": "X"}), 428, "PRECONDITION_REQUIRED")
    with h.sessions() as s:
        row = s.get(MacroSkill, h.macro)
        assert row is not None
        tag = f'"{row.row_version}"'
    res = h.call("PATCH", path, h.fde(), headers={"If-Match": tag}, json={"name": "Renamed"})
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Renamed"
    assert res.headers["ETag"] != tag
    problem(
        h.call("PATCH", path, h.fde(), headers={"If-Match": tag}, json={"name": "Again"}),
        412,
        "PRECONDITION_FAILED",
    )
    assert h.call("PATCH", path, h.fde(), headers={"If-Match": tag}, json={}).status_code == 422


def test_e8_4_an_edit_that_breaks_a_cross_field_rule_is_stored_invalid(h: Harness) -> None:
    sub = h.add_sub_skill(sample())
    res = h.patch(sub, {"changes": {IDEF + "v_variables.max_iterations": 5}})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "INVALID"
    assert [e["path"] for e in res.json()["validation_errors"]] == [IDEF + "i_instructions.bound"]


# --- A-29 (#455): a reorder or delete takes the Macro Skill's lock before the Sub-Skill's ---------


@pytest.fixture
def locks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Which rows `get_for_update` locked, in order (SQLite ignores FOR UPDATE, so the order is
    observed at the repository)."""
    taken: list[str] = []
    for name, repo in (("macro", MacroSkillRepository), ("sub", SubSkillRepository)):
        original = repo.get_for_update

        def wrapped(self: Any, id: uuid.UUID, _o: Any = original, _n: str = name) -> Any:
            taken.append(_n)
            return _o(self, id)

        monkeypatch.setattr(repo, "get_for_update", wrapped)
    return taken


def test_a29_t03_reorder_and_delete_lock_the_macro_before_the_sub(
    h: Harness, locks: list[str]
) -> None:
    first = h.add_sub_skill(sample(), order=1)
    second = h.add_sub_skill(other_skill(2), order=2)
    assert h.patch(second, {"order": 1}).status_code == 200
    assert locks == ["macro", "sub"]
    locks.clear()
    res = h.call("DELETE", h.sub_path(first), h.fde(), headers={"If-Match": h.etag_of(first)})
    assert res.status_code == 204, res.text
    assert locks == ["macro", "sub"]


def test_a29_t04_a_content_edit_takes_no_macro_lock(h: Harness, locks: list[str]) -> None:
    sub = h.add_sub_skill(sample())
    res = h.patch(sub, {"changes": {BUDGET: B6}})
    assert res.status_code == 200, res.text
    assert locks == ["sub"]


class _Orig(Exception):  # noqa: N818 - stands in for the driver's error
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])  # deadlock detected, serialization failure
def test_a29_t05_a_deadlock_or_serialization_failure_is_409_with_retry_after(
    h: Harness, monkeypatch: pytest.MonkeyPatch, sqlstate: str
) -> None:
    sub = h.add_sub_skill(sample())

    def lost(*_: Any, **__: Any) -> None:
        raise OperationalError("UPDATE sub_skills", {}, _Orig(sqlstate))

    monkeypatch.setattr(edits, "edit_sub_skill", lost)
    res = h.patch(sub, {"order": 1})
    body = problem(res, 409, "CONFLICT")
    assert body["title"] == "Edited at the same time"
    assert res.headers["Retry-After"] == "1"


def test_a29_t05_any_other_database_error_is_not_turned_into_a_409() -> None:
    with pytest.raises(OperationalError), edits.conflicts_are_409():
        raise OperationalError("SELECT 1", {}, _Orig("08006"))  # connection failure
    with pytest.raises(ApiError) as err, edits.conflicts_are_409():
        raise OperationalError("SELECT 1", {}, _Orig("40P01"))
    assert err.value.status == 409 and err.value.headers == {"Retry-After": "1"}
