"""`generate_sow` (E6.2, TD §10.6, §13.2): the project's SOW from its OKF bundle via `sbam-sow`.

`generate_version` is the whole step and takes its dependencies as arguments, so tests run it with
a fake Bifrost and a temp-dir storage; the activity only builds the real ones. For run `inp`
(`ref_id` = the READY knowledge version):

  1. a version this run already wrote is returned as it is (a retry never calls the model again,
     T10); one that failed fails again, non-retryable;
  2. the bundle is read hash-verified, and `bundle.json` must match the version's `okf_sha256`;
  3. the context is the whole bundle when it fits, else a ranked selection per section (FULL /
     RANKED, `sow/context.py`); how it was chosen goes to `llm_calls.context_manifest`;
  4. `sbam-sow` answers a `SowDocument`. Beyond the schema, every citation must name a chunk or
     entity that was sent, with its document version (`UNKNOWN_CITATION`); a failed check is
     re-asked like a schema error, with codes and ids only;
  5. SBAM, not the model, numbers the features (`F-<module>-<n>`, in module order) and copies each
     citation's location from the bundle;
  6. the version is stored GENERATED with its canonical sha256 and the `llm_calls` row it came from.
Re-asks exhausted: the version is stored GENERATION_FAILED (`SCHEMA_INVALID`) and the activity
fails non-retryable (`SchemaInvalidAfterRetries`), so the pipeline goes to SOW_GENERATION_FAILED.
Bifrost down: retryable until the last attempt, which stores `LLM_UNAVAILABLE`; a request Bifrost
refuses (4xx) or a context over the model's limit fail at once (`LLM_REJECTED`,
`CONTEXT_TOO_LARGE`). The knowledge version is never touched. Logs carry ids and counts only.
"""

import asyncio
import hashlib
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from sbam_api.db.repository import TenantContext
from sbam_api.knowledge.graph import load
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.knowledge.repository import KnowledgeVersionRepository
from sbam_api.knowledge.service import read_okf_part
from sbam_api.sow.models import SowVersion
from sbam_api.sow.repository import SowVersionRepository
from sbam_api.sow.service import Generation, record_generation
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.factory import make_backend
from sbam_contracts.okf import BundleManifest
from sbam_contracts.pipeline import IdResult, SowGenerateInput, StageRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sow import SECTIONS, Citation, SowDocument
from sbam_contracts.state_machines import KnowledgeVersionState, SowVersionState
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.llm.client import (
    ContextTooLarge,
    LlmClient,
    LlmRequestRejected,
    LlmTransportError,
)
from sbam_workers.llm.embed import embed
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings
from sbam_workers.sow.context import RESERVED_TOKENS, Bundle, Embed, SowContext, build_context

log = logging.getLogger("sbam_workers.sow.generate")

