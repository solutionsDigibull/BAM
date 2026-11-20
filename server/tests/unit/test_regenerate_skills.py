"""E8.4 `regenerate_skills`: a Macro Skill's Sub-Skills (or one) written again with a hint, over a
Workflow in ISATVON_REVIEW with the pass-2 rows stored, a fake Bifrost and SQLite. Rows are updated
in place (id, skill_id and order stay), the old definition goes to `sub_skill_edits`."""

import asyncio
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from knowledge_db import World
from llm_db import FakeBifrost, completion
from sbam_api.audit.models import AuditLog
from sbam_api.isatvon.models import SubSkill, SubSkillEdit
from sbam_api.llm.models import LlmCall
from sbam_contracts.isatvon_macros import SubSkillExtraction
from sbam_contracts.isatvon_workbench import RegenerateInput
from sbam_workers.isatvon.regenerate import (
    NO_MATCH,
    NOT_EDITABLE,
    SUB_SKILL_IS_LOCKED,
    _blocks,
    regenerate,
    regenerate_scope,
)
from sbam_workers.llm.client import DERIVATIVE_MODEL, cache_key, compose
from sbam_workers.llm.registry import PromptRegistry
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


# -- #930: a regenerate with a new hint asks the model; the same request is a cache hit -----------


def _scope_of(p: Pass2, run_id: str) -> Any:
    """The scope the activity builds: the same Temporal workflow id and activity id every time."""
    return regenerate_scope(regen_input(p), "isatvon-regen-fixed", run_id, "1", 1)


def _run_as(p: Pass2, bifrost: FakeBifrost, inp: Any, run_id: str) -> list[uuid.UUID]:
    return asyncio.run(regenerate(p.deps(bifrost), inp, _scope_of(p, run_id)))


def _llm_rows(p: Pass2) -> list[Any]:
    with p.w.sessions() as s:
        return list(s.scalars(select(LlmCall).order_by(LlmCall.id)))


def _sent_blocks(p: Pass2, inp: Any, current: Any, feedback: Any) -> str:
    prompt = PromptRegistry().get("isatvon-sub-skills")
    messages, _ = compose(prompt, SubSkillExtraction, _blocks(inp, current, feedback))
    return cache_key("sbam-isatvon", messages)


def test_wg42_t01_two_hints_give_two_cache_keys_and_the_same_hint_the_same_key(p: Pass2) -> None:
    current = [{"id": "1", "skill_id": "MS1", "name": "A"}]
    one = _sent_blocks(p, regen_input(p, hint="Be strict"), current, {})
    other = _sent_blocks(p, regen_input(p, hint="Allow retries"), current, {})
    again = _sent_blocks(p, regen_input(p, hint="Be strict"), current, {})
    assert one != other and one == again


def test_wg42_t02_the_validator_feedback_is_part_of_the_key(p: Pass2) -> None:
    current = [{"id": "1", "skill_id": "MS1", "name": "A"}]
    inp = regen_input(p)
    plain = _sent_blocks(p, inp, current, {})
    flagged = _sent_blocks(p, inp, current, {"MS1": [{"code": "X", "path": "a.b"}]})
    other = _sent_blocks(p, inp, current, {"MS1": [{"code": "Y", "path": "a.b"}]})
    assert len({plain, flagged, other}) == 3
    assert "feedback" not in _blocks(inp, current, {}) and "hint" in _blocks(inp, current, {})


def test_wg42_t03_a_new_hint_in_a_new_run_calls_the_model_and_records_it(p: Pass2) -> None:
    first = FakeBifrost([completion(answer(AUTH, KYC))])
    _run_as(p, first, regen_input(p, hint="Be strict"), "run-1")
    second = FakeBifrost([completion(answer(NEW_AUTH, KYC))])
    _run_as(p, second, regen_input(p, hint="Rename the first one"), "run-2")
    assert len(first.requests) == 1 and len(second.requests) == 1  # not the cached answer
    assert p.sub_skills()[0].skill_name == "Investor Sign-in Gate"  # the new hint took effect
    real = [r for r in _llm_rows(p) if r.model != DERIVATIVE_MODEL and r.output is not None]
    assert len(real) == 3  # pass 2 of the fixture, then both regenerations
    assert all(r.tokens_in > 0 for r in real)  # what the budget counts


def test_wg42_t04_the_same_hint_again_is_served_from_the_cache(p: Pass2) -> None:
    # the answer changes nothing, so the second run asks the same question (same names, hint)
    _run_as(p, FakeBifrost([completion(answer(AUTH, KYC))]), regen_input(p), "run-1")
    again = FakeBifrost([completion(answer(AUTH, KYC))])
    _run_as(p, again, regen_input(p), "run-2")
    assert again.requests == []  # no model call
    assert any(r.model == DERIVATIVE_MODEL for r in _llm_rows(p))  # recorded as a cache hit


def test_wg42_t05_an_activity_retry_in_the_same_run_still_replays(p: Pass2) -> None:
    inp = regen_input(p)
    _run_as(p, FakeBifrost([completion(answer(NEW_AUTH, KYC))]), inp, "run-1")
    retry = FakeBifrost([completion(answer(AUTH, KYC))])
    _run_as(p, retry, inp, "run-1")
    assert retry.requests == []
    scopes = {_scope_of(p, "run-1"), _scope_of(p, "run-1")}
    assert len(scopes) == 1 and _scope_of(p, "run-2") not in scopes


def test_wg42_t06_without_a_hint_or_findings_the_blocks_are_what_they_always_were(
    p: Pass2,
) -> None:
    current = [{"id": "1", "skill_id": "MS1", "name": "A"}]
    assert _blocks(regen_input(p, hint=None), current, {}) == {
        "current": json.dumps(current, sort_keys=True)
    }
