"""The `build_schema_sql` stage (E9.4, TD §12.6, §13): the Workflow's schema.sql and its
SCHEMA_SQL gate, once per build before the Sub-Skill builds.

1. Declared tables from every Sub-Skill of the Workflow; the base is the FDE's uploaded `.sql`
   (`workflow_schemas`, PENDING) if there is one, checked like any schema.
2. LLM DDL (`fragments.propose`) for the declared tables the base doesn't define, one call per
   Sub-Skill in skill_id order, each for the tables nobody defined yet.
3. `merge` (+ the platform guards), then `apply` to a throwaway database on `schema-check`.
4. The script is stored (content-addressed, `artifacts` SCHEMA_SQL) even when it failed, so the FDE
   sees what was checked; a `workflow_schemas` version and a Workflow-level `gate_results` row
   record the verdict.

Failed -> non-retryable `SchemaInvalid` (the pipeline notifies the FDE). `schema-check` down ->
retryable (T09, at most 2 attempts). Idempotent: the LLM answers are replayed by stage key, the
merge is deterministic and `record` returns the version a retried attempt already wrote, with its
gate row.
"""

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from sbam_api.artifacts.models import ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.models import GateResultRow
from sbam_api.schema_sql.repository import WorkflowSchemaRepository
from sbam_api.schema_sql.validate import Problem, Script, check, split
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.build_dashboard import Finding, GateName, GateStatus
from sbam_contracts.pipeline import IdResult, WorkflowRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.schema_sql import SchemaSource
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.schema_sql.apply import apply
from sbam_workers.build.schema_sql.fragments import ALIAS, PROMPT, Ask, DdlFragment, propose
from sbam_workers.build.schema_sql.merge import declared, merge
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

STAGE: Final = "build_schema_sql"
SCHEMA_INVALID: Final = "SchemaInvalid"
SQL_CONTENT_TYPE: Final = "application/sql"


@dataclass(frozen=True)
class Loaded:
    sub_skills: list[dict[str, Any]]  # stored ISATVON 1.4, in skill_id order
    base_sql: str | None
    base_document_id: uuid.UUID | None


@dataclass(frozen=True)
class Outcome:
    sql: str
    tables: list[str]
    problems: list[Problem]
    source: SchemaSource


class SqlSchemaStore:
    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    def _ctx(self, inp: WorkflowRef) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def load(self, inp: WorkflowRef) -> Loaded:
        with self.sessions() as session:
            ctx = self._ctx(inp)
            if WorkflowRepository(session, ctx).get(inp.workflow_id) is None:
                raise ApplicationError(
                    f"Workflow {inp.workflow_id} is not in this run", type="IdsMismatch",
                    non_retryable=True,
                )
            rows = SubSkillRepository(session, ctx).of_workflow(inp.workflow_id)
            subs = sorted(
                (dict(r.isatvon) for r in rows),
                key=lambda s: str(s.get("skill_metadata", {}).get("skill_id", "")),
            )
            base = WorkflowSchemaRepository(session, ctx).base(inp.workflow_id)
            key = None if base is None else base.storage_key
            document = None if base is None else base.source_document_id
        sql = None
        if key is not None:
            with self.storage.open(key) as stream:
                sql = stream.read().decode("utf-8")
        return Loaded(subs, sql, document)

    def save(self, inp: WorkflowRef, loaded: Loaded, outcome: Outcome) -> uuid.UUID:
        """Store the script, then the version + gate row in one transaction (once per script)."""
        data = outcome.sql.encode("utf-8")
        scope = StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id)
        stored = self.storage.put(data, scope, Kind.CODE, SQL_CONTENT_TYPE)
        status = GateStatus.FAILED if outcome.problems else GateStatus.PASSED
        findings = [
            _finding(p).model_dump(mode="json", exclude_none=True) for p in outcome.problems
        ]
        with self.sessions() as session:
            ctx = self._ctx(inp)
            ArtifactRepository(session, ctx).index(ArtifactKind.SCHEMA_SQL, stored, "sbam-worker")
            repo = WorkflowSchemaRepository(session, ctx)
            values = {
                "id": uuid.uuid4(),
                "tenant_id": inp.tenant_id,
                "project_id": inp.project_id,
                "workflow_id": inp.workflow_id,
                "source": outcome.source,
                "source_document_id": loaded.base_document_id,
                "storage_key": stored.storage_key,
                "sha256": stored.sha256,
                "tables": outcome.tables,
                "status": status,
                "findings": findings,
                "created_by": "sbam-worker",
            }
            row = repo.record(values)
            if row.id == values["id"]:  # new: its gate row goes with it
                session.add(
                    GateResultRow(
                        id=uuid.uuid4(),
                        tenant_id=inp.tenant_id,
                        project_id=inp.project_id,
                        workflow_id=inp.workflow_id,
                        gate=GateName.SCHEMA_SQL,
                        status=status,
                        findings=findings,
                        created_by="sbam-worker",
                    )
                )
            session.commit()
            return row.id


