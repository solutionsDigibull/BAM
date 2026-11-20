"""The `build_workflow_ui` stage (#768, WG-20): generate and store the Workflow's UI bundle.

After the Workflow's server files are stored (#862) the bundle is rendered by
`ui_bundle.build_ui_bundle` from the Skill manifests and the stored server's name, checked by the
drift gate (`gates/ui_gate.py`), stored as one content-addressed tar through `StorageBackend`
(indexed as an artifact) and recorded in one audit row, `workflow_ui_built`: the file map, the
template and the `ui_bundle` entry (version, digest, entry file, allowed hosts, workflow server) the
manifest will hold. The manifest draft cannot hold it yet (`ui_bundle` needs the `mcp_server` entry,
which exists only once the image is built), so Commit (#766) merges the entry from this row.

The model fills plain text only (`UiCopy`, prompt `ui-copy`, alias `sbam-codegen`). Its answer is
kept to ids the Workflow has and to text without markup or addresses; a model that refuses or
cannot answer in the schema leaves the Sub-Skills' own text in place (`copy_source` says which).
A budget or transport failure raises, so Temporal retries and the budget parks the build.

A Workflow whose bundle cannot be built (no server stored, a Sub-Skill without a manifest) or whose
bundle fails the drift gate is recorded as `workflow_ui_failed` with the stable code and the
pipeline goes on: nothing here changes a hash that was already frozen. Storage trouble raises.
"""

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Final, Protocol

from sbam_api.artifacts.models import ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.audit import repository as audit
from sbam_api.builds.workflow_server import newest as newest_server
from sbam_api.builds.workflow_ui import (
    BUILT_ACTION,
    ENTITY,
    FAILED_ACTION,
    UI_TAR_TYPE,
    WorkflowUiRecord,
)
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.repository import SubSkillManifestRepository
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, StorageBackend, StorageScope, StoredObject
from sbam_api.storage.factory import make_backend
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.pipeline import IdResult, WorkflowRef
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.gates.ui_gate import check_ui_bundle_files
from sbam_workers.build.ui_bundle import (
    SubCopy,
    UiBundleError,
    UiBundleInput,
    UiCopy,
    build_ui_bundle,
    load_ui_template,
    workflow_spec,
)
from sbam_workers.llm.client import ContextTooLarge, LlmClient, LlmRequestRejected
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.registry import activity_for
from sbam_workers.repo_fetch.report import bundle_tar
from sbam_workers.settings import WorkerSettings

STAGE: Final = "build_workflow_ui"
ALIAS: Final = "sbam-codegen"
PROMPT: Final = "ui-copy"
IDS_MISMATCH: Final = "BuildIdsMismatch"
SERVER_NOT_BUILT: Final = "SERVER_NOT_BUILT"
SKILL_NOT_BUILT: Final = "SKILL_NOT_BUILT"
UI_GATE_FAILED: Final = "UI_GATE_FAILED"
_MARKUP: Final = re.compile(r"[<>`]|https?:|wss?:|//\w|\bwww\.", re.IGNORECASE)


def _failure(kind: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=kind, non_retryable=True)


class UiInputs:
    """What the bundle is rendered from."""

    def __init__(self, workflow_name: str, server_name: str, skills: list[SkillManifest]) -> None:
        self.workflow_name = workflow_name
        self.server_name = server_name
        self.skills = skills


class UiStore(Protocol):
    storage: StorageBackend

    def load(self, inp: WorkflowRef) -> UiInputs: ...

    def recorded(self, inp: WorkflowRef, digest: str) -> uuid.UUID | None: ...

    def save(
        self, inp: WorkflowRef, record: WorkflowUiRecord, stored: StoredObject
    ) -> uuid.UUID: ...

    def failed(self, inp: WorkflowRef, error: UiBundleError) -> uuid.UUID: ...


class Copier(Protocol):
    """Asks for the plain text (a fake in the tests, `LlmCopier` in production)."""

    async def __call__(
        self, inp: WorkflowRef, context: Mapping[str, str], temporal_workflow_id: str
    ) -> UiCopy | None: ...


# --- the model's part ----------------------------------------------------------------------------


def _digest(context: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(dict(context), sort_keys=True).encode()).hexdigest()[:16]


def copy_context(name: str, spec: dict[str, Any]) -> dict[str, str]:
    """The `<data>` block of the request: names, ids and the Sub-Skills' own text, no secrets."""
    skills = []
    for skill in spec["skills"]:
        skills.append(
            {
                "id": skill["id"],
                "name": skill["name"],
                "sub_skills": [
                    {
                        "skill_id": s["id"],
                        "name": s["name"],
                        "instructions": s["summary"],
                        "inputs": [
                            {"name": f["name"], "type": f["type"], "required": f["required"]}
                            for f in s["inputs"]
                        ],
                    }
                    for s in skill["subs"]
                ],
            }
        )
    return {"workflow": json.dumps({"name": name, "skills": skills}, indent=1, ensure_ascii=False)}


