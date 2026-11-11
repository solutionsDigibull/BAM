"""The `generate_tests` build stage (E13.4, TD §15.1): `sbam-testgen` writes the test data.

* The context is this Sub-Skill's ISATVON, its closed input schema, its tables and the allowed case
  kinds, each in a `<data>` block. Nothing else reaches the model: no client documents, no other
  Sub-Skill. The output type forbids extra keys and has no `synthetic`, `status` or `FDE` kind, so a
  reply cannot set a platform field.
* Every case is checked with the same rules the API applies to an FDE's edit
  (`sbam_api.testgen.validate.case_errors`: the input against `input_schema`, the seed-SQL
  allowlist, the 500-row budget, a terminal the kind can reach). A reply with a bad case is
  re-asked with the case index and the field path only, at most three times, and a case that never
  validates is never stored. `max_iterations = 1` removes `PASS_AFTER_RETRY` from the allowed kinds.
* One mock definition is written per API reference that names a route (`test/mocks.py`, E13.5),
  stored content-addressed like the files and hashed into the dataset: change a stub and the
  dataset's sha256 changes. A definition that could forward or record is refused before anything
  is stored.
* One file is written per FILE reference of the ISATVON (csv, json or xlsx, by the reference's
  extension, at most 5 MiB) and stored content-addressed. The dataset row (always `synthetic`,
  version one more than the Sub-Skill's newest) and its cases are inserted in one transaction; its
  sha256 is the shared `dataset_sha256` over the cases and the files' hashes.
* Idempotency: the stage key names the `llm_calls` replay key, so a retry after the model answered
  reuses that answer, and a dataset that already has the same hash is found, not written twice.
* Corrections (E11.4b): the newest dataset is reused when it was generated for the same bounds,
  input schema and tables (`fingerprint`), so a correction that leaves them alone leaves the tests
  alone; another fingerprint regenerates. An FDE-edited dataset is never overwritten: if its
  fingerprint no longer matches, the stage fails with `TestDataStale` and the Sub-Skill waits in
  review.
* Seed SQL fits `schema.sql` (#704): a case whose seed SQL breaks a column rule of the Workflow's
  latest PASSED `schema.sql` (NULL into NOT NULL, a NOT NULL column left out, an unknown column) is
  re-asked with the column and its rule, because the sandbox refuses the whole dataset for it. A
  generated dataset that the check refuses is not reused: it is asked again under a key that names
  it, so the replay cache cannot hand the same answer back. An FDE's dataset is left alone.
* Mocks follow the API references (#546): `fingerprint` does not cover `S.external_data`, so a
  reused dataset whose stored mocks differ from `mock_definitions(isatvon, its cases)` is written
  again as a new version with the same cases, files and FDE flag and the refreshed mocks. No model
  call; the older versions stay as evidence. Unchanged references write nothing.
"""

import csv
import hashlib
import io
import json
import re
import uuid
import zipfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from openpyxl import Workbook
from sbam_api.db.repository import TenantContext
from sbam_api.schema_sql.repository import WorkflowSchemaRepository
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.testgen import seed_fit
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.testgen.repository import CaseRepository, DatasetRepository
from sbam_api.testgen.service import body_of, files_hashes, mocks_hashes
from sbam_api.testgen.validate import case_errors, declared_tables, input_schema
from sbam_contracts import gate_expr
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import GateStatus
from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.pipeline import IdResult
from sbam_contracts.testgen import (
    MAX_FILE_BYTES,
    CaseBody,
    CaseKind,
    TestgenOutput,
    dataset_sha256,
)
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.llm.client import LlmClient, LlmResult
from sbam_workers.llm.store import LlmScope
from sbam_workers.test import seed_check
from sbam_workers.test.mocks import definition_bytes, definition_hash, mock_definitions

STAGE: Final = "generate_tests"
ALIAS: Final = "sbam-testgen"
PROMPT_ID: Final = "testgen"
FILE_FORMATS: Final = ("csv", "json", "xlsx")
SYSTEM_ACTOR: Final = "sbam-worker"
TEST_DATA_STALE: Final = "TestDataStale"  # error type: an FDE's tests no longer fit the bounds
FIXED_TIME: Final = datetime(2000, 1, 1, tzinfo=UTC)  # keeps an xlsx's bytes the same every run