ALIAS: Final = "sbam-sow"
PROMPT_ID: Final = "sow-generate"
GENERATE_SOW_ATTEMPTS: Final = 3  # ProjectPipeline's PLAN uses the same (a test checks)
SCHEMA_INVALID: Final = "SchemaInvalidAfterRetries"
KNOWLEDGE_NOT_READY: Final = "KnowledgeNotReady"
LLM_UNAVAILABLE: Final = "LLM_UNAVAILABLE"
# Error reasons stored on a GENERATION_FAILED version (codes, never document text).
REASONS: Final = {
    SCHEMA_INVALID: "SCHEMA_INVALID",
    "ContextTooLarge": "CONTEXT_TOO_LARGE",
    "LlmRequestRejected": "LLM_REJECTED",
    LLM_UNAVAILABLE: LLM_UNAVAILABLE,
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9#:._-]{1,80}$")


@dataclass(frozen=True)
class SowDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend
    llm: LlmClient
    embed: Embed
    budget_tokens: int
    attempts: int = GENERATE_SOW_ATTEMPTS


# --- checks and SBAM's own fields -----------------------------------------------------------------


def _citations(doc: SowDocument) -> list[tuple[str, Citation]]:
    """Every citation with the path the re-ask names it by."""
    out: list[tuple[str, Citation]] = []
    for name in SECTIONS:
        section = getattr(doc, name)
        if not isinstance(section, list):
            out += [(f"{name}.citations", c) for c in section.citations]
    for i, m in enumerate(doc.modules):
        out += [(f"modules.{i}.citations", c) for c in m.citations]
    for i, f in enumerate(doc.features):
        out += [(f"features.{i}.citations", c) for c in f.citations]
    return out


def check_citations(context: SowContext) -> Callable[[SowDocument], tuple[str, ...]]:
    """Every citation names an id that was sent, with a document version that id belongs to."""

    def check(doc: SowDocument) -> tuple[str, ...]:
        errors: list[str] = []
        for path, c in _citations(doc):
            ref = c.okf_ref_id if _SAFE_ID.match(c.okf_ref_id) else "<id>"
            docs = context.citable.get(c.okf_ref_id)
            if docs is None:
                errors.append(f"{path}: UNKNOWN_CITATION {ref}")
            elif c.document_version_id not in docs:
                errors.append(f"{path}: CITATION_DOCUMENT_MISMATCH {ref}")
        return tuple(dict.fromkeys(errors))  # once each, in order

    return check


def finalize(doc: SowDocument, context: SowContext) -> dict[str, Any]:
    """SBAM's fields on the model's answer: feature ids `F-<module>-<n>` (module order, then the
    model's order) and each citation's location as the bundle has it. Validated again."""
    data = doc.model_dump(mode="json")
    counts: dict[str, int] = {}
    for feature in data["features"]:
        module = feature["module_id"].removeprefix("M-")
        counts[module] = counts.get(module, 0) + 1
        feature["id"] = f"F-{module}-{counts[module]}"

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            for c in node.get("citations", []):
                where = context.locations.get(
                    (c["okf_ref_id"], uuid.UUID(c["document_version_id"]))
                )
                if where is not None:
                    c["location"] = dict(where)
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for value in node:
                fix(value)

    fix(data)
    return SowDocument.model_validate(data).model_dump(mode="json")


# --- the step -------------------------------------------------------------------------------------


def _ctx(inp: StageRef) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _existing(deps: SowDeps, inp: StageRef) -> tuple[uuid.UUID, str, str | None] | None:
    with deps.sessions() as session:
        row: SowVersion | None = SowVersionRepository(session, _ctx(inp)).by_run(
            inp.run_id, inp.ref_id
        )
        return None if row is None else (row.id, row.state, row.error_reason)


def _bundle(deps: SowDeps, inp: StageRef) -> Bundle:
    with deps.sessions() as session:
        row: KnowledgeVersion | None = KnowledgeVersionRepository(session, _ctx(inp)).get(
            inp.ref_id
        )
        ready = row is not None and row.state == KnowledgeVersionState.READY.value
        if row is None or not ready or not row.okf_bundle_key or not row.okf_sha256:
            raise ApplicationError(
                "knowledge version is not READY", type=KNOWLEDGE_NOT_READY, non_retryable=True
            )
        head = read_okf_part(deps.storage, row.okf_bundle_key)
        if hashlib.sha256(head).hexdigest() != row.okf_sha256:
            raise ApplicationError(
                "bundle.json does not match okf_sha256", type="HASH_MISMATCH", non_retryable=True
            )
        loaded = load(deps.storage, row)
    manifest = BundleManifest.model_validate_json(head)
    masters = manifest.parts.get("masters.jsonl")
    text = read_okf_part(deps.storage, masters.storage_key).decode("utf-8") if masters else ""
    return Bundle(list(loaded.chunks.values()), loaded.entities, loaded.relations, text)


def _record(deps: SowDeps, inp: StageRef, gen: Generation) -> uuid.UUID:
    with deps.sessions() as session:
        version = record_generation(session, _ctx(inp), gen, correlation_id=inp.correlation_id)
        session.commit()
        return version.id


def _failed(deps: SowDeps, inp: StageRef, kind: str, message: str) -> ApplicationError:
    gen = Generation(inp.run_id, inp.ref_id, None, None, error_reason=REASONS[kind])
    version_id = _record(deps, inp, gen)
    log.warning(
        "SOW generation failed",
        extra={"project_id": str(inp.project_id), "sow_version_id": str(version_id), "type": kind},
    )
    return ApplicationError(message, type=kind, non_retryable=True)


async def generate_version(
    deps: SowDeps, inp: StageRef, scope: LlmScope, *, hint: str | None = None
) -> uuid.UUID:
    """The SOW version of run `inp`; see the module docstring."""
    done = await asyncio.to_thread(_existing, deps, inp)
    if done is not None:
        version_id, state, reason = done
        if state == SowVersionState.GENERATION_FAILED.value:
            kind = next((k for k, v in REASONS.items() if v == reason), SCHEMA_INVALID)
            raise ApplicationError(f"already failed: {reason}", type=kind, non_retryable=True)
        return version_id

    bundle = await asyncio.to_thread(_bundle, deps, inp)
    context = await build_context(bundle, deps.embed, budget_tokens=deps.budget_tokens, hint=hint)
    try:
        result = await deps.llm.run(
            ALIAS,
            PROMPT_ID,
            SowDocument,
            context.blocks,
            scope,
            validate=check_citations(context),
            manifest_extra=context.manifest,
        )
    except SchemaInvalidAfterRetries as exc:
        raise await asyncio.to_thread(
            _failed, deps, inp, SCHEMA_INVALID, f"{len(exc.attempts)} answers failed the checks"
        ) from None
    except (ContextTooLarge, LlmRequestRejected) as exc:
        kind = type(exc).__name__
        raise await asyncio.to_thread(_failed, deps, inp, kind, str(exc)) from None
    except LlmTransportError:
        if scope.attempt < deps.attempts:
            raise
        raise await asyncio.to_thread(
            _failed, deps, inp, LLM_UNAVAILABLE, "Bifrost unavailable on the last attempt"
        ) from None

    structured = finalize(result.output, context)
    gen = Generation(inp.run_id, inp.ref_id, structured, result.llm_call_id)
    version_id = await asyncio.to_thread(_record, deps, inp, gen)
    log.info(
        "SOW generated",
        extra={
            "project_id": str(inp.project_id),
            "sow_version_id": str(version_id),
            "mode": context.mode,
            "modules": len(structured["modules"]),
            "features": len(structured["features"]),
            "llm_attempts": result.attempts,
        },
    )
    return version_id


# --- the activity ---------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _deps() -> SowDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values from the env
    sessions = mirror.sessions()

    async def embed_texts(texts: list[str]) -> list[list[float]]:
        return await embed(settings, texts)

    return SowDeps(
        sessions=sessions,
        storage=make_backend(settings),
        llm=LlmClient(settings, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(settings)),
        embed=embed_texts,
        budget_tokens=settings.llm_max_context_tokens - RESERVED_TOKENS,
    )


@activity_for(TaskQueue.LLM)
@activity.defn(name="generate_sow")
async def generate_sow(inp: SowGenerateInput) -> IdResult:
    """The SOW version id. Non-retryable failure type: SchemaInvalidAfterRetries."""
    info = activity.info()
    scope = LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=info.workflow_id or "",
        activity_id=info.activity_id,
        attempt=info.attempt,
    )
    ref = StageRef(**inp.model_dump(exclude={"hint"}))
    return IdResult(id=await generate_version(_deps(), ref, scope, hint=inp.hint))
