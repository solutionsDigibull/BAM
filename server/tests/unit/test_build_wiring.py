"""E11.2b (#355): the real `generate_bodies`, `assemble` and `correct` stages are registered in
place of the stubs, and their loaders rebuild a pass's inputs from the database (manifest, template,
libraries' modules, effective Sub-Skill). SQLite with the real models; no Docker."""

import asyncio
import copy
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from pipeline_db import pipeline_sessions
from registry_db import sample_registry
from sbam_api.audit import repository as audit
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.ids import uuid7
from sbam_api.isatvon.models import SubSkill
from sbam_api.manifests.models import SubSkillManifest
from sbam_api.registry.models import RuntimeRegistryPackageRow, RuntimeRegistryRow
from sbam_api.registry.repository import upsert_registry
from sbam_contracts.build import BuildStageInput, CorrectionInput
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.runs import TaskQueue
from sbam_workers import mirror, registry
from sbam_workers.build import correct as correct_stage
from sbam_workers.build import wiring
from sbam_workers.build.assemble import Assemble, AssembleInputs
from sbam_workers.build.bodies import BodiesInputs, GenerateBodies
from sbam_workers.build.codegen.params import params_from_manifest
from sbam_workers.build.codegen.render import render_manifest
from sbam_workers.build.codegen.store import LoadedTemplate, load_template
from sbam_workers.build.correct import Correct, CorrectionInputs
from sbam_workers.build.wiring import (
    INPUT_MISMATCH,
    LOOP_SPEC_INVALID,
    SqlBuildInputs,
    loop_spec_of,
)
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

TENANT, PROJECT, WORKFLOW, MACRO = uuid.uuid4(), uuid7(), uuid.uuid4(), uuid.uuid4()
DIGEST_BYTE = "ab"
DIGEST = "sha256:" + DIGEST_BYTE * 32
MAX_ITERATIONS = "isatvon_definition.v_variables.max_iterations"


def structured() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8")
    )
    return raw


def patched_sub_skill(max_iterations: int) -> dict[str, Any]:
    raw = copy.deepcopy(structured())
    raw["isatvon_definition"]["v_variables"]["max_iterations"] = max_iterations
    return raw


