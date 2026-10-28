"""E8.4 `regenerate_skills`: a Macro Skill's Sub-Skills (or one) written again with a hint, over a
Workflow in ISATVON_REVIEW with the pass-2 rows stored, a fake Bifrost and SQLite. Rows are updated
in place (id, skill_id and order stay), the old definition goes to `sub_skill_edits`."""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from knowledge_db import World
from llm_db import FakeBifrost, completion
from sbam_api.audit.models import AuditLog
from sbam_api.isatvon.models import SubSkill, SubSkillEdit
from sbam_contracts.isatvon_workbench import RegenerateInput
from sbam_workers.isatvon.regenerate import (
    NO_MATCH,
    NOT_EDITABLE,
    SUB_SKILL_IS_LOCKED,
    regenerate,
)
from sqlalchemy import select
from sub_skills_db import Pass2, answer, proposal
from temporalio.exceptions import ApplicationError

AUTH = proposal("Investor Auth Manager", "F-ACCESS-1")
KYC = proposal("Investor KYC Verifier", "F-ACCESS-2")
NEW_AUTH = proposal(
    "Investor Sign-in Gate",
    "F-ACCESS-1",
    **{"isatvon_definition.v_variables.budget_limit": "9000ms per pass"},
)
THIRD = proposal("Investor Audit Logger", "F-ACCESS-2")


