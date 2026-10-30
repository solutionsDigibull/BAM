"""E13.6: the `build_promptfoo_suite` stage against a real (SQLite) database."""

import asyncio
import json
import uuid
from typing import Any

import pytest
from fixtures import fixture_path
from llm_db import llm_sessions
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_contracts.build import BuildStageInput
from sbam_workers.build.gates.errors import SUITE_INVALID
from sbam_workers.test.promptfoo_suite import BuildPromptfooSuite
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_codegen_assemble import stage_input

INPUT = {"mobile_number": "9999900000", "otp_payload": "123456", "device_fingerprint": "fp-1"}


def isatvon() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8")
    )
    return raw


class World:
    def __init__(self) -> None:
        self.sessions: sessionmaker[Session] = llm_sessions()
        bind = self.sessions.kw["bind"]
        for table in (DatasetRow, CaseRow):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.inp: BuildStageInput = stage_input()

        async def isatvon_of(_: BuildStageInput) -> dict[str, Any]:
            return isatvon()

        self.stage = BuildPromptfooSuite(isatvon_of, self.sessions)

    def dataset(
        self, version: int = 1, assertions: list[dict[str, Any]] | None = None
    ) -> uuid.UUID:
        with self.sessions() as s:
            row = DatasetRow(
                tenant_id=self.inp.tenant_id,
                project_id=self.inp.project_id,
                sub_skill_id=self.inp.sub_skill_id,
                version=version,
                files_keys=[],
                sha256="a" * 64,
                synthetic=True,
                edited_by_fde=False,
            )
            s.add(row)
            s.flush()
            for name, kind, terminal in (
                ("happy", "PASS_FIRST", "SUCCESS"),
                ("never passes", "ESCALATION", "ESCALATED"),
                ("nothing to do", "NO_OP", "NO_OP"),
            ):
                s.add(
                    CaseRow(
                        tenant_id=self.inp.tenant_id,
                        project_id=self.inp.project_id,
                        dataset_id=row.id,
                        name=name,
                        kind=kind,
                        input=INPUT,
                        initial_state=[],
                        state_script=[],
                        expected_terminal=terminal,
                        expected_output=None,
                        assertions=assertions if name == "happy" and assertions else [],
                    )
                )
            s.commit()
            return row.id

    def run(self) -> uuid.UUID:
        return asyncio.run(ActivityEnvironment().run(self.stage.run, self.inp)).id


def test_e13_6_the_stage_builds_the_suite_from_the_newest_dataset() -> None:
    w = World()
    w.dataset(1)
    newest = w.dataset(2)
    assert w.run() == newest


def test_e13_6_the_stage_is_idempotent() -> None:
    w = World()
    first = w.dataset()
    assert w.run() == w.run() == first


def test_e13_6_a_known_assertion_type_on_a_case_is_fine() -> None:
    w = World()
    dataset = w.dataset(assertions=[{"type": "iterations_within_bound"}])
    assert w.run() == dataset


def test_e13_6_an_assertion_type_the_suite_does_not_know_is_a_non_retryable_suite_invalid() -> None:
    w = World()
    w.dataset(assertions=[{"type": "looks_fine"}])
    with pytest.raises(ApplicationError) as caught:
        w.run()
    assert caught.value.type == SUITE_INVALID and caught.value.non_retryable
    assert "looks_fine" not in str(caught.value)  # the type only, never the content


def test_e13_6_no_test_data_is_a_non_retryable_suite_invalid() -> None:
    with pytest.raises(ApplicationError) as caught:
        World().run()
    assert caught.value.type == SUITE_INVALID and caught.value.non_retryable
