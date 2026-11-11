"""E13.4: the `generate_tests` stage against a fake Bifrost and a real (SQLite) database."""

import asyncio
import json
import tempfile
import time
import types
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from llm_db import FakeBifrost, completion, make_client
from openpyxl.writer import excel as excel_writer
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_contracts.build import BuildStageInput
from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.testgen import CaseBody, CaseKind, TestgenOutput, dataset_sha256
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.test.testgen import (
    INPUT_HINT,
    TEST_DATA_STALE,
    GenerateTests,
    SqlDatasetStore,
    allowed_kinds,
    check_output,
    data_files,
    fingerprint,
    request_context,
)
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_codegen_assemble import stage_input

IDEF = "isatvon_definition."


def sample(**dotted: Any) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8")
    )
    for path, value in dotted.items():
        *parents, leaf = path.split(".")
        node = raw
        for part in parents:
            node = node[part]
        node[leaf] = value
    return raw


INPUT = {"mobile_number": "9999900000", "otp_payload": "123456", "device_fingerprint": "fp-1"}


def case(name: str, kind: str, terminal: str, **over: Any) -> dict[str, Any]:
    return {"name": name, "kind": kind, "input": INPUT, "expected_terminal": terminal, **over}


GOOD = {
    "cases": [
        case("happy", "PASS_FIRST", "SUCCESS"),
        case("never passes", "ESCALATION", "ESCALATED"),
        case("retry", "PASS_AFTER_RETRY", "SUCCESS"),
    ]
}


def reply(body: dict[str, Any]) -> dict[str, Any]:
    return completion(json.dumps(body), model="sbam-testgen-primary")


class World:
    def __init__(
        self,
        replies: list[dict[str, Any]],
        isatvon: dict[str, Any] | None = None,
        schema: str | None = None,
    ) -> None:
        self.bifrost = FakeBifrost([reply(r) for r in replies])
        self.client, self.sessions = make_client(self.bifrost)
        bind = self.sessions.kw["bind"]
        for table in (DatasetRow, CaseRow):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        # a short root: storage keys are long and Windows stops paths at 260 characters
        self.storage = LocalDiskBackend(Path(tempfile.mkdtemp(prefix="sbam-")))
        self.isatvon = isatvon or sample()
        self.inp: BuildStageInput = stage_input()

        async def isatvon_of(_: BuildStageInput) -> dict[str, Any]:
            return self.isatvon

        self.schema = schema

        async def schema_of(_: BuildStageInput) -> str:
            return self.schema or ""

        self.stage = GenerateTests(
            isatvon_of,
            self.client,
            SqlDatasetStore(self.sessions, self.storage),
            schema_of,
        )

    def run(self) -> uuid.UUID:
        return asyncio.run(ActivityEnvironment().run(self.stage.run, self.inp)).id

    def datasets(self) -> list[DatasetRow]:
        with self.sessions() as s:
            rows = list(s.scalars(select(DatasetRow).order_by(DatasetRow.version)))
            s.expunge_all()
            return rows

    def cases(self) -> list[CaseRow]:
        with self.sessions() as s:
            rows = list(s.scalars(select(CaseRow)))
            s.expunge_all()
            return rows


def test_e13_4_t01_generates_a_synthetic_dataset_with_the_three_scenarios() -> None:
    w = World([GOOD])
    dataset_id = w.run()
    (dataset,) = w.datasets()
    assert dataset.id == dataset_id and dataset.version == 1
    assert dataset.synthetic is True and dataset.edited_by_fde is False
    cases = w.cases()
    assert {c.kind for c in cases} == {"PASS_FIRST", "ESCALATION", "PASS_AFTER_RETRY"}
    pairs = [
        (
            CaseKind(c.kind),
            CaseBody.model_validate(
                {
                    **{
                        k: getattr(c, k)
                        for k in (
                            "name",
                            "input",
                            "initial_state",
                            "state_script",
                            "expected_output",
                            "assertions",
                        )
                    },
                    "expected_terminal": c.expected_terminal,
                }
            ),
        )
        for c in cases
    ]
    assert dataset.sha256 == dataset_sha256(pairs, {})  # recomputed over the stored cases


