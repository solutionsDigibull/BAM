"""The real `generate_bodies`, `assemble` and `correct` stages on the worker (E11.2b, #355).

E11.2 to E11.4 built the three stages as classes with injected dependencies, but the worker kept
registering the stubs in `stubs.py`. This module is the wiring: it builds the dependencies (the
`LlmClient`, the storage backend, the stores) and the `inputs` loaders, and registers one activity
per stage under the stub's name and queue with the same Pydantic in/out models, so `SubSkillBuild`
does not change. The stub functions stay in `stubs.py` as the workflow's typed handles; they are
just no longer registered.

The loaders rebuild what the earlier stages produced from the database; no stage passes a payload:

* The manifest is the row `build_manifest` stored for the stage key; its sealed library pins are
  what `check_libraries` resolved. The template is the one it names, at the hash it names.
* The skeleton is rendered again from that manifest with `render_manifest`. It is deterministic and
  is what the `render_skeleton` stage (still a stub) would produce.
* `modules` are the top-level modules of the pinned libraries, from the registry of the image digest
  the manifest pinned.
* The Sub-Skill is the stored row. After a correction that patched the ISATVON it is the patched
  one: the newest `isatvon_corrected` audit row of this cycle (E11.4 keeps it there), and the loop
  spec is compiled from it.
* `assemble` loads the manifest of the code attempt's own `manifest_id`, not of the current pass: a
  correction records the manifest of the pass that failed, and the skeleton has to match it.
* `correct` gets no sandbox trace yet (the test stages are stubs).
"""

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sbam_api.audit import repository as audit
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.repository import SubSkillManifestRepository
from sbam_api.registry.repository import registry_for_digest
from sbam_api.storage.factory import make_backend
from sbam_contracts.build import BuildStageInput, CorrectionInput
from sbam_contracts.isatvon14 import SubSkill
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.pipeline import IdResult
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build import assemble as assemble_stage
from sbam_workers.build import bodies as bodies_stage
from sbam_workers.build import correct as correct_stage
from sbam_workers.build.assemble import Assemble, AssembleInputs, SqlBundleStore
from sbam_workers.build.bodies import BodiesInputs, GenerateBodies, SqlAttemptStore
from sbam_workers.build.codegen.params import params_from_manifest
from sbam_workers.build.codegen.render import render_manifest
from sbam_workers.build.codegen.store import LoadedTemplate, load_template
from sbam_workers.build.correct import (
    AUDIT_ENTITY,
    Correct,
    CorrectionInputs,
    SqlCorrectionStore,
)
from sbam_workers.isatvon.loop_spec import LoopSpecError, compile_definition
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

INPUT_MISMATCH = assemble_stage.INPUT_MISMATCH
LOOP_SPEC_INVALID = "LoopSpecInvalid"


def _mismatch(message: str, kind: str = INPUT_MISMATCH) -> ApplicationError:
    return ApplicationError(message, type=kind, non_retryable=True)


@dataclass(frozen=True)
class Loaded:
    """One pass's manifest, its template, the libraries' modules and the effective Sub-Skill."""

    manifest_id: uuid.UUID
    manifest: SkillManifest
    template: LoadedTemplate
    modules: tuple[str, ...]
    sub_skill: dict[str, Any]


def loop_spec_of(sub_skill: Mapping[str, Any]) -> bytes:
    """RFC 8785 `loop_spec.json` of a structured Sub-Skill (E9.2). A bound that does not compile
    cannot be retried into working."""
    try:
        definition = SubSkill.model_validate(sub_skill).isatvon_definition
        return compile_definition(definition).canonical_bytes
    except LoopSpecError as exc:
        raise _mismatch(f"the loop spec does not compile: {exc}", LOOP_SPEC_INVALID) from exc