def known_ids(spec: dict[str, Any]) -> tuple[set[str], set[str]]:
    skills = spec["skills"]
    return {s["id"] for s in skills}, {x["id"] for s in skills for x in s["subs"]}


def problems(copy: UiCopy, categories: set[str], sub_skills: set[str]) -> tuple[str, ...]:
    """Why an answer cannot be used (ids and codes only, so the re-ask holds no text)."""
    found: list[str] = []
    for category, note in copy.review_notes.items():
        if category not in categories:
            found.append(f"UNKNOWN_SKILL:{category}")
        if _MARKUP.search(note):
            found.append(f"MARKUP_IN_REVIEW_NOTE:{category}")
    for skill_id, text in copy.sub_skills.items():
        if skill_id not in sub_skills:
            found.append(f"UNKNOWN_SUB_SKILL:{skill_id}")
        if _MARKUP.search(text.summary):
            found.append(f"MARKUP_IN_SUMMARY:{skill_id}")
        found += [
            f"MARKUP_IN_HINT:{skill_id}:{n}"
            for n, t in text.field_help.items()
            if _MARKUP.search(t)
        ]
    return tuple(found)


def usable(copy: UiCopy, categories: set[str], sub_skills: set[str]) -> UiCopy:
    """The answer reduced to what the Workflow has and text that is safe to show."""
    subs = {
        k: SubCopy(
            summary="" if _MARKUP.search(v.summary) else v.summary,
            field_help={n: t for n, t in v.field_help.items() if not _MARKUP.search(t)},
        )
        for k, v in copy.sub_skills.items()
        if k in sub_skills
    }
    notes = {
        k: v for k, v in copy.review_notes.items() if k in categories and not _MARKUP.search(v)
    }
    return UiCopy(sub_skills=subs, review_notes=notes)


class LlmCopier:
    def __init__(self, llm: LlmClient) -> None:
        self.llm = llm

    async def __call__(
        self, inp: WorkflowRef, context: Mapping[str, str], temporal_workflow_id: str
    ) -> UiCopy | None:
        spec = json.loads(context["workflow"])
        categories = {s["id"] for s in spec["skills"]}
        subs = {x["skill_id"] for s in spec["skills"] for x in s["sub_skills"]}
        scope = LlmScope(
            tenant_id=inp.tenant_id,
            project_id=inp.project_id,
            correlation_id=inp.correlation_id,
            temporal_workflow_id=temporal_workflow_id,
            # the content digest: a Workflow whose spec changed must not get the old copy back from
            # the replay key; a retry of the same spec still replays (#926 review of #930)
            activity_id=f"{STAGE}:{inp.workflow_id}:{_digest(context)}",
        )
        try:
            result = await self.llm.run(
                ALIAS,
                PROMPT,
                UiCopy,
                context,
                scope,
                validate=lambda c: problems(c, categories, subs),
                subject={"workflow_id": str(inp.workflow_id)},
            )
        except (SchemaInvalidAfterRetries, ContextTooLarge, LlmRequestRejected):
            return None  # the Sub-Skills' own text stands in; a budget or transport error raises
        return result.output


# --- storage -------------------------------------------------------------------------------------


