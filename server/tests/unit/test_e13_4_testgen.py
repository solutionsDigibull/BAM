"""E13.4: the `generate_tests` stage against a fake Bifrost and a real (SQLite) database."""

import asyncio
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from llm_db import FakeBifrost, completion, make_client
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_contracts.build import BuildStageInput
from sbam_contracts.testgen import CaseBody, CaseKind, dataset_sha256
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.test.testgen import (
    GenerateTests,
    SqlDatasetStore,
    allowed_kinds,
    data_files,
    request_context,
)
from sqlalchemy import select
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
        self, replies: list[dict[str, Any]], isatvon: dict[str, Any] | None = None
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

        self.stage = GenerateTests(
            isatvon_of, self.client, SqlDatasetStore(self.sessions, self.storage)
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
    assert json.loads(context["tables"]) == ["investor_profiles", "auth_event_audit_handoff"]


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
