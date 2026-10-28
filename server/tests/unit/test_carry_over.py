"""E7.2 `carry_over` on SQLite with the real tables: after a scope-change approval, the previous
Workflow version's rows are copied into the new one as the stored impact says. The impact is made
by the preview's own function (`record_impact`), so these tests also pin "preview = what happens".

v1's Workflow: Macro MS01 (M-A) with A1 (F-A-1, CERTIFIED), A2 (F-A-2, CERTIFIED); Macro MS02 (M-B)
with B1 (F-B-1, VALID, never built), B2 (F-B-2, CERTIFIED).
"""

import copy
import uuid

import pytest
from sbam_contracts.pipeline import CarryOverInput
from sbam_workers.isatvon.carry_over import carry_over
from scope_change_db import DOC, TENANT, World, changed


@pytest.fixture
def w() -> World:
    return World()


def test_e7_2_t04_only_the_citing_sub_skill_is_rebuilt_the_others_reused(w: World) -> None:
    inp = w.scope_change(changed(**{"F-A-1": "a1 with refunds"}))
    ids = carry_over(w.sessions, inp)
    new = w.rows(w.w2)
    assert set(new) == {"MS01.1_A1", "MS01.2_A2", "MS02.3_B1", "MS02.4_B2"}
    assert sorted(r.id for r in new.values()) == sorted(ids)
    a1 = new["MS01.1_A1"]
    assert a1.state == "DRAFT" and a1.reused_from_id is None
    assert w.trail(a1.id) == ["INVALIDATED", "DRAFT"]  # invalidated, then to be re-extracted
    for name in ("MS01.2_A2", "MS02.4_B2"):  # untouched and certified: reused
        assert new[name].state == "CERTIFIED"
        assert new[name].reused_from_id == w.subs[name.split("_")[1]]
    b1 = new["MS02.3_B1"]  # untouched, never built: a draft, validated again
    assert b1.reused_from_id is None and b1.state in ("VALID", "INVALID")
    assert [m.macro_category for m in w.macro_rows(w.w2)] == ["MS01_A", "MS02_B"]
    assert {m.state for m in w.macro_rows(w.w2)} == {"PENDING"}


def test_e7_2_the_previous_version_is_never_changed(w: World) -> None:
    before = {k: (r.state, r.row_version) for k, r in w.rows(w.w1).items()}
    carry_over(w.sessions, w.scope_change(changed(**{"F-A-1": "a1 with refunds"})))
    assert {k: (r.state, r.row_version) for k, r in w.rows(w.w1).items()} == before
    assert {m.state for m in w.macro_rows(w.w1)} == {"CERTIFIED"}


def test_e7_2_a_removed_feature_drops_the_sub_skills_citing_only_it(w: World) -> None:
    doc = copy.deepcopy(DOC)
    doc["features"] = [f for f in doc["features"] if f["id"] != "F-B-2"]
    carry_over(w.sessions, w.scope_change(doc))
    assert "MS02.4_B2" not in w.rows(w.w2)
    assert len(w.rows(w.w2)) == 3


def test_e7_2_a_removed_module_drops_its_macro_and_sub_skills(w: World) -> None:
    doc = copy.deepcopy(DOC)
    doc["modules"] = [m for m in doc["modules"] if m["id"] != "M-B"]
    doc["features"] = [f for f in doc["features"] if f["module_id"] != "M-B"]
    carry_over(w.sessions, w.scope_change(doc))
    assert [m.macro_category for m in w.macro_rows(w.w2)] == ["MS01_A"]
    assert set(w.rows(w.w2)) == {"MS01.1_A1", "MS01.2_A2"}
    assert {r.state for r in w.rows(w.w2).values()} == {"CERTIFIED"}  # nothing else changed


def test_e7_2_a_change_outside_modules_and_features_reuses_everything(w: World) -> None:
    doc = copy.deepcopy(DOC) | {"assumptions_open_points": {"open_points": ["Refunds TBC"]}}
    carry_over(w.sessions, w.scope_change(doc))
    new = w.rows(w.w2)
    assert {k: r.state for k, r in new.items() if k != "MS02.3_B1"} == {
        "MS01.1_A1": "CERTIFIED",
        "MS01.2_A2": "CERTIFIED",
        "MS02.4_B2": "CERTIFIED",
    }


def test_e7_2_a_retry_returns_the_copies_already_made(w: World) -> None:
    inp = w.scope_change(changed(**{"F-A-1": "a1 with refunds"}))
    first = carry_over(w.sessions, inp)
    assert sorted(carry_over(w.sessions, inp)) == sorted(first)
    assert len(w.rows(w.w2)) == 4


def test_e7_2_a_first_approval_copies_nothing(w: World) -> None:
    inp = CarryOverInput(
        tenant_id=TENANT,
        project_id=w.project,
        run_id=uuid.uuid4(),
        correlation_id="cid-e7-2",
        workflow_id=w.w1,
        sow_version_id=w.v1,  # no impact stored for a first approval
    )
    assert carry_over(w.sessions, inp) == []