class SqlUiStore:
    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    def _ctx(self, inp: WorkflowRef) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def load(self, inp: WorkflowRef) -> UiInputs:
        """The bundle's inputs; another tenant's or project's Workflow is not found."""
        ctx = self._ctx(inp)
        with self.sessions() as session:
            workflow = WorkflowRepository(session, ctx).get(inp.workflow_id)
            if workflow is None:
                raise _failure(IDS_MISMATCH, f"Workflow {inp.workflow_id} is not in this run")
            server = newest_server(session, inp.tenant_id, inp.workflow_id)
            if server is None:
                raise UiBundleError(SERVER_NOT_BUILT, "the Workflow's server is not stored yet")
            subs = SubSkillRepository(session, ctx).of_workflow(inp.workflow_id)
            manifests = SubSkillManifestRepository(session, ctx).newest([s.id for s in subs])
            skills: list[SkillManifest] = []
            for sub in subs:
                found = manifests.get(sub.id)
                if found is None:
                    raise UiBundleError(SKILL_NOT_BUILT, f"{sub.skill_id} has no manifest")
                skills.append(SkillManifest.model_validate(found.manifest))
            return UiInputs(workflow.name, server.server_name, skills)

    @staticmethod
    def _key(digest: str) -> str:
        return f"workflow_ui:{digest.removeprefix('sha256:')}"

    def recorded(self, inp: WorkflowRef, digest: str) -> uuid.UUID | None:
        with self.sessions() as session:
            row = audit.find_by_key(
                session, inp.tenant_id, ENTITY, str(inp.workflow_id), self._key(digest)
            )
            return None if row is None else row.id

    def save(self, inp: WorkflowRef, record: WorkflowUiRecord, stored: StoredObject) -> uuid.UUID:
        with self.sessions() as session:
            ArtifactRepository(session, self._ctx(inp)).index(
                ArtifactKind.CODE_BUNDLE, stored, "sbam-worker"
            )
            row = audit.record(
                session,
                tenant_id=inp.tenant_id,
                actor_id=SYSTEM_ACTOR_ID,
                action=BUILT_ACTION,
                entity_type=ENTITY,
                entity_id=str(inp.workflow_id),
                correlation_id=inp.correlation_id,
                after=record.model_dump(mode="json"),
                idempotency_key=self._key(record.digest),
            )
            session.commit()
            return row.id

    def failed(self, inp: WorkflowRef, error: UiBundleError) -> uuid.UUID:
        digest = hashlib.sha256(f"{error.code}:{error}".encode()).hexdigest()[:16]
        key = f"workflow_ui_failed:{digest}"
        with self.sessions() as session:
            row = audit.find_by_key(session, inp.tenant_id, ENTITY, str(inp.workflow_id), key)
            if row is None:
                row = audit.record(
                    session,
                    tenant_id=inp.tenant_id,
                    actor_id=SYSTEM_ACTOR_ID,
                    action=FAILED_ACTION,
                    entity_type=ENTITY,
                    entity_id=str(inp.workflow_id),
                    correlation_id=inp.correlation_id,
                    after={"code": error.code, "message": str(error)[:300]},
                    idempotency_key=key,
                )
                session.commit()
            return row.id


# --- the stage -----------------------------------------------------------------------------------


async def build_for(
    store: UiStore, copier: Copier, inp: WorkflowRef, temporal_workflow_id: str = "test"
) -> IdResult:
    """The whole stage (tests call it directly; the activity runs it)."""
    try:
        loaded = await asyncio.to_thread(store.load, inp)
        base = UiBundleInput(loaded.workflow_name, loaded.server_name, loaded.skills)
        spec = workflow_spec(
            base.workflow_name, base.server_name, base.version, base.skills, UiCopy()
        )
        categories, sub_skills = known_ids(spec)
        asked = await copier(inp, copy_context(base.workflow_name, spec), temporal_workflow_id)
        copy = usable(asked, categories, sub_skills) if asked is not None else None
        template = load_ui_template()
        bundle = build_ui_bundle(
            UiBundleInput(base.workflow_name, base.server_name, base.skills, copy),
            template=template,
        )
        verdict = check_ui_bundle_files(bundle.files, template, bundle.entry.allowed_hosts)
        if not verdict.passed:
            codes = ",".join(sorted({f["code"] for f in verdict.findings}))
            raise UiBundleError(UI_GATE_FAILED, f"the bundle breaks the pattern: {codes}")
    except UiBundleError as exc:
        return IdResult(id=await asyncio.to_thread(store.failed, inp, exc))
    found = await asyncio.to_thread(store.recorded, inp, bundle.digest)
    if found is not None:  # an activity retry, or a rebuild that gave the same bytes
        return IdResult(id=found)
    stored = await asyncio.to_thread(
        store.storage.put,
        bundle_tar(bundle.files),
        StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id),
        Kind.CODE,
        UI_TAR_TYPE,
    )
    record = WorkflowUiRecord(
        entry=bundle.entry,
        template=bundle.template,
        tar_key=stored.storage_key,
        files={p: hashlib.sha256(d).hexdigest() for p, d in bundle.files.items()},
        copy_source="model" if copy is not None else "manifest",
    )
    return IdResult(id=await asyncio.to_thread(store.save, inp, record, stored))


@lru_cache(maxsize=1)
def _stage() -> tuple[SqlUiStore, LlmCopier]:
    settings = WorkerSettings()  # type: ignore[call-arg]
    sessions = mirror.sessions()
    llm = LlmClient(settings, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(settings))
    return SqlUiStore(sessions, make_backend(settings)), LlmCopier(llm)


@activity_for(TaskQueue.LLM)
@activity.defn(name=STAGE)
async def build_workflow_ui(inp: WorkflowRef) -> IdResult:
    """Render, check and store the Workflow's UI bundle and record its `ui_bundle` entry (#768)."""
    store, copier = _stage()
    return await build_for(store, copier, inp, activity.info().workflow_id or "")