def test_e13_4_a_retry_finds_the_dataset_instead_of_writing_a_second() -> None:
    w = World([GOOD])
    first = w.run()
    assert w.run() == first
    assert len(w.datasets()) == 1 and len(w.cases()) == 3
    assert len(w.bifrost.requests) == 1  # the second run used the replay cache or the dataset


def test_e13_4_t02_a_case_that_violates_the_input_schema_is_re_asked_and_never_stored() -> None:
    bad = {
        "cases": [*GOOD["cases"][:2], case("bad", "EDGE", "SUCCESS", input={"mobile_number": "1"})]
    }
    w = World([bad, GOOD])
    w.run()
    assert len(w.bifrost.requests) == 2
    asked = json.dumps(w.bifrost.requests[1])
    assert "/input/otp_payload" in asked  # the path, not the value
    assert "bad" not in {c.name for c in w.cases()}
    assert len(w.cases()) == 3


def test_e13_4_t03_fewer_than_three_cases_on_every_attempt_stores_nothing() -> None:
    two = {"cases": GOOD["cases"][:2]}
    w = World([two])
    with pytest.raises(SchemaInvalidAfterRetries):
        w.run()
    assert w.datasets() == [] and w.cases() == []


def test_e13_4_t04_one_iteration_never_gets_a_pass_after_retry_case() -> None:
    isatvon = sample(**{IDEF + "v_variables.max_iterations": 1})
    assert CaseKind.PASS_AFTER_RETRY not in allowed_kinds(isatvon)
    assert "PASS_AFTER_RETRY" not in json.loads(request_context(isatvon)["limits"])["allowed_kinds"]
    noop = {"cases": [*GOOD["cases"][:2], case("nothing to do", "NO_OP", "NO_OP")]}
    w = World([GOOD, noop], isatvon)
    w.run()
    assert len(w.bifrost.requests) == 2  # the first answer was refused for its kind
    assert {c.kind for c in w.cases()} == {"PASS_FIRST", "ESCALATION", "NO_OP"}


def test_e13_4_t05_destructive_seed_sql_is_re_asked_and_never_stored() -> None:
    evil = case("evil", "EDGE", "SUCCESS", initial_state=["DROP TABLE audit; --"])
    w = World([{"cases": [*GOOD["cases"], evil]}, GOOD])
    w.run()
    assert len(w.bifrost.requests) == 2 and "/initial_state/0" in json.dumps(w.bifrost.requests[1])
    assert "evil" not in {c.name for c in w.cases()}
    assert all("DROP" not in json.dumps(c.initial_state) for c in w.cases())


def test_e13_4_t09_the_same_answer_gives_the_same_hash() -> None:
    first, second = World([GOOD]), World([GOOD])
    first.run()
    second.run()
    assert first.datasets()[0].sha256 == second.datasets()[0].sha256


def test_e13_4_the_model_cannot_set_a_platform_field() -> None:
    forged = {"cases": [{**c, "synthetic": False} for c in GOOD["cases"]]}
    w = World([forged])
    with pytest.raises(SchemaInvalidAfterRetries):
        w.run()
    assert w.datasets() == []
    fde = {"cases": [{**GOOD["cases"][0], "kind": "FDE"}, *GOOD["cases"][1:]]}
    with pytest.raises(SchemaInvalidAfterRetries):
        World([fde]).run()


def test_e13_4_a_dataset_the_fde_edited_is_kept_on_a_rebuild() -> None:
    w = World([GOOD])
    first = w.run()
    with w.sessions() as s:  # as the API does after an FDE edit
        row = s.get(DatasetRow, first)
        assert row is not None
        row.edited_by_fde = True
        s.commit()
    assert w.run() == first
    assert len(w.bifrost.requests) == 1 and len(w.datasets()) == 1