class World:
    """One Sub-Skill, its manifests and attempts, in a SQLite database with the real tables."""

    def __init__(self) -> None:
        self.template: LoadedTemplate = load_template("fastmcp-loopy", "1.0.0")
        self.sessions: sessionmaker[Session] = pipeline_sessions()
        bind = self.sessions.kw["bind"]
        tables = [
            SubSkill.__table__,
            SubSkillManifest.__table__,
            CodeAttempt.__table__,
            RuntimeRegistryRow.__table__,
            RuntimeRegistryPackageRow.__table__,
        ]
        SubSkill.metadata.create_all(bind, tables=tables)  # type: ignore[arg-type]
        from sbam_api.audit.models import AuditLog

        AuditLog.metadata.create_all(bind, tables=[AuditLog.__table__])  # type: ignore[list-item]
        self.sub = self.add_sub_skill()
        self.loaders = SqlBuildInputs(self.sessions)

    def bodies(self, inp: BuildStageInput) -> BodiesInputs:
        return asyncio.run(self.loaders.bodies(inp))

    def assemble(self, inp: BuildStageInput) -> AssembleInputs:
        return asyncio.run(self.loaders.assemble(inp))

    def correction(self, inp: CorrectionInput) -> CorrectionInputs:
        return asyncio.run(self.loaders.correction(inp))

    def add_registry(self) -> None:
        with self.sessions() as session:
            upsert_registry(session, sample_registry(DIGEST_BYTE), activate=True)
            session.commit()

    def add_sub_skill(
        self, workflow_id: uuid.UUID = WORKFLOW, raw: dict[str, Any] | None = None
    ) -> uuid.UUID:
        body = structured() if raw is None else raw
        with self.sessions() as session:
            row = SubSkill(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=PROJECT,
                macro_skill_id=MACRO,
                workflow_id=workflow_id,
                skill_id=body["skill_metadata"]["skill_id"],
                skill_name=body["skill_metadata"]["skill_name"],
                order=1,
                isatvon=body,
                isatvon_framework_version="1.4",
                sow_refs=["F-ACCESS-1"],
                validation_errors=[],
                content_sha256="0" * 64,
            )
            row.state = "BUILDING"
            session.add(row)
            session.commit()
            return row.id

    def manifest_data(self, *, template_sha256: str | None = None) -> dict[str, Any]:
        """The MS01.1 manifest with the loaded template's own ref (what `build_manifest` writes)."""
        data: dict[str, Any] = json.loads(
            fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8")
        )
        build = data["sbam_build_record"]["build"]
        build["template"] = {
            **self.template.ref.model_dump(),
            **({} if template_sha256 is None else {"sha256": template_sha256}),
        }
        build["runtime_image_digest"] = DIGEST
        return SkillManifest.sealed(data).dump()

    def add_manifest(self, attempt: int, data: dict[str, Any] | None = None) -> uuid.UUID:
        with self.sessions() as session:
            row = SubSkillManifest(
                tenant_id=TENANT,
                project_id=PROJECT,
                sub_skill_id=self.sub,
                build_no=1,
                cycle=0,
                attempt=attempt,
                manifest=self.manifest_data() if data is None else data,
                sha256="sha256:" + "1" * 64,
                runtime_digest=DIGEST,
            )
            session.add(row)
            session.commit()
            return row.id

    def add_code_attempt(self, attempt: int, manifest_id: uuid.UUID | None) -> None:
        with self.sessions() as session:
            session.add(
                CodeAttempt(
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    workflow_id=WORKFLOW,
                    sub_skill_id=self.sub,
                    manifest_id=manifest_id,
                    build_no=1,
                    cycle=0,
                    attempt=attempt,
                    template_name=self.template.name,
                    template_version=self.template.version,
                    template_sha256=self.template.sha256,
                    status="BODIES_GENERATED",
                    bodies={},
                    llm_call_ids=[],
                    trace_id="t",
                    findings=[],
                    created_by="sbam-worker",
                )
            )
            session.commit()

    def add_correction(self, attempt: int, sub_skill: dict[str, Any]) -> None:
        """The audit row a correction that patched the ISATVON leaves (E11.4); it made `attempt`."""
        with self.sessions() as session:
            audit.record(
                session,
                tenant_id=TENANT,
                actor_id="sbam-corrector",
                action=correct_stage.AUDIT_ACTION,
                entity_type=correct_stage.AUDIT_ENTITY,
                entity_id=str(self.sub),
                correlation_id="cid",
                after={"sub_skill": sub_skill},
                idempotency_key=correct_stage.stage_key(self.stage(attempt), attempt),
            )
            session.commit()

    def stage(self, attempt: int = 0, workflow_id: uuid.UUID = WORKFLOW) -> BuildStageInput:
        return BuildStageInput(
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id=uuid.uuid4(),
            correlation_id="cid",
            workflow_id=workflow_id,
            sub_skill_id=self.sub,
            build_no=1,
            attempt=attempt,
        )

    def correcting(self, attempt: int = 0) -> CorrectionInput:
        return CorrectionInput(**self.stage(attempt).model_dump(), failed_gate="ast_gate")


def refused(error: pytest.ExceptionInfo[ApplicationError], kind: str) -> None:
    assert error.value.type == kind and error.value.non_retryable


# --- the loaders ----------------------------------------------------------------------------------


def test_e11_2b_t01_bodies_inputs_rebuild_what_the_earlier_stages_produced() -> None:
    world = World()
    world.add_registry()
    manifest_id = world.add_manifest(0)
    given = world.bodies(world.stage())
    manifest = SkillManifest.model_validate(world.manifest_data())
    assert given.manifest_id == manifest_id
    assert given.template.sha256 == world.template.sha256
    assert dict(given.params) == params_from_manifest(manifest)
    assert given.skeleton == render_manifest(world.template, manifest)
    registry_ = sample_registry(DIGEST_BYTE)
    wanted = {
        module
        for name in ("fastmcp", "psycopg", "pyjwt")
        for package in [registry_.package(name)]
        if package is not None
        for module in package.top_level_modules
    }
    assert wanted and set(given.modules) == wanted  # the pinned libraries' importable modules
    assert list(given.modules) == sorted(given.modules)


def test_e11_2b_t02_assemble_reads_the_manifest_of_the_code_attempt_not_this_pass() -> None:
    world = World()
    world.add_registry()
    first = world.add_manifest(0)
    second = world.add_manifest(1)
    world.add_code_attempt(1, manifest_id=first)  # a correction records the failed pass's manifest
    given = world.assemble(world.stage(attempt=1))
    assert given.manifest_id == first != second
    assert given.loop_spec == loop_spec_of(structured())