def allowed_kinds(isatvon: Mapping[str, Any]) -> list[CaseKind]:
    """The kinds the model may use: `PASS_AFTER_RETRY` needs a second iteration to pass in."""
    bound = SubSkillModel.model_validate(isatvon).isatvon_definition.v_variables.max_iterations
    kinds = [k for k in CaseKind if k is not CaseKind.FDE]
    return [k for k in kinds if bound > 1 or k is not CaseKind.PASS_AFTER_RETRY]


def request_context(isatvon: Mapping[str, Any], schema_sql: str = "") -> dict[str, str]:
    """The `<data>` blocks of the request, from this Sub-Skill's ISATVON only."""
    model = SubSkillModel.model_validate(isatvon)
    limits = {
        "max_iterations": model.isatvon_definition.v_variables.max_iterations,
        "allowed_kinds": [k.value for k in allowed_kinds(isatvon)],
    }
    context = {
        "sub_skill": json.dumps(model.isatvon_definition.model_dump(mode="json"), sort_keys=True),
        "input_schema": json.dumps(input_schema(isatvon), sort_keys=True),
        "tables": json.dumps(declared_tables(isatvon)),
        "limits": json.dumps(limits, sort_keys=True),
    }
    if schema_sql:  # the real tables and columns the seed statements must use
        context["workflow_schema_sql"] = schema_sql
    return context


def fingerprint(isatvon: Mapping[str, Any]) -> str:
    """What the cases are validated against: the bound, the closed input schema, the tables. A
    correction that leaves these alone leaves the tests alone (E11.4b)."""
    context = request_context(isatvon)
    parts = {key: context[key] for key in ("input_schema", "tables", "limits")}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


INPUT_HINT: Final = (
    "every case input must satisfy input_schema; a kind whose condition cannot be reached with a"
    " valid input (e.g. a required input left null) is written as an EDGE case instead"
)


def _input_rule(schema: Mapping[str, Any], path: str) -> str:
    """What an `/input/<field>` path must be, from our own input schema: never the reply's value
    (#702). Without it the model is told only the path and repeats the same case."""
    field = path.removeprefix("/input/").split("/", 1)[0]
    props: Mapping[str, Any] = schema.get("properties") or {}
    if field not in props:
        return f"not an input of this Sub-Skill; inputs: {', '.join(sorted(props)) or 'none'}"
    kind = props[field].get("type")
    shown = "/".join(kind) if isinstance(kind, list) else str(kind or "value")
    if field in (schema.get("required") or []):
        return f"required {shown}; never null or missing"
    return f"{shown} when given"


def _no_op_fires(condition: str, inputs: Mapping[str, Any]) -> bool | None:
    """Whether no_op_condition holds for these inputs, or None when it reads anything but inputs
    (fresh state or outputs) or cannot be evaluated."""
    try:
        expr = gate_expr.parse(condition)
        names = gate_expr.identifiers(expr)
        if not names or not names <= set(inputs):
            return None
        return bool(gate_expr.evaluate(expr, dict(inputs), {}, {"max_iterations": 1}))
    except (gate_expr.ExpressionError, TypeError, ValueError):
        return None