def test_e13_4_the_context_holds_only_this_sub_skills_isatvon() -> None:
    context = request_context(sample())
    assert set(context) == {"sub_skill", "input_schema", "tables", "limits"}
    assert json.loads(context["tables"]) == ["investor_profiles"]  # never the handoff (#738)


@pytest.mark.parametrize("extension", ["csv", "json", "xlsx"])
def test_e13_4_one_deterministic_file_per_file_reference(extension: str) -> None:
    isatvon = sample()
    isatvon["isatvon_definition"]["s_source"]["external_data"] = [
        {"name": "rates", "kind": "FILE", "ref": f"rates.{extension}"},
        {"name": "feed", "kind": "API", "ref": "https://example.test/feed"},
        {"name": "legacy", "kind": "FILE", "ref": "legacy.xls"},
    ]
    (one,) = data_files(isatvon)
    assert one.name == f"rates.{extension}" and one.content
    assert data_files(isatvon)[0].content == one.content  # the same bytes every run


def test_e13_4_t09_an_xlsx_has_the_same_bytes_whatever_the_time_it_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """openpyxl stamps `modified` and every zip entry with the clock (#642): move the clock
    between two builds instead of relying on timing."""
    isatvon = sample()
    isatvon["isatvon_definition"]["s_source"]["external_data"] = [
        {"name": "rates", "kind": "FILE", "ref": "rates.xlsx"}
    ]
    now = [datetime(2026, 11, 3, 9, 0, 0, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "Clock":
            return cls.fromtimestamp(now[0].timestamp(), tz or UTC)

    monkeypatch.setattr(
        excel_writer, "datetime", types.SimpleNamespace(datetime=Clock, timezone=timezone)
    )
    monkeypatch.setattr(time, "time", lambda: now[0].timestamp())
    first = data_files(isatvon)[0].content
    now[0] += timedelta(seconds=7, days=3)
    assert data_files(isatvon)[0].content == first


def test_e13_4_the_dataset_hash_covers_the_files(tmp_path: Path) -> None:
    isatvon = sample()
    isatvon["isatvon_definition"]["s_source"]["external_data"] = [
        {"name": "rates", "kind": "FILE", "ref": "rates.csv"}
    ]
    w = World([GOOD], isatvon)
    w.run()
    (dataset,) = w.datasets()
    assert len(dataset.files_keys) == 1 and dataset.files_keys[0].rsplit("/", 2)[1] == "tests"
    plain = World([GOOD])
    plain.run()
    assert dataset.sha256 != plain.datasets()[0].sha256  # same cases, a file more: another hash


def after_a_correction(w: World, isatvon: dict[str, Any]) -> None:
    """The build's next attempt: a new stage key, the Sub-Skill as the corrector left it."""
    w.isatvon = isatvon
    w.inp = w.inp.model_copy(update={"attempt": w.inp.attempt + 1})


def mark_edited(w: World, dataset_id: uuid.UUID, generated_for: str | None = "keep") -> None:
    with w.sessions() as s:
        row = s.get(DatasetRow, dataset_id)
        assert row is not None
        row.edited_by_fde = True
        if generated_for != "keep":
            row.generated_for = generated_for
        s.commit()


def test_e11_4b_the_dataset_records_what_it_was_generated_for() -> None:
    w = World([GOOD])
    w.run()
    assert w.datasets()[0].generated_for == fingerprint(w.isatvon)


def test_e11_4b_a_correction_that_changes_max_iterations_regenerates_the_tests() -> None:
    w = World([GOOD, {"cases": [*GOOD["cases"][:2], case("nothing", "NO_OP", "NO_OP")]}])
    first = w.run()
    after_a_correction(w, sample(**{IDEF + "v_variables.max_iterations": 1}))
    second = w.run()
    one, two = w.datasets()
    assert second != first and (one.version, two.version) == (1, 2)
    assert one.sha256 != two.sha256 and one.generated_for != two.generated_for
    newest = {c.kind for c in w.cases() if c.dataset_id == second}
    assert "PASS_AFTER_RETRY" not in newest and len(w.bifrost.requests) == 2


def test_e11_4b_a_correction_that_leaves_the_bounds_alone_leaves_the_tests_alone() -> None:
    w = World([GOOD])
    first = w.run()
    patched = sample()
    patched["isatvon_definition"]["t_tech_stack"]["fixed_check_tool"] = "other_check_tool"
    after_a_correction(w, patched)
    assert fingerprint(patched) == fingerprint(sample())
    assert w.run() == first
    assert len(w.bifrost.requests) == 1 and len(w.datasets()) == 1


def test_e11_4b_edited_tests_that_no_longer_fit_the_new_bound_wait_for_the_fde() -> None:
    w = World([GOOD])
    first = w.run()
    mark_edited(w, first)
    after_a_correction(w, sample(**{IDEF + "v_variables.max_iterations": 1}))
    with pytest.raises(ApplicationError) as caught:
        w.run()
    assert caught.value.type == TEST_DATA_STALE and caught.value.non_retryable
    assert len(w.datasets()) == 1 and len(w.bifrost.requests) == 1  # nothing overwritten


def test_e11_4b_edited_tests_from_before_the_fingerprint_are_kept() -> None:
    w = World([GOOD])
    first = w.run()
    mark_edited(w, first, generated_for=None)
    after_a_correction(w, sample(**{IDEF + "v_variables.max_iterations": 1}))
    assert w.run() == first and len(w.datasets()) == 1


def test_e13_6_an_assertion_type_the_suite_does_not_know_is_re_asked_and_never_stored() -> None:
    typo = case("typo", "EDGE", "SUCCESS", assertions=[{"type": "looks_fine"}])
    w = World([{"cases": [*GOOD["cases"], typo]}, GOOD])
    w.run()
    assert len(w.bifrost.requests) == 2 and "/assertions/0/type" in json.dumps(
        w.bifrost.requests[1]
    )
    assert "typo" not in {c.name for c in w.cases()}


# --- #702: the re-ask names the input rule a case broke, never its value -----------------------


def answer(*cases: dict[str, Any]) -> TestgenOutput:
    return TestgenOutput.model_validate({"cases": [*GOOD["cases"], *cases]})


def test_702_t01_a_null_required_input_names_the_rule_not_the_value() -> None:
    nulled = {**INPUT, "otp_payload": None}
    lines = check_output(sample(), answer(case("no otp", "NO_OP", "NO_OP", input=nulled)))
    assert lines[0] == "case 3: /input/otp_payload: required string; never null or missing"
    missing = {k: v for k, v in INPUT.items() if k != "otp_payload"}
    lines = check_output(sample(), answer(case("gone", "EDGE", "SUCCESS", input=missing)))
    assert lines[0] == "case 3: /input/otp_payload: required string; never null or missing"
    assert not any("None" in line or "123456" in line for line in lines)


def test_702_t02_an_undeclared_input_lists_the_declared_ones() -> None:
    extra = {**INPUT, "secret_flag": "x"}
    [line, _] = check_output(sample(), answer(case("extra", "EDGE", "SUCCESS", input=extra)))
    assert line == (
        "case 3: /input/secret_flag: not an input of this Sub-Skill;"
        " inputs: device_fingerprint, mobile_number, otp_payload"
    )


def test_702_t03_the_edge_hint_comes_once_and_only_with_an_input_error() -> None:
    nulled = {**INPUT, "otp_payload": None, "mobile_number": None}
    lines = check_output(sample(), answer(case("a", "NO_OP", "NO_OP", input=nulled)))
    assert lines.count(INPUT_HINT) == 1 and lines[-1] == INPUT_HINT and len(lines) == 3
    assert check_output(sample(), answer()) == ()
    drop = check_output(
        sample(), answer(case("b", "EDGE", "SUCCESS", initial_state=["DROP TABLE x"]))
    )
    assert drop == ("case 3: /initial_state/0",)  # other lines are unchanged, no hint


DDL = (
    "CREATE TABLE auth_event_audit_handoff (id bigserial PRIMARY KEY, mobile_number text);"
    "CREATE TABLE investor_profiles (id bigserial PRIMARY KEY, investor_id text);"
)


def test_the_schema_is_in_the_request_only_when_given_and_never_in_the_fingerprint() -> None:
    from sbam_workers.test.testgen import fingerprint

    assert "workflow_schema_sql" not in request_context(sample())
    assert request_context(sample(), DDL)["workflow_schema_sql"] == DDL
    # the fingerprint decides whether the cases are kept: it must not move with the schema text
    assert fingerprint(sample()) == fingerprint(sample())


def test_a_seed_naming_a_column_the_schema_lacks_is_sent_back_by_case_and_statement() -> None:
    seed = "INSERT INTO investor_profiles (investor_id, kit_status) VALUES ('1', 'k')"
    out = answer(case("bad seed", "EDGE", "SUCCESS", initial_state=[seed]))
    assert check_output(sample(), out) == ()  # without the schema nothing can be said
    [line] = check_output(sample(), out, DDL)
    assert line.startswith("case 3: statement 1:") and "kit_status" in line
    assert "'k'" not in line  # identifiers only


def test_a_state_script_longer_than_the_passes_after_the_first_is_sent_back() -> None:
    """Three passes means two steps: step i runs before pass i + 2. A third ended the whole sandbox
    run as DATASET_INVALID after the VM had booted."""
    limit = SubSkillModel.model_validate(sample()).isatvon_definition.v_variables.max_iterations
    step = ["UPDATE investor_profiles SET investor_id = 'x' WHERE id = 1"]
    steps = [step] * limit  # one too many
    [line] = check_output(sample(), answer(case("too long", "EDGE", "SUCCESS", state_script=steps)))
    assert line == (
        f"case 3: /state_script has {limit} steps, at most {limit - 1} "
        f"(max_iterations {limit} minus the first pass)"
    )
    ok = steps[: limit - 1]
    assert check_output(sample(), answer(case("fits", "EDGE", "SUCCESS", state_script=ok))) == ()


def test_a_seed_that_writes_the_handoff_table_is_sent_back() -> None:
    """The runner counts handoff rows and wants exactly one new row per pass: a seeded row made
    every case fail with 'pass 1 left 2 handoff rows' after a sandbox had been booted."""
    seed = "INSERT INTO auth_event_audit_handoff (mobile_number) VALUES ('1')"
    out = answer(case("seeds the log", "EDGE", "SUCCESS", initial_state=[seed]))
    [line] = [x for x in check_output(sample(), out) if "do not seed" in x]
    assert line.startswith("case 3: statement 1: do not seed auth_event_audit_handoff")
    assert "'1'" not in line  # no value echoed


def test_a_case_whose_input_contradicts_no_op_condition_is_sent_back() -> None:
    """An ESCALATION case whose input already satisfies no_op_condition ends NO_OP before any tool
    runs, so it can never escalate; the run only found out after booting a sandbox."""
    base = sample()
    base["isatvon_definition"]["v_variables"]["no_op_condition"] = "mobile_number == 'BLOCKED'"
    blocked = {**INPUT, "mobile_number": "BLOCKED"}
    lines = check_output(base, answer(case("bad", "ESCALATION", "ESCALATED", input=blocked)))
    assert any("already satisfies no_op_condition" in x for x in lines)
    lines = check_output(base, answer(case("not no-op", "NO_OP", "NO_OP", input=INPUT)))
    assert any("is not a NO_OP case" in x for x in lines)
    ok = answer(
        case("fine", "ESCALATION", "ESCALATED"), case("noop", "NO_OP", "NO_OP", input=blocked)
    )
    assert not [x for x in check_output(base, ok) if "no_op_condition" in x]