def test_e11_2b_t03_assemble_compiles_the_newest_patched_isatvon_of_the_cycle() -> None:
    world = World()
    world.add_registry()
    manifest = world.add_manifest(0)
    for attempt in (0, 1, 2):
        world.add_code_attempt(attempt, manifest_id=manifest)
    original = loop_spec_of(structured())
    assert (world.assemble(world.stage(attempt=1))).loop_spec == original
    world.add_correction(1, patched_sub_skill(7))
    patched = loop_spec_of(patched_sub_skill(7))
    assert patched != original
    assert (world.assemble(world.stage(attempt=1))).loop_spec == patched
    # correction 2 changed no ISATVON (no audit row): attempt 2 is still built on correction 1's
    assert (world.assemble(world.stage(attempt=2))).loop_spec == patched
    world.add_correction(2, patched_sub_skill(9))
    assert (world.assemble(world.stage(attempt=2))).loop_spec == loop_spec_of(patched_sub_skill(9))
    # attempt 0 never sees a later correction
    assert (world.assemble(world.stage(attempt=0))).loop_spec == original


def test_e11_2b_t04_the_correction_gets_the_effective_sub_skill() -> None:
    world = World()
    world.add_registry()
    world.add_manifest(0)
    world.add_manifest(1)
    assert (world.correction(world.correcting(0))).sub_skill == structured()
    world.add_correction(1, patched_sub_skill(7))
    given = world.correction(world.correcting(1))
    assert given.sub_skill == patched_sub_skill(7)
    assert given.trace == ""  # the test stages are stubs: there is no sandbox trace yet


# --- what refuses ---------------------------------------------------------------------------------


def test_e11_2b_t05_a_missing_manifest_is_a_non_retryable_mismatch() -> None:
    world = World()
    world.add_registry()
    with pytest.raises(ApplicationError) as error:
        world.bodies(world.stage())
    refused(error, INPUT_MISMATCH)


def test_e11_2b_t05_a_template_that_changed_since_the_manifest_is_refused() -> None:
    world = World()
    world.add_registry()
    world.add_manifest(0, world.manifest_data(template_sha256="0" * 64))
    with pytest.raises(ApplicationError) as error:
        world.bodies(world.stage())
    refused(error, INPUT_MISMATCH)


def test_e11_2b_t05_no_registry_for_the_pinned_image_is_refused() -> None:
    world = World()  # no registry stored
    world.add_manifest(0)
    with pytest.raises(ApplicationError) as error:
        world.bodies(world.stage())
    refused(error, INPUT_MISMATCH)


def test_e11_2b_t05_a_sub_skill_of_another_workflow_is_refused() -> None:
    world = World()
    world.add_registry()
    world.add_manifest(0)
    with pytest.raises(ApplicationError) as error:
        world.bodies(world.stage(workflow_id=uuid.uuid4()))
    refused(error, INPUT_MISMATCH)


def test_e11_2b_t05_assemble_without_its_code_attempt_is_a_wiring_bug() -> None:
    world = World()
    world.add_registry()
    world.add_manifest(0)
    with pytest.raises(ApplicationError) as error:
        world.assemble(world.stage())
    refused(error, INPUT_MISMATCH)


def test_e11_2b_t05_a_loop_spec_that_does_not_compile_is_not_retried() -> None:
    with pytest.raises(ApplicationError) as error:
        loop_spec_of(patched_sub_skill(0))  # max_iterations is 1 to 10
    refused(error, LOOP_SPEC_INVALID)


# --- the registration -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "queue"),
    [
        ("generate_bodies", TaskQueue.LLM),
        ("correct", TaskQueue.LLM),
        ("assemble", TaskQueue.BUILD),
    ],
)
def test_e11_2b_t06_each_stage_is_registered_once_on_its_queue_by_the_wiring(
    name: str, queue: TaskQueue
) -> None:
    registry.load_all()
    _, activities = registry.collect(queue)
    found = [a for a in activities if a.__temporal_activity_definition.name == name]  # type: ignore[attr-defined]
    assert len(found) == 1
    assert found[0].__module__ == "sbam_workers.build.wiring"  # the real stage, not the stub
    for other in TaskQueue:
        if other is not queue:
            _, elsewhere = registry.collect(other)
            assert name not in {a.__temporal_activity_definition.name for a in elsewhere}  # type: ignore[attr-defined]


def test_e11_2b_t07_the_registered_stages_build_their_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first call of each stage builds its client, storage and stores from the worker's
    settings; a worker only needs what the stages on its queue need."""
    from llm_db import settings

    with tempfile.TemporaryDirectory(prefix="sbam-") as root:
        sessions = pipeline_sessions()
        monkeypatch.setattr(wiring, "WorkerSettings", lambda: settings(storage_root=Path(root)))
        monkeypatch.setattr(mirror, "sessions", lambda: sessions)
        for builder, kind in (
            (wiring._generate, GenerateBodies),
            (wiring._assemble, Assemble),
            (wiring._correct, Correct),
        ):
            builder.cache_clear()
            assert isinstance(builder(), kind)
            assert builder() is builder()  # built once, then reused
            builder.cache_clear()
