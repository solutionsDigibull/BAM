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
* Mocks follow the API references (#546): `fingerprint` does not cover `S.external_data`, so a
  reused dataset whose stored mocks differ from `mock_definitions(isatvon, its cases)` is written
  again as a new version with the same cases, files and FDE flag and the refreshed mocks. No model
  call; the older versions stay as evidence. Unchanged references write nothing.
"""

import csv
import hashlib
import io
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from openpyxl import Workbook
from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.testgen.repository import CaseRepository, DatasetRepository
from sbam_api.testgen.service import body_of, files_hashes, mocks_hashes
from sbam_api.testgen.validate import case_errors, declared_tables, input_schema
from sbam_contracts.build import BuildStageInput
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


def request_context(isatvon: Mapping[str, Any]) -> dict[str, str]:
    """The `<data>` blocks of the request, from this Sub-Skill's ISATVON only."""
    model = SubSkillModel.model_validate(isatvon)
    limits = {
        "max_iterations": model.isatvon_definition.v_variables.max_iterations,
        "allowed_kinds": [k.value for k in allowed_kinds(isatvon)],
    }
    return {
        "sub_skill": json.dumps(model.isatvon_definition.model_dump(mode="json"), sort_keys=True),
        "input_schema": json.dumps(input_schema(isatvon), sort_keys=True),
        "tables": json.dumps(declared_tables(isatvon)),
        "limits": json.dumps(limits, sort_keys=True),
    }


def fingerprint(isatvon: Mapping[str, Any]) -> str:
    """What the cases are validated against: the bound, the closed input schema, the tables. A
    correction that leaves these alone leaves the tests alone (E11.4b)."""
    context = request_context(isatvon)
    parts = {key: context[key] for key in ("input_schema", "tables", "limits")}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def check_output(isatvon: Mapping[str, Any], out: TestgenOutput) -> tuple[str, ...]:
    """Error lines for a re-ask: the case index and the field path or a stable code, never a
    value. Empty when every case may be stored."""
    kinds = set(allowed_kinds(isatvon))
    lines: list[str] = []
    for index, case in enumerate(out.cases):
        if case.kind not in kinds:
            lines.append(f"case {index}: kind {case.kind.value} is not allowed for this Sub-Skill")
        body = CaseBody.model_validate(case.model_dump(exclude={"kind"}))
        lines += [f"case {index}: {e.path}" for e in case_errors(isatvon, case.kind, body)]
    return tuple(lines)


async def generate(
    client: LlmClient, scope: LlmScope, isatvon: Mapping[str, Any]
) -> LlmResult[TestgenOutput]:
    return await client.run(
        ALIAS,
        PROMPT_ID,
        TestgenOutput,
        request_context(isatvon),
        scope,
        validate=lambda out: check_output(isatvon, out),
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
            data = sink_b.getvalue()
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


def stage_key(inp: BuildStageInput) -> str:
    return f"{STAGE}:{inp.sub_skill_id}:{inp.build_no}:{inp.cycle}:{inp.attempt}"


def stage_scope(inp: BuildStageInput, temporal_workflow_id: str) -> LlmScope:
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=temporal_workflow_id,
        activity_id=stage_key(inp),
    )


class GenerateTests:
    """`generate_tests` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        isatvon_of: Callable[[BuildStageInput], Awaitable[Mapping[str, Any]]],
        client: LlmClient,
        store: SqlDatasetStore,
    ) -> None:
        self.isatvon_of = isatvon_of
        self.client = client
        self.store = store

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        isatvon = await self.isatvon_of(inp)
        wanted = fingerprint(isatvon)
        newest = self.store.newest(inp)
        if newest is not None and newest.generated_for == wanted:
            # same bounds, same tables: the cases stay, the mocks follow the API references
            return IdResult(id=self.store.refresh_mocks(inp, isatvon))
        if newest is not None and newest.edited_by_fde:
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
        result = await generate(self.client, stage_scope(inp, workflow_id), isatvon)
        cases = [
            (c.kind, CaseBody.model_validate(c.model_dump(exclude={"kind"})))
            for c in result.output.cases
        ]
        mocks = [
            definition.model_dump(mode="json") for definition in mock_definitions(isatvon, cases)
        ]
        return IdResult(id=self.store.record(inp, cases, data_files(isatvon), wanted, mocks))