def check_output(
    isatvon: Mapping[str, Any], out: TestgenOutput, schema_sql: str = ""
) -> tuple[str, ...]:
    """Error lines for a re-ask: the case index, the field path and, for an input, the rule from
    our input schema, for seed SQL the column rule of `schema.sql` (`tables`, #704), or a stable
    code, never a value. Empty when every case may be stored."""
    kinds = set(allowed_kinds(isatvon))
    schema = input_schema(isatvon)
    lines: list[str] = []
    bad_input = False
    for index, case in enumerate(out.cases):
        if case.kind not in kinds:
            lines.append(f"case {index}: kind {case.kind.value} is not allowed for this Sub-Skill")
        body = CaseBody.model_validate(case.model_dump(exclude={"kind"}))
        for e in case_errors(isatvon, case.kind, body):
            if e.path.startswith("/input/"):
                bad_input = True
                lines.append(f"case {index}: {e.path}: {_input_rule(schema, e.path)}")
            else:
                lines.append(f"case {index}: {e.path}")
        for path, reason in seed_fit.errors(case.initial_state, case.state_script, tables or {}):
            lines.append(f"case {index}: {path}: {reason}")
    if bad_input:
        lines.append(INPUT_HINT)
    # no_op_condition is checked on the input before any tool runs, so a case's kind must agree
    # with it: an ESCALATION case whose input already satisfies it ends NO_OP and never escalates.
    # Only conditions that read inputs alone can be judged here; anything else is left to the run.
    no_op = SubSkillModel.model_validate(isatvon).isatvon_definition.v_variables.no_op_condition
    for index, case in enumerate(out.cases):
        fired = _no_op_fires(no_op, case.input)
        if fired is None or case.kind is CaseKind.EDGE:
            continue
        if fired and case.kind is not CaseKind.NO_OP:
            lines.append(
                f"case {index}: /input already satisfies no_op_condition, so the loop ends NO_OP"
                f" before any tool runs: use an input that does not (kind {case.kind.value})"
            )
        elif not fired and case.kind is CaseKind.NO_OP:
            lines.append(
                f"case {index}: /input does not satisfy no_op_condition, so it is not a NO_OP case"
            )
    # A step runs before pass i + 2, so only max_iterations - 1 steps can ever run; one more ended
    # the whole run in the VM as "state_script keys [4] are not passes 1..3" (DATASET_INVALID).
    limit = SubSkillModel.model_validate(isatvon).isatvon_definition.v_variables.max_iterations
    for index, case in enumerate(out.cases):
        if len(case.state_script) > limit - 1:
            lines.append(
                f"case {index}: /state_script has {len(case.state_script)} steps,"
                f" at most {limit - 1} (max_iterations {limit} minus the first pass)"
            )
    # The runner counts the handoff table's rows and expects exactly one new row per pass, so a
    # seed that writes to it ended the case with "pass 1 left 2 handoff rows".
    handoff = SubSkillModel.model_validate(
        isatvon
    ).isatvon_definition.o_outcome.state_handoff_file.table.lower()
    for index, case in enumerate(out.cases):
        for n, statement in enumerate(
            [*case.initial_state, *(s for st in case.state_script for s in st)], 1
        ):
            if re.search(rf"\b(?:into|update)\s+\"?{re.escape(handoff)}\"?\b", statement, re.I):
                lines.append(
                    f"case {index}: statement {n}: do not seed {handoff}, it is the handoff log"
                    " the platform counts (seed other tables or nothing)"
                )
    if schema_sql:  # seeds naming a column the schema lacks end the run in the VM (DATASET_INVALID)
        for index, case in enumerate(out.cases):
            seeds = [*case.initial_state, *(s for step in case.state_script for s in step)]
            lines += [f"case {index}: {p}" for p in seed_check.problems(schema_sql, seeds)]
    return tuple(lines)


async def generate(
    client: LlmClient, scope: LlmScope, isatvon: Mapping[str, Any], schema_sql: str = ""
) -> LlmResult[TestgenOutput]:
    return await client.run(
        ALIAS,
        PROMPT_ID,
        TestgenOutput,
        request_context(isatvon, schema_sql),
        scope,
        validate=lambda out: check_output(isatvon, out, schema_sql),
    )


# --- files for the FILE references ---------------------------------------------------------------


@dataclass(frozen=True)
class DataFile:
    name: str
    content: bytes
    content_type: str


def _extension(ref: str) -> str:
    return ref.rsplit(".", 1)[-1].lower() if "." in ref else ""


def _rows(columns: Sequence[str]) -> list[list[str]]:
    return [list(columns), [f"sample-{i + 1}" for i in range(len(columns))]]


_MODIFIED: Final = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")


def _pinned_xlsx(raw: bytes) -> bytes:
    """The same workbook with no wall-clock time in it: openpyxl stamps `modified` with the save
    time and every zip entry with the current time, so two saves a second apart differ (#642)."""
    stamp = FIXED_TIME.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    sink = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(sink, "w") as target:
        for item in source.infolist():
            body = source.read(item.filename)
            if item.filename == "docProps/core.xml":
                body = _MODIFIED.sub(rb"\g<1>" + stamp + rb"\g<2>", body)
            pinned = zipfile.ZipInfo(item.filename, date_time=FIXED_TIME.timetuple()[:6])
            pinned.compress_type = zipfile.ZIP_DEFLATED
            pinned.external_attr = item.external_attr
            target.writestr(pinned, body)
    return sink.getvalue()