class SqlBuildInputs:
    """The `inputs` loaders of the three stages (see the module docstring)."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def _ctx(inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def _load(
        self, session: Session, inp: BuildStageInput, manifest_id: uuid.UUID | None
    ) -> Loaded:
        """`manifest_id` None: the manifest of this pass; else that one (see `assemble`)."""
        ctx = self._ctx(inp)
        manifests = SubSkillManifestRepository(session, ctx)
        row = (
            manifests.find(inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt)
            if manifest_id is None
            else manifests.get(manifest_id)
        )
        if row is None:
            raise _mismatch(
                f"no manifest for {inp.sub_skill_id} build {inp.build_no} attempt {inp.attempt}"
            )
        manifest = SkillManifest.model_validate(row.manifest)
        ref = manifest.sbam_build_record.build.template
        template = load_template(ref.name, ref.version)
        if template.sha256 != ref.sha256:
            raise _mismatch(f"template {ref.name} {ref.version} changed since the manifest")
        registry = registry_for_digest(session, row.runtime_digest)
        if registry is None:  # check_libraries stops the build first; fail closed all the same
            raise _mismatch("no runtime registry for the manifest's runtime image digest")
        modules: set[str] = set()
        for library in manifest.sbam_build_record.build.libraries:
            package = registry.package(library.package)
            if package is not None:  # template-provided packages are not in the registry
                modules.update(package.top_level_modules)
        return Loaded(
            row.id, manifest, template, tuple(sorted(modules)), self._sub_skill(session, inp)
        )

    def _sub_skill(self, session: Session, inp: BuildStageInput) -> dict[str, Any]:
        """The Sub-Skill as the corrections left it: the newest patch of the cycle, else the row."""
        ctx = self._ctx(inp)
        sub = SubSkillRepository(session, ctx).get(inp.sub_skill_id)
        if sub is None or sub.workflow_id != inp.workflow_id:
            raise _mismatch(f"Sub-Skill {inp.sub_skill_id} is not in this run")
        for attempt in range(inp.attempt, 0, -1):  # the correction that made attempt N is keyed N
            earlier = audit.find_by_key(
                session,
                inp.tenant_id,
                AUDIT_ENTITY,
                str(inp.sub_skill_id),
                correct_stage.stage_key(inp, attempt),
            )
            if earlier is not None and earlier.after is not None:
                patched: dict[str, Any] = dict(earlier.after["sub_skill"])
                return patched
        return dict(sub.isatvon)

    def _bodies(self, inp: BuildStageInput) -> BodiesInputs:
        with self.sessions() as session:
            loaded = self._load(session, inp, None)
            return BodiesInputs(
                template=loaded.template,
                params=params_from_manifest(loaded.manifest),
                skeleton=render_manifest(loaded.template, loaded.manifest),
                modules=loaded.modules,
                manifest_id=loaded.manifest_id,
            )

    def _assemble(self, inp: BuildStageInput) -> AssembleInputs:
        with self.sessions() as session:
            attempt = CodeAttemptRepository(session, self._ctx(inp)).find(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
            if attempt is None:  # generate_bodies always runs first; a missing row is a wiring bug
                raise _mismatch(
                    f"no code_attempts row for {inp.sub_skill_id} attempt {inp.attempt}"
                )
            loaded = self._load(session, inp, attempt.manifest_id)
            return AssembleInputs(
                template=loaded.template,
                params=params_from_manifest(loaded.manifest),
                skeleton=render_manifest(loaded.template, loaded.manifest),
                loop_spec=loop_spec_of(loaded.sub_skill),
                manifest_id=loaded.manifest_id,
            )

    def _correction(self, inp: CorrectionInput) -> CorrectionInputs:
        with self.sessions() as session:
            loaded = self._load(session, inp, None)
            return CorrectionInputs(
                template=loaded.template,
                params=params_from_manifest(loaded.manifest),
                skeleton=render_manifest(loaded.template, loaded.manifest),
                modules=loaded.modules,
                sub_skill=loaded.sub_skill,
                manifest_id=loaded.manifest_id,
            )

    async def bodies(self, inp: BuildStageInput) -> BodiesInputs:
        return await asyncio.to_thread(self._bodies, inp)

    async def assemble(self, inp: BuildStageInput) -> AssembleInputs:
        return await asyncio.to_thread(self._assemble, inp)

    async def correction(self, inp: CorrectionInput) -> CorrectionInputs:
        return await asyncio.to_thread(self._correction, inp)


# --- the registered stages ------------------------------------------------------------------------
# Built on first use, one stage at a time, so a worker only needs what its queue's stages need.

def _llm(settings: WorkerSettings, sessions: sessionmaker[Session]) -> LlmClient:
    return LlmClient(settings, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(settings))


@lru_cache(maxsize=1)
def _generate() -> GenerateBodies:
    settings, sessions = WorkerSettings(), mirror.sessions()  # type: ignore[call-arg]
    return GenerateBodies(
        SqlBuildInputs(sessions).bodies, _llm(settings, sessions), SqlAttemptStore(sessions)
    )


@lru_cache(maxsize=1)
def _assemble() -> Assemble:
    settings, sessions = WorkerSettings(), mirror.sessions()  # type: ignore[call-arg]
    return Assemble(
        SqlBuildInputs(sessions).assemble, make_backend(settings), SqlBundleStore(sessions)
    )


@lru_cache(maxsize=1)
def _correct() -> Correct:
    settings, sessions = WorkerSettings(), mirror.sessions()  # type: ignore[call-arg]
    return Correct(
        SqlBuildInputs(sessions).correction, _llm(settings, sessions), SqlCorrectionStore(sessions)
    )


@activity_for(TaskQueue.LLM)
@activity.defn(name=bodies_stage.STAGE)
async def generate_bodies_stage(inp: BuildStageInput) -> IdResult:
    """LLM bodies for one build attempt (E11.2); a pre-check failure is GateFailed."""
    return await _generate().run(inp)


@activity_for(TaskQueue.BUILD)
@activity.defn(name=assemble_stage.STAGE)
async def assemble_stage_activity(inp: BuildStageInput) -> IdResult:
    """Skeleton + bodies -> the stored code bundle (E11.3); a syntax failure is GateFailed."""
    return await _assemble().run(inp)


@activity_for(TaskQueue.LLM)
@activity.defn(name=correct_stage.STAGE)
async def correct_stage_activity(inp: CorrectionInput) -> IdResult:
    """The correction written as the next attempt (E11.4)."""
    return await _correct().run(inp)
