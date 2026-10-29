"""E11.2b (#355): SubSkillBuild with the real `generate_bodies`, `assemble` and `correct` stages.

The real child workflow runs the real stage classes (E11.2 to E11.4) against the recorded mock-LLM
answers and the local disk backend; the stages that still are stubs (the gates, the tests, the
certification) answer like the stubs. Only the loaders are replaced by the MS01.1 fixtures here:
`tests/unit/test_build_wiring.py` covers the real ones. Time-skipping Temporal server, no Docker.
"""

import json
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from codegen_cases import ms01_loop_spec, ms01_params
from llm_db import FakeBifrost, completion, llm_sessions, make_client
from sbam_api.artifacts.models import Artifact
from sbam_api.audit.models import AuditLog
from sbam_api.builds.models import CodeAttempt
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import BuildStageInput, CorrectionInput
from sbam_workers.build.assemble import Assemble, AssembleInputs, SqlBundleStore
from sbam_workers.build.bodies import BodiesInputs, GenerateBodies, SqlAttemptStore
from sbam_workers.build.codegen.bodies import ALIAS
from sbam_workers.build.codegen.render import render_skeleton
from sbam_workers.build.codegen.store import load_template
from sbam_workers.build.correct import Correct, CorrectionInputs, SqlCorrectionStore
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_sub_skill_build import (
    Build,
    gate_failed,
    only_when,
    run,
    traced,
)

MOCK = Path(__file__).resolve().parents[1] / "llm-mock" / "sbam-codegen" / "e11_2_ms01_1.json"
MS01_ANSWER = json.loads(MOCK.read_text(encoding="utf-8"))["response"]
SUB_SKILL = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "isa14" / "ms01_1_structured.json"
    ).read_text(encoding="utf-8")
)


@pytest.fixture
def root() -> Iterator[Path]:
    """A short storage root: a bundle's key is ~180 characters, and pytest's `tmp_path` pushes it
    past Windows' 260-character path limit."""
    with tempfile.TemporaryDirectory(prefix="sbam-") as directory:
        yield Path(directory)


def codegen_reply() -> dict[str, Any]:
    return completion(json.dumps(MS01_ANSWER), model=f"{ALIAS}-primary")


def corrector_reply() -> dict[str, Any]:
    """A correction that changes nothing: the bodies stay, the pass runs again."""
    body = {"isatvon_patch": None, "bodies": None, "rationale": "looked again, no change"}
    return completion(json.dumps(body), model="sbam-corrector-primary")


class Real:
    """The three real stages over one SQLite database and a temp-dir storage backend."""

    def __init__(self, root: Path, replies: list[Any]) -> None:
        self.sessions: sessionmaker[Session] = llm_sessions()
        bind = self.sessions.kw["bind"]
        tables = [CodeAttempt.__table__, Artifact.__table__, AuditLog.__table__]
        CodeAttempt.metadata.create_all(bind, tables=tables)  # type: ignore[arg-type]
        self.storage = LocalDiskBackend(root)
        self.bifrost = FakeBifrost(replies)
        client, _ = make_client(self.bifrost, sessions=self.sessions)
        template = load_template("fastmcp-loopy", "1.0.0")
        params = ms01_params()
        skeleton = render_skeleton(template, params)

        async def bodies(inp: BuildStageInput) -> BodiesInputs:
            return BodiesInputs(template=template, params=params, skeleton=skeleton, modules=())

        async def assemble(inp: BuildStageInput) -> AssembleInputs:
            return AssembleInputs(
                template=template, params=params, skeleton=skeleton, loop_spec=ms01_loop_spec()
            )

        async def correction(inp: CorrectionInput) -> CorrectionInputs:
            return CorrectionInputs(
                template=template,
                params=params,
                skeleton=skeleton,
                modules=(),
                sub_skill=SUB_SKILL,
            )

        self.overrides: dict[str, Any] = {
            "generate_bodies": GenerateBodies(bodies, client, SqlAttemptStore(self.sessions)).run,
            "assemble": Assemble(assemble, self.storage, SqlBundleStore(self.sessions)).run,
            "correct": Correct(correction, client, SqlCorrectionStore(self.sessions)).run,
        }

    def attempts(self) -> dict[int, str]:
        """attempt number -> status, for every `code_attempts` row the build wrote."""
        with self.sessions() as session:
            rows = session.scalars(select(CodeAttempt).order_by(CodeAttempt.attempt))
            return {row.attempt: row.status for row in rows}

    def bundles(self) -> list[Artifact]:
        with self.sessions() as session:
            return list(session.scalars(select(Artifact)))

    def stored(self, row: Artifact) -> bool:
        return self.storage.exists(row.storage_key)


def stage_stubs(**behave: Callable[[Any], Any]) -> dict[str, Any]:
    """The gates still are stubs; a test makes one of them fail."""
    return {name: traced(name, [], fn) for name, fn in behave.items()}


# --- the scenarios --------------------------------------------------------------------------------


def test_e11_2b_t07_ms01_builds_with_the_real_stages(root: Path) -> None:
    real = Real(root, [codegen_reply()])

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.state()

    result, state = run(scenario, **real.overrides)
    assert result.certified and state == "CERTIFIED"
    assert real.attempts() == {0: "ASSEMBLED"}  # one real model answer, assembled
    [bundle] = real.bundles()
    assert real.stored(bundle)  # the code bundle is in storage and indexed
    assert len(real.bifrost.requests) == 1


def test_e11_2b_t08_a_defect_is_corrected_by_the_real_corrector(root: Path) -> None:
    real = Real(root, [codegen_reply(), corrector_reply()])

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.actions()

    result, actions = run(
        scenario,
        **real.overrides,
        **stage_stubs(ast_gate=only_when(lambda inp: inp.attempt == 0)),
    )
    assert result.certified
    assert real.attempts() == {0: "ASSEMBLED", 1: "ASSEMBLED"}  # the correction is attempt 1
    assert len(real.bifrost.requests) == 2  # the bodies, then one correction
    assert len(real.bundles()) == 1  # an unchanged correction assembles the same bytes: one object
    assert actions == [
        "gate_failed",
        "correction_applied",
        "tests_started",
        "tests_passed",
        "sub_skill.certified",
    ]


def test_e11_2b_t09_an_unfixable_defect_escalates_after_two_real_corrections(
    root: Path,
) -> None:
    real = Real(root, [codegen_reply(), corrector_reply(), corrector_reply()])

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        await build.end()
        return waiting, build.actions()

    waiting, actions = run(scenario, **real.overrides, **stage_stubs(ast_gate=gate_failed))
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.corrections_used == 2
    assert real.attempts() == {0: "ASSEMBLED", 1: "ASSEMBLED", 2: "ASSEMBLED"}
    assert len(real.bifrost.requests) == 3  # the bodies, then exactly two corrections
    assert actions[-2:] == ["attempts_exhausted", "sub_skill.needs_fde_review"]