@pytest.fixture
def p(tmp_path: Path) -> Iterator[Pass2]:
    pass2 = Pass2(World(tmp_path))
    SubSkillEdit.__table__.create(pass2.w.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
    pass2.run_macro(FakeBifrost([completion(answer(AUTH, KYC))]))
    with pass2.w.sessions() as s:
        row = pass2.workflow_row()
        row.state = "ISATVON_REVIEW"
        s.merge(row)
        s.commit()
    yield pass2


def regen_input(p: Pass2, sub: uuid.UUID | None = None, hint: str | None = "Be strict") -> Any:
    return RegenerateInput(
        **p.inp.model_dump(),
        workflow_id=p.workflow,
        macro_skill_id=p.macro_ids[0],
        sub_skill_id=sub,
        hint=hint,
        actor_id="fde-1",
    )


def run(p: Pass2, bifrost: FakeBifrost, inp: Any) -> list[uuid.UUID]:
    return asyncio.run(regenerate(p.deps(bifrost), inp, p.scope(1)))


def fails(p: Pass2, bifrost: FakeBifrost, inp: Any) -> ApplicationError:
    with pytest.raises(ApplicationError) as err:
        run(p, bifrost, inp)
    assert err.value.non_retryable
    return err.value


def history(p: Pass2, sub: uuid.UUID) -> list[SubSkillEdit]:
    with p.w.sessions() as s:
        return list(s.scalars(select(SubSkillEdit).where(SubSkillEdit.sub_skill_id == sub)))


def test_e8_4_t09_macro_regenerate_updates_rows_in_place_and_keeps_the_old_in_history(
    p: Pass2,
) -> None:
    before = p.sub_skills()
    bifrost = FakeBifrost([completion(answer(NEW_AUTH, KYC))])
    ids = run(p, bifrost, regen_input(p))
    after = p.sub_skills()
    assert ids == [r.id for r in before] == [r.id for r in after]
    assert [r.skill_id for r in after] == [r.skill_id for r in before]
    assert [r.order for r in after] == [1, 2]
    assert after[0].skill_name == "Investor Sign-in Gate"
    assert after[0].content_sha256 != before[0].content_sha256
    assert after[1].content_sha256 == before[1].content_sha256  # the same answer: untouched
    assert [r.state for r in after] == ["VALID", "VALID"]
    [edit] = history(p, after[0].id)
    assert edit.path == "isatvon_definition" and edit.edited_by == "fde-1"
    assert edit.old == before[0].isatvon["isatvon_definition"]
    assert history(p, after[1].id) == []


def test_e8_4_t09_the_hint_and_the_current_names_go_to_the_model_as_data_blocks(p: Pass2) -> None:
    bifrost = FakeBifrost([completion(answer(AUTH, KYC))])
    run(p, bifrost, regen_input(p, hint="Make it stricter"))
    sent = "".join(str(m["content"]) for m in bifrost.requests[0]["messages"])
    assert '<data name="hint">' in sent and "Make it stricter" in sent
    assert '<data name="current">' in sent and "Investor Auth Manager" in sent
    assert '<data name="focus">' not in sent
    assert bifrost.requests[0]["metadata"]["prompt_id"] == "isatvon-sub-skills"


def test_e8_4_t09_more_answers_add_rows_with_fresh_skill_ids(p: Pass2) -> None:
    ids = run(p, FakeBifrost([completion(answer(AUTH, KYC, THIRD))]), regen_input(p))
    rows = p.sub_skills()
    assert len(ids) == 3 and [r.id for r in rows] == ids
    assert [r.order for r in rows] == [1, 2, 3]
    assert len({r.skill_id for r in rows}) == 3
    assert rows[2].state == "VALID"


def test_e8_4_t09_fewer_answers_soft_delete_the_rest_and_free_their_order(p: Pass2) -> None:
    before = p.sub_skills()
    ids = run(
        p,
        FakeBifrost(
            [completion(answer(proposal("Investor Combined", "F-ACCESS-1", "F-ACCESS-2")))]
        ),
        regen_input(p),
    )
    assert ids == [before[0].id]
    with p.w.sessions() as s:
        gone = s.get(SubSkill, before[1].id)
        assert gone is not None and gone.deleted_at is not None and gone.order < 1
        trail = s.scalars(select(AuditLog).where(AuditLog.action == "ISATVON_DELETE")).all()
    assert len(trail) == 1


def test_e8_4_t09_one_sub_skill_is_replaced_by_position_and_keeps_its_ids(p: Pass2) -> None:
    before = p.sub_skills()
    run(
        p,
        FakeBifrost([completion(answer(AUTH, proposal("Investor Identity Check", "F-ACCESS-2")))]),
        regen_input(p, before[1].id),
    )
    after = p.sub_skills()
    assert after[0].content_sha256 == before[0].content_sha256
    assert (after[1].id, after[1].skill_id) == (before[1].id, before[1].skill_id)
    assert after[1].skill_name == "Investor Identity Check"
    assert after[1].isatvon["isatvon_definition"]["n_notification"]["next"] == (
        before[1].isatvon["isatvon_definition"]["n_notification"].get("next")
    )
    assert len(history(p, after[1].id)) == 1


def test_e8_4_t09_the_sub_skill_case_names_the_focus(p: Pass2) -> None:
    before = p.sub_skills()
    bifrost = FakeBifrost([completion(answer(AUTH, KYC))])
    run(p, bifrost, regen_input(p, before[1].id))
    sent = "".join(str(m["content"]) for m in bifrost.requests[0]["messages"])
    assert '<data name="focus">' in sent and before[1].skill_id in sent


def test_e8_4_t09_a_shorter_answer_cannot_replace_the_named_sub_skill(p: Pass2) -> None:
    before = p.sub_skills()
    bifrost = FakeBifrost(
        [completion(answer(proposal("Investor Combined", "F-ACCESS-1", "F-ACCESS-2")))]
    )
    assert fails(p, bifrost, regen_input(p, before[1].id)).type == NO_MATCH
    assert [r.content_sha256 for r in p.sub_skills()] == [r.content_sha256 for r in before]


def test_e8_4_t09_a_retry_after_the_commit_changes_nothing(p: Pass2) -> None:
    answers = [completion(answer(NEW_AUTH, KYC)), completion(answer(NEW_AUTH, KYC))]
    run(p, FakeBifrost(answers[:1]), regen_input(p))
    first = p.sub_skills()
    run(p, FakeBifrost(answers[1:]), regen_input(p))
    assert [r.content_sha256 for r in p.sub_skills()] == [r.content_sha256 for r in first]
    assert len(history(p, first[0].id)) == 1


def test_e8_4_t09_a_workflow_that_is_not_editable_is_refused_without_a_model_call(p: Pass2) -> None:
    with p.w.sessions() as s:
        row = p.workflow_row()
        row.state = "BUILDING"
        s.merge(row)
        s.commit()
    bifrost = FakeBifrost([completion(answer(AUTH, KYC))])
    assert fails(p, bifrost, regen_input(p)).type == NOT_EDITABLE
    assert bifrost.requests == []


def test_e8_4_t09_a_certified_sub_skill_is_refused_without_a_model_call(p: Pass2) -> None:
    with p.w.sessions() as s:
        row = s.get(SubSkill, p.sub_skills()[0].id)
        assert row is not None
        row.state = "CERTIFIED"
        s.commit()
    bifrost = FakeBifrost([completion(answer(AUTH, KYC))])
    assert fails(p, bifrost, regen_input(p)).type == SUB_SKILL_IS_LOCKED
    assert bifrost.requests == []
