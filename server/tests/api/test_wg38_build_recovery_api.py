"""WG-38 (#925): a build is refused up front when the skill-runtime registry is missing, and a
Sub-Skill in BUILD_ERROR is recovered by POST W/rebuild once the cause is fixed. Same harness as
the rebuild tests (fake Temporal, real app and auth chain)."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.registry.models import RuntimeRegistryRow
from sbam_contracts.state_machines import Actor, InvalidTransition, can_transition
from sqlalchemy import delete, select, update
from test_auth import ADMIN_A, FDE_A1, FDE_A2, FDE_B1, TENANT_B, problem
from test_isatvon_export_api import sample
from test_isatvon_generate_api import GenerateHarness
from test_isatvon_rebuild_api import RebuildHarness


@pytest.fixture
def h(tmp_path: Path) -> Iterator[RebuildHarness]:
    yield RebuildHarness(tmp_path)


@pytest.fixture
def g(tmp_path: Path) -> Iterator[GenerateHarness]:
    yield GenerateHarness(tmp_path)


def drop_registry(harness: GenerateHarness) -> None:
    with harness.sessions() as s:
        s.execute(update(RuntimeRegistryRow).values(is_active=False))
        s.commit()


def recovered(harness: RebuildHarness) -> list[AuditLog]:
    with harness.sessions() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == "recover")))


def test_wg38_t01_generate_without_a_runtime_registry_is_refused_with_the_fix(
    g: GenerateHarness,
) -> None:
    g.sub(g.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    drop_registry(g)
    res = g.generate(key="generate-key-1")
    problem(res, 409, "CONFLICT")
    body = res.json()
    assert "registry" in body["title"].lower()
    assert "make runtime-registry" in body["detail"] and "Nothing was started" in body["detail"]
    assert g.pipeline.signals == [] and g.requested() == [] and g.entered_building() == []
    assert g.states() == {"MS01.1_A": "VALID"}


def test_wg38_t01_rebuild_without_a_runtime_registry_is_refused_too(h: RebuildHarness) -> None:
    sub = h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"), state="BUILD_ERROR")
    drop_registry(h)
    res = h.post_rebuild({"sub_skill_ids": [str(sub.id)]}, key="rebuild-key-1")
    problem(res, 409, "CONFLICT")
    assert "make runtime-registry" in res.json()["detail"]
    assert h.pipeline.signals == [] and h.retried() == [] and recovered(h) == []
    assert h.states() == {"MS01.1_A": "BUILD_ERROR"}  # still recoverable once the registry is in


def test_wg38_t02_build_error_is_recovered_after_the_registry_is_loaded(h: RebuildHarness) -> None:
    macro = h.macro(1, "MS01_A")
    a = h.sub(macro, 1, sample("MS01.1_A", "A"), state="BUILD_ERROR")
    h.sub(macro, 2, sample("MS01.2_B", "B"), state="CERTIFIED")
    res = h.post_rebuild({"sub_skill_ids": [str(a.id)]}, key="rebuild-key-1")
    assert res.status_code == 202, res.text
    [(name, payload)] = h.pipeline.signals
    assert name == "rebuild_subskills" and payload.sub_skill_ids == [a.id]
    assert h.states() == {"MS01.1_A": "BUILDING", "MS01.2_B": "CERTIFIED"}


def test_wg38_t03_the_recovery_is_audited_with_the_actor_and_state_change(
    h: RebuildHarness,
) -> None:
    a = h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"), state="BUILD_ERROR")
    assert h.post_rebuild({"sub_skill_ids": [str(a.id)]}).status_code == 202
    [row] = recovered(h)
    assert row.entity_type == "sub_skill" and row.entity_id == str(a.id)
    assert row.actor_id == str(FDE_A1)
    assert (row.before or {}).get("state") == "BUILD_ERROR"
    assert (row.after or {}).get("state") == "BUILDING"


def test_wg38_t04_a_repeated_key_recovers_and_signals_once(h: RebuildHarness) -> None:
    a = h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"), state="BUILD_ERROR")
    first = h.post_rebuild({"sub_skill_ids": [str(a.id)]}, key="rebuild-same")
    again = h.post_rebuild({"sub_skill_ids": [str(a.id)]}, key="rebuild-same")
    assert first.status_code == again.status_code == 202
    assert len(h.pipeline.signals) == 1 and len(recovered(h)) == 1 and len(h.rebuilds()) == 1


def test_wg38_t05_no_ids_includes_build_error_with_the_others(h: RebuildHarness) -> None:
    macro = h.macro(1, "MS01_A")
    a = h.sub(macro, 1, sample("MS01.1_A", "A"), state="BUILD_ERROR")
    b = h.sub(macro, 2, sample("MS01.2_B", "B"), state="NEEDS_FDE_REVIEW")
    assert h.post_rebuild().status_code == 202
    [(_, payload)] = h.pipeline.signals
    assert set(payload.sub_skill_ids) == {a.id, b.id}
    assert h.states() == {"MS01.1_A": "BUILDING", "MS01.2_B": "BUILDING"}
    assert len(recovered(h)) == 1  # BUILD_ERROR takes `recover`, review takes `retry`


def test_wg38_t06_cross_tenant_unassigned_and_wrong_portal(h: RebuildHarness) -> None:
    a = h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"), state="BUILD_ERROR")
    body = {"sub_skill_ids": [str(a.id)]}
    problem(h.post_rebuild(body, token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")  # unassigned
    other = h.realm.token(FDE_B1, tenant_id=[str(TENANT_B)])
    problem(h.post_rebuild(body, token=other), 404, "NOT_FOUND")  # another tenant
    problem(h.post_rebuild(body, token=h.realm.token(ADMIN_A)), 403, "WRONG_PORTAL")
    problem(h.post_rebuild({"sub_skill_ids": [str(uuid.uuid4())]}), 404, "NOT_FOUND")
    assert h.pipeline.signals == [] and recovered(h) == []
    assert h.states() == {"MS01.1_A": "BUILD_ERROR"}


def test_wg38_t07_recover_is_an_fde_event_and_the_platform_retry_is_unchanged() -> None:
    assert can_transition("sub_skill", "BUILD_ERROR", "recover", Actor.FDE) == "BUILDING"
    assert can_transition("sub_skill", "BUILD_ERROR", "retry", Actor.ADMIN) == "BUILDING"
    for actor in (Actor.ADMIN, Actor.SYSTEM):
        with pytest.raises(InvalidTransition):
            can_transition("sub_skill", "BUILD_ERROR", "recover", actor)
    with pytest.raises(InvalidTransition):
        can_transition("sub_skill", "BUILD_ERROR", "retry", Actor.FDE)
    with pytest.raises(InvalidTransition):  # certified work stays locked
        can_transition("sub_skill", "CERTIFIED", "recover", Actor.FDE)


def test_wg38_t08_a_frozen_workflow_still_refuses(h: RebuildHarness) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"), state="BUILD_ERROR")
    h.set_workflow("FROZEN")
    problem(h.post_rebuild(), 423, "WORKFLOW_FROZEN")
    assert h.pipeline.signals == [] and recovered(h) == []


def test_wg38_t09_no_registry_row_at_all_is_also_refused(g: GenerateHarness) -> None:
    g.sub(g.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    with g.sessions() as s:
        s.execute(delete(RuntimeRegistryRow))
        s.commit()
    problem(g.generate(), 409, "CONFLICT")
