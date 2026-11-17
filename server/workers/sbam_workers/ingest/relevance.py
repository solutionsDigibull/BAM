"""How useful a parsed document is for the project's goal (#802): the `score_relevance` and
`parsed_documents` activities.

`score_version` is the whole step and takes its dependencies as arguments, so tests run it with a
fake model, a temp-dir storage and SQLite; the activities only build the real ones. For one document
version:
  * it must be PARSED with a CLEAN scan and have a `doc.md`, and the project must have a goal;
    otherwise nothing is asked and nothing is stored (False);
  * a version already scored against this goal (same `goal_sha256`) is not asked again (True);
  * the model (`sbam-relevance`, prompt `score-relevance`) gets the goal, the file name and the
    start of `doc.md` (at most `MAX_DOCUMENT_CHARS`), and answers a `RelevanceAnswer`. An answer
    that never fits the schema, or a request the provider refuses, leaves it unscored (False);
  * the answer is stored on the version as a `DocumentRelevance` with the goal's sha256.
It is advice only: nothing here changes the version's state, and a transport error only fails this
activity (the workflow catches it). The document text is data; only the level, score and reason are
taken from the answer. Nothing here logs document text, file names or the goal.
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Final, Protocol

import sbam_api.ingest.models  # noqa: F401 - registers DocumentVersion for the state machine
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.projects.models import Project
from sbam_api.projects.repository import ProjectRepository
from sbam_api.storage.factory import make_backend
from sbam_contracts.documents import DocumentRelevance, RelevanceAnswer, ScanStatus
from sbam_contracts.pipeline import IdsResult, StageRef
from sbam_contracts.runs import RunInput, TaskQueue
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy.orm import sessionmaker
from temporalio import activity
from temporalio.common import RetryPolicy

from sbam_workers import mirror
from sbam_workers.llm.client import ContextTooLarge, LlmClient, LlmRequestRejected, LlmResult
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.relevance")

ALIAS: Final = "sbam-relevance"
PROMPT: Final = "score-relevance"
MAX_DOCUMENT_CHARS: Final = 16_000  # the start of doc.md: enough to tell what a document is
# One document's scoring, from either workflow: a short call, retried once (advice only).
SCORE_TIMEOUT: Final = timedelta(minutes=2)
SCORE_RETRY: Final = RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=5))


class Scorer(Protocol):
    """The part of `LlmClient` this step uses (a fake in the tests)."""

    async def run(
        self,
        alias: str,
        prompt_id: str,
        output_type: type[RelevanceAnswer],
        context: Mapping[str, str],
        scope: LlmScope,
        *,
        validate: Callable[[RelevanceAnswer], tuple[str, ...]] | None = None,
        subject: Mapping[str, str] | None = None,
        manifest_extra: Mapping[str, Any] | None = None,
    ) -> LlmResult[RelevanceAnswer]: ...


@dataclass(frozen=True)
class RelevanceDeps:
    sessions: sessionmaker[Any]
    storage: Any
    scorer: Scorer
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


@dataclass(frozen=True)
class ScoreRun:
    """Which activity run this is: the replay-cache key names it, never the attempt."""

    workflow_id: str
    activity_id: str
    run_id: str = ""


@dataclass(frozen=True)
class Target:
    """What scoring needs from a version that may be scored."""

    md_key: str
    title: str
    goal: str
    current: dict[str, Any] | None


def goal_sha256(goal: str) -> str:
    return hashlib.sha256(goal.encode()).hexdigest()


def _ctx(inp: RunInput) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _target(deps: RelevanceDeps, inp: StageRef) -> Target | None:
    with deps.sessions() as session:
        version: DocumentVersion | None = DocumentVersionRepository(session, _ctx(inp)).get(
            inp.ref_id
        )
        if (
            version is None
            or version.state != S.PARSED.value
            or version.scan_status != ScanStatus.CLEAN.value
            or not version.derived_md_key
        ):
            return None
        project: Project | None = ProjectRepository(session, _ctx(inp)).get(inp.project_id)
        if project is None or not project.goal:
            return None
        document = session.get(Document, version.document_id)
        return Target(
            md_key=version.derived_md_key,
            title=document.title if document is not None else "",
            goal=project.goal,
            current=dict(version.relevance) if version.relevance else None,
        )


def _scope(inp: StageRef, run: ScoreRun) -> LlmScope:
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=f"{run.workflow_id}:{run.run_id}",
        activity_id=f"{run.activity_id}:relevance-{inp.ref_id}",
        attempt=1,  # the replay-cache key stays the same for every attempt of this run
    )


def _text(deps: RelevanceDeps, key: str) -> str:
    raw: bytes = deps.storage.get(key)
    return raw.decode("utf-8", errors="replace")[:MAX_DOCUMENT_CHARS]


def _store(deps: RelevanceDeps, inp: StageRef, relevance: DocumentRelevance) -> None:
    with deps.sessions() as session:
        version: DocumentVersion | None = DocumentVersionRepository(session, _ctx(inp)).get(
            inp.ref_id
        )
        if version is None:
            return  # deleted meanwhile: nothing to keep
        version.relevance = relevance.model_dump(mode="json")
        session.commit()


async def score_version(deps: RelevanceDeps, inp: StageRef, run: ScoreRun) -> bool:
    """True when the version is scored against the project's current goal; see the module doc."""
    target = await asyncio.to_thread(_target, deps, inp)
    if target is None:
        return False
    sha = goal_sha256(target.goal)
    if target.current is not None and target.current.get("goal_sha256") == sha:
        return True  # already scored against this goal
    text = await asyncio.to_thread(_text, deps, target.md_key)
    if not text.strip():
        return False
    try:
        result = await deps.scorer.run(
            ALIAS,
            PROMPT,
            RelevanceAnswer,
            {"goal": target.goal, "title": target.title, "document": text},
            _scope(inp, run),
            subject={"document_version_id": str(inp.ref_id)},
        )
    except (SchemaInvalidAfterRetries, LlmRequestRejected, ContextTooLarge) as exc:
        log.warning(
            "relevance not scored",
            extra={"document_version_id": str(inp.ref_id), "exc_type": type(exc).__name__},
        )
        return False
    answer = result.output
    relevance = DocumentRelevance(**answer.model_dump(), goal_sha256=sha, scored_at=deps.clock())
    await asyncio.to_thread(_store, deps, inp, relevance)
    return True


def parsed_ids(deps: RelevanceDeps, inp: RunInput) -> list[uuid.UUID]:
    """The project's active versions that are PARSED with a CLEAN scan, oldest first."""
    with deps.sessions() as session:
        repo = DocumentVersionRepository(session, _ctx(inp))
        active: Sequence[uuid.UUID] = repo.active_ids()
        ready = repo.parsed_clean_ids(active)
        return [i for i in active if i in ready]


@lru_cache(maxsize=1)
def _deps() -> RelevanceDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    sessions = mirror.sessions()
    scorer = LlmClient(settings, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(settings))
    return RelevanceDeps(sessions=sessions, storage=make_backend(settings), scorer=scorer)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="score_relevance")
async def score_relevance(inp: StageRef) -> IdsResult:
    info = activity.info()
    run = ScoreRun(info.workflow_id or "", info.activity_id, info.workflow_run_id or "")
    scored = await score_version(_deps(), inp, run)
    return IdsResult(ids=[inp.ref_id] if scored else [])


@activity_for(TaskQueue.INGEST)
@activity.defn(name="parsed_documents")
def parsed_documents(inp: RunInput) -> IdsResult:
    return IdsResult(ids=parsed_ids(_deps(), inp))