def data_files(isatvon: Mapping[str, Any]) -> list[DataFile]:
    """One synthetic file per FILE reference, with the Sub-Skill's declared inputs as columns."""
    definition = SubSkillModel.model_validate(isatvon).isatvon_definition
    columns = definition.s_source.required_inputs
    files: list[DataFile] = []
    for ref in definition.s_source.external_data or []:
        extension = _extension(ref.ref)
        if ref.kind.value != "FILE" or extension not in FILE_FORMATS:
            continue
        name = f"{ref.name}.{extension}"
        if extension == "csv":
            sink = io.StringIO()
            csv.writer(sink, lineterminator="\n").writerows(_rows(columns))
            data, kind = sink.getvalue().encode(), "text/csv"
        elif extension == "json":
            records = [dict(zip(columns, _rows(columns)[1], strict=True))]
            data, kind = json.dumps(records, sort_keys=True).encode(), "application/json"
        else:
            book = Workbook()
            book.properties.created = book.properties.modified = FIXED_TIME
            sheet = book.active or book.create_sheet()
            for row in _rows(columns):
                sheet.append(row)
            sink_b = io.BytesIO()
            book.save(sink_b)
            data = _pinned_xlsx(sink_b.getvalue())
            kind = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if len(data) <= MAX_FILE_BYTES:
            files.append(DataFile(name, data, kind))
    return files


# --- storage --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Newest:
    """The newest dataset: its id, what it was generated for, whether an FDE edited it."""

    id: uuid.UUID
    generated_for: str | None
    edited_by_fde: bool


class SqlDatasetStore:
    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    @staticmethod
    def _ctx(inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def newest(self, inp: BuildStageInput) -> Newest | None:
        with self.sessions() as session:
            latest = DatasetRepository(session, self._ctx(inp)).latest(inp.sub_skill_id)
            if latest is None:
                return None
            return Newest(latest.id, latest.generated_for, latest.edited_by_fde)

    def _store_mocks(self, inp: BuildStageInput, mocks: Sequence[Mapping[str, Any]]) -> list[str]:
        # Linted first: a definition that could forward or record raises here, before anything is
        # stored, so the dataset is never created (E13.5-T06).
        stored = [definition_bytes(m) for m in mocks]
        scope = StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id)
        return [
            self.storage.put(io.BytesIO(data), scope, Kind.TESTS, "application/json").storage_key
            for data in stored
        ]

    def fits(self, inp: BuildStageInput, dataset_id: uuid.UUID, tables: seed_fit.Tables) -> bool:
        """Whether every case of the dataset seeds rows `schema.sql` accepts (#704)."""
        with self.sessions() as session:
            rows = CaseRepository(session, self._ctx(inp)).of_dataset(dataset_id)
            return not any(seed_fit.errors(r.initial_state, r.state_script, tables) for r in rows)

    def refresh_mocks(self, inp: BuildStageInput, isatvon: Mapping[str, Any]) -> uuid.UUID:
        """The newest dataset, or a new version of it whose mocks answer the current API
        references (#546). Cases, files, `edited_by_fde` and `generated_for` are carried over."""
        with self.sessions() as session:
            latest = DatasetRepository(session, self._ctx(inp)).latest(inp.sub_skill_id)
            if latest is None:
                raise RuntimeError("refresh_mocks needs a dataset")
            rows = CaseRepository(session, self._ctx(inp)).of_dataset(latest.id)
            cases = [(CaseKind(row.kind), body_of(row)) for row in rows]
            latest_id, files_keys, mocks_keys = latest.id, latest.files_keys, latest.mocks_keys
            edited, generated_for = latest.edited_by_fde, latest.generated_for
        mocks = [d.model_dump(mode="json") for d in mock_definitions(isatvon, cases)]
        if sorted(definition_hash(m) for m in mocks) == sorted(mocks_hashes(mocks_keys)):
            return latest_id  # the references did not move: same dataset, same hash
        return self._write(
            inp, cases, files_keys, self._store_mocks(inp, mocks), generated_for, edited
        )

    def record(
        self,
        inp: BuildStageInput,
        cases: Sequence[tuple[CaseKind, CaseBody]],
        files: list[DataFile],
        generated_for: str,
        mocks: Sequence[Mapping[str, Any]] = (),
    ) -> uuid.UUID:
        mock_keys = self._store_mocks(inp, mocks)
        scope = StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id)
        keys = [
            self.storage.put(io.BytesIO(f.content), scope, Kind.TESTS, f.content_type).storage_key
            for f in files
        ]
        return self._write(inp, cases, keys, mock_keys, generated_for, edited_by_fde=False)

    def _write(
        self,
        inp: BuildStageInput,
        cases: Sequence[tuple[CaseKind, CaseBody]],
        keys: list[str],
        mock_keys: list[str],
        generated_for: str | None,
        edited_by_fde: bool,
    ) -> uuid.UUID:
        sha = dataset_sha256(cases, files_hashes(keys), mocks_hashes(mock_keys))
        with self.sessions() as session:
            repo = DatasetRepository(session, self._ctx(inp))
            latest = repo.latest(inp.sub_skill_id)
            if latest is not None and latest.sha256 == sha:  # a retry, or identical data
                return latest.id
            dataset = DatasetRow(
                sub_skill_id=inp.sub_skill_id,
                version=1 if latest is None else latest.version + 1,
                files_keys=keys,
                mocks_keys=mock_keys,
                sha256=sha,
                synthetic=True,
                edited_by_fde=edited_by_fde,
                generated_for=generated_for,
                created_by=SYSTEM_ACTOR,
            )
            repo.add(dataset)
            cases_repo = CaseRepository(session, self._ctx(inp))
            for kind, case in cases:
                cases_repo.add(
                    CaseRow(
                        dataset_id=dataset.id,
                        name=case.name,
                        kind=kind.value,
                        input=case.input,
                        initial_state=case.initial_state,
                        state_script=case.state_script,
                        expected_terminal=case.expected_terminal.value,
                        expected_output=case.expected_output,
                        assertions=[a.model_dump(mode="json") for a in case.assertions],
                        created_by=SYSTEM_ACTOR,
                    )
                )
            session.commit()
            return dataset.id