def _finding(problem: Problem) -> Finding:
    where = [] if problem.statement_index is None else [f"statement {problem.statement_index}"]
    return Finding(
        gate=GateName.SCHEMA_SQL,
        code=problem.code,
        message=problem.message[:10_000],
        file="schema.sql",
        expected=None if problem.table is None else problem.table,
        actual=", ".join(where) or None,
    )


async def build_schema(
    loaded: Loaded, ask_for: Mapping[str, Ask], check_db: Any
) -> Outcome:
    """Steps 1-3: `ask_for[skill_id]` asks the model, `check_db(statements, tables, handoff)`
    applies (tests pass fakes)."""
    want = declared(loaded.sub_skills)
    base: Script | None = None if loaded.base_sql is None else check(loaded.base_sql)
    source = SchemaSource.GENERATED if base is None else SchemaSource.UPLOADED
    if base is not None and not base.ok:  # an invalid base: nothing is generated or applied
        return Outcome(loaded.base_sql or "", sorted(want.tables), base.problems, source)

    defined = set(base.tables()) if base is not None else set()
    fragments: dict[str, Script] = {}
    problems: list[Problem] = []
    for sub in loaded.sub_skills:
        skill_id = str(sub.get("skill_metadata", {}).get("skill_id", ""))
        wanted = {t for t in want.of(skill_id) if t not in defined}
        if not wanted:
            continue
        handoff = {t: f for t, f in want.handoff.items() if t in wanted}
        script, failed = await propose(ask_for[skill_id], sub, wanted, handoff, defined)
        problems += failed
        if script is not None:
            fragments[skill_id] = script
            defined |= set(script.tables())
    merged = merge(want, base, fragments)
    problems += merged.problems
    if not problems and merged.sql:
        statements: Sequence[str] = list(split(merged.sql))
        handoff_tables = [t for t in want.handoff if t in merged.tables]
        problems += await asyncio.to_thread(check_db, statements, merged.tables, handoff_tables)
    return Outcome(merged.sql, merged.tables, problems, source)


# --- the activity ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaDeps:
    store: SqlSchemaStore
    llm: LlmClient
    dsn: str


def _isolated(dsn: str, app_url: str) -> str:
    """The schema-check DSN, refused when it points at SBAM's own database server."""
    check_url, app = make_url(dsn), make_url(app_url)
    if (check_url.host, check_url.port) == (app.host, app.port):
        raise RuntimeError("SCHEMA_CHECK_DSN must not point at SBAM's database server")
    return check_url.set(drivername="postgresql").render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def _deps() -> SchemaDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values from the env
    if settings.schema_check_dsn is None:
        raise RuntimeError("SCHEMA_CHECK_DSN is not set (the schema-check PostgreSQL)")
    sessions = mirror.sessions()
    return SchemaDeps(
        store=SqlSchemaStore(sessions, make_backend(settings)),
        llm=LlmClient(settings, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(settings)),
        dsn=_isolated(settings.schema_check_dsn.get_secret_value(), settings.database_url),
    )


def _ask(deps: SchemaDeps, inp: WorkflowRef, skill_id: str, temporal_workflow_id: str) -> Ask:
    # The replay key is the run + Sub-Skill, not the Temporal attempt: a retry re-reads the answer.
    scope = LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=temporal_workflow_id,
        activity_id=f"{STAGE}:{inp.run_id}:{inp.workflow_id}:{skill_id}",
    )

    async def ask(context: Mapping[str, str], validate: Any) -> DdlFragment:
        result = await deps.llm.run(
            ALIAS, PROMPT, DdlFragment, context, scope, validate=validate,
            subject={"workflow_id": str(inp.workflow_id), "skill_id": skill_id},
        )
        return result.output

    return ask


async def run_stage(deps: SchemaDeps, inp: WorkflowRef, temporal_workflow_id: str) -> IdResult:
    loaded = await asyncio.to_thread(deps.store.load, inp)
    ids = [str(s.get("skill_metadata", {}).get("skill_id", "")) for s in loaded.sub_skills]
    asks = {i: _ask(deps, inp, i, temporal_workflow_id) for i in ids}
    outcome = await build_schema(
        loaded, asks, lambda s, t, h: apply(deps.dsn, s, t, h)
    )
    schema_id = await asyncio.to_thread(deps.store.save, inp, loaded, outcome)
    if outcome.problems:
        codes = sorted({p.code.value for p in outcome.problems})
        raise ApplicationError(
            f"schema.sql failed SCHEMA_SQL ({', '.join(codes)}); workflow_schemas {schema_id}",
            str(schema_id),
            type=SCHEMA_INVALID,
            non_retryable=True,
        )
    return IdResult(id=schema_id)


@activity_for(TaskQueue.BUILD)
@activity.defn(name=STAGE)
async def build_schema_sql(inp: WorkflowRef) -> IdResult:
    """The Workflow's schema.sql + SCHEMA_SQL gate (TD §12.6); non-retryable SchemaInvalid."""
    return await run_stage(_deps(), inp, activity.info().workflow_id or "")