class SqlSchemaSource:
    """The Workflow's latest PASSED `schema.sql`, the one the sandbox builds its database from."""

    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    def __call__(self, inp: BuildStageInput) -> str | None:
        with self.sessions() as session:
            row = WorkflowSchemaRepository(
                session, TenantContext(inp.tenant_id, inp.project_id)
            ).latest(inp.workflow_id, status=GateStatus.PASSED)
            key = row.storage_key if row is not None else None
        return self.storage.get(key).decode("utf-8") if key else None


def stage_key(inp: BuildStageInput) -> str:
    return f"{STAGE}:{inp.sub_skill_id}:{inp.build_no}:{inp.cycle}:{inp.attempt}"


def stage_scope(
    inp: BuildStageInput, temporal_workflow_id: str, replaces: uuid.UUID | None = None
) -> LlmScope:
    """`replaces` names a refused dataset (#704): its own key, so the replay cache cannot answer
    with the cases that dataset was made from."""
    key = stage_key(inp) if replaces is None else f"{stage_key(inp)}:replaces:{replaces}"
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=temporal_workflow_id,
        activity_id=key,
    )


class GenerateTests:
    """`generate_tests` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        isatvon_of: Callable[[BuildStageInput], Awaitable[Mapping[str, Any]]],
        client: LlmClient,
        store: SqlDatasetStore,
        schema_of: Callable[[BuildStageInput], Awaitable[str]] | None = None,
    ) -> None:
        self.isatvon_of = isatvon_of
        self.client = client
        self.store = store
        self.schema_of = schema_of

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        isatvon = await self.isatvon_of(inp)
        wanted = fingerprint(isatvon)
        schema = self.schema_of(inp)
        tables = seed_fit.tables_of(schema) if schema else {}
        newest = self.store.newest(inp)
        replaces: uuid.UUID | None = None
        if newest is not None and newest.generated_for == wanted:
            if newest.edited_by_fde or self.store.fits(inp, newest.id, tables):
                # same bounds, same tables: the cases stay, the mocks follow the API references
                return IdResult(id=self.store.refresh_mocks(inp, isatvon))
            replaces = newest.id  # generated cases the sandbox would refuse: ask again (#704)
        elif newest is not None and newest.edited_by_fde:
            if newest.generated_for is None:  # written before the fingerprint: keep their cases
                return IdResult(id=self.store.refresh_mocks(inp, isatvon))
            raise ApplicationError(
                "the FDE's edited tests were written for other bounds",
                type=TEST_DATA_STALE,
                non_retryable=True,
            )
        workflow_id = activity.info().workflow_id
        if workflow_id is None:
            raise RuntimeError(f"{STAGE} runs only inside a workflow")
        schema_sql = await self.schema_of(inp) if self.schema_of else ""
        result = await generate(self.client, stage_scope(inp, workflow_id), isatvon, schema_sql)
        cases = [
            (c.kind, CaseBody.model_validate(c.model_dump(exclude={"kind"})))
            for c in result.output.cases
        ]
        mocks = [
            definition.model_dump(mode="json") for definition in mock_definitions(isatvon, cases)
        ]
        return IdResult(id=self.store.record(inp, cases, data_files(isatvon), wanted, mocks))
