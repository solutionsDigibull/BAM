"""Test data endpoints' logic (E13.4, TD §8.8): read the Sub-Skill's dataset, and add, change or
remove a case.

Rules shared by every write:
* The Sub-Skill is looked up through the tenant- and project-scoped repository, so another tenant's,
  an unassigned project's or another Workflow's is 404. A certified, frozen or superseded Sub-Skill
  is 423 `RESOURCE_LOCKED` (the code the Sub-Skill edits already use).
* The dataset's `sha256` is the `ETag`: `If-Match` is required (428) and must match (412). Edits are
  serialised by locking the Sub-Skill row, so two writers cannot both pass the check.
* A write never changes a dataset: it writes a new version (the cases copied with the change) with
  its own hash and `edited_by_fde`, and keeps the previous one as evidence. A write that changes
  nothing writes nothing.
* Every case is validated before it is stored (`validate.case_errors`): 422 with the field paths.
"""

import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import JsonValue, ValidationError
from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.testgen import (
    MAX_CASES,
    MIN_CASES,
    CaseBody,
    CaseKind,
    CasePost,
    CasePut,
    TestCaseOut,
    TestDatasetOut,
    TestsResponse,
    dataset_sha256,
)
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, locked, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.edits import SUB_SKILL_LOCKED
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.testgen.repository import CaseRepository, DatasetRepository
from sbam_api.testgen.validate import case_errors, input_schema

HEX = set("0123456789abcdef")


def _correlation() -> str:
    return correlation_id_var.get() or "unknown"


def _unprocessable(errors: list[FieldError]) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, "Invalid request", errors=errors)


def files_hashes(keys: list[str]) -> dict[str, str]:
    """name -> sha256 of the dataset's files. Storage keys are content-addressed (the last segment
    is the object's sha256), so the hash is read from the key, never recomputed from bytes here."""
    out: dict[str, str] = {}
    for key in keys:
        tail = key.rsplit("/", 1)[-1]
        out[key] = tail if len(tail) == 64 and set(tail) <= HEX else tail.encode().hex()[:64]
    return out


def mocks_hashes(keys: list[str]) -> dict[str, str]:
    """sha256 -> sha256 of the dataset's mock definitions, read from their content-addressed keys.
    Keyed by the hash and not by the storage key, so the dataset hash does not depend on which
    tenant, project or Workflow folder the same definition sits in."""
    tails = (key.rsplit("/", 1)[-1] for key in keys)
    return {tail: tail for tail in tails}


def body_of(row: CaseRow) -> CaseBody:
    return CaseBody.model_validate(
        {
            "name": row.name,
            "input": row.input,
            "initial_state": row.initial_state,
            "state_script": row.state_script,
            "expected_terminal": row.expected_terminal,
            "expected_output": row.expected_output,
            "assertions": row.assertions,
        }
    )


def case_out(row: CaseRow) -> TestCaseOut:
    return TestCaseOut.model_validate(
        {**body_of(row).model_dump(mode="json"), "id": row.id, "kind": row.kind}
    )


def dataset_out(dataset: DatasetRow, cases: list[CaseRow]) -> TestDatasetOut:
    return TestDatasetOut(
        version=dataset.version,
        sha256=dataset.sha256,
        edited_by_fde=dataset.edited_by_fde,
        files_keys=list(dataset.files_keys),
        mocks_keys=list(dataset.mocks_keys),
        created_at=dataset.created_at,
        cases=[case_out(c) for c in cases],
    )


def _sub_skill(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_id: uuid.UUID
) -> SubSkill:
    sub: SubSkill | None = SubSkillRepository(session, ctx).get_for_update(sub_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    return sub


def get_tests(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_id: uuid.UUID
) -> TestsResponse:
    sub: SubSkill | None = SubSkillRepository(session, ctx).get(sub_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    dataset = DatasetRepository(session, ctx).latest(sub.id)
    if dataset is None:
        return TestsResponse()
    return TestsResponse(
        dataset=dataset_out(dataset, CaseRepository(session, ctx).of_dataset(dataset.id)),
        input_schema=_input_schema_or_none(sub.isatvon),
    )


def _input_schema_or_none(isatvon: Mapping[str, Any]) -> dict[str, JsonValue] | None:
    """The schema the panel checks an input against first (E13.7). An ISATVON it cannot be built
    from leaves the check to the server's 422, which reports the real problem."""
    try:
        return input_schema(isatvon)
    except ValidationError:
        return None


def _check_etag(if_match: str | None, sha: str) -> None:
    if not if_match:
        raise ApiError(
            428, ErrorCode.PRECONDITION_REQUIRED, "If-Match header required on this write"
        )
    if if_match.strip().removeprefix("W/").strip('"') != sha:
        raise ApiError(
            412, ErrorCode.PRECONDITION_FAILED, "Test data changed since you loaded it; reload"
        )


class Open:
    """The Sub-Skill, its newest dataset and that dataset's cases, locked for a write."""

    def __init__(
        self,
        session: Session,
        user: CurrentUser,
        project_id: uuid.UUID,
        workflow_id: uuid.UUID,
        sub_id: uuid.UUID,
        if_match: str | None,
    ) -> None:
        self.session = session
        self.user = user
        self.ctx = user.context(project_id)
        self.sub = _sub_skill(session, self.ctx, workflow_id, sub_id)
        if self.sub.state in SUB_SKILL_LOCKED:
            raise locked()
        dataset = DatasetRepository(session, self.ctx).latest(self.sub.id)
        if dataset is None:
            raise ApiError(
                409,
                ErrorCode.CONFLICT,
                "No test data yet",
                "The test data is generated when the Sub-Skill is built; edit it after that.",
            )
        _check_etag(if_match, dataset.sha256)
        self.dataset = dataset
        self.rows = CaseRepository(session, self.ctx).of_dataset(dataset.id)

    def pairs(self) -> list[tuple[CaseRow, CaseKind, CaseBody]]:
        return [(row, CaseKind(row.kind), body_of(row)) for row in self.rows]

    def isatvon(self) -> Mapping[str, Any]:
        isatvon: Mapping[str, Any] = self.sub.isatvon
        return isatvon

    def write_version(
        self, cases: list[tuple[CaseKind, CaseBody]], action: str, detail: dict[str, Any]
    ) -> tuple[DatasetRow, list[CaseRow]]:
        """The next version of the dataset with `cases`, and an audit row saying what changed."""
        keys = list(self.dataset.files_keys)
        mocks = list(self.dataset.mocks_keys)  # an edit to the cases leaves the mocks as they are
        new = DatasetRow(
            sub_skill_id=self.sub.id,
            version=self.dataset.version + 1,
            files_keys=keys,
            mocks_keys=mocks,
            sha256=dataset_sha256(cases, files_hashes(keys), mocks_hashes(mocks)),
            synthetic=True,
            edited_by_fde=True,
            generated_for=self.dataset.generated_for,  # an edit does not change the ISATVON
            created_by=str(self.user.user_id),
        )
        DatasetRepository(self.session, self.ctx).add(new)
        rows = [
            CaseRow(
                dataset_id=new.id,
                name=body.name,
                kind=kind.value,
                input=body.input,
                initial_state=body.initial_state,
                state_script=body.state_script,
                expected_terminal=body.expected_terminal.value,
                expected_output=body.expected_output,
                assertions=[a.model_dump(mode="json") for a in body.assertions],
                created_by=str(self.user.user_id),
            )
            for kind, body in cases
        ]
        cases_repo = CaseRepository(self.session, self.ctx)
        for row in rows:
            cases_repo.add(row)
        record(
            self.session,
            tenant_id=self.ctx.tenant_id,
            actor_id=str(self.user.user_id),
            action=action,
            entity_type="sub_skill",
            entity_id=str(self.sub.id),
            before={"version": self.dataset.version, "sha256": self.dataset.sha256},
            after={"version": new.version, "sha256": new.sha256, **detail},
            correlation_id=_correlation(),
        )
        return new, rows


def edit_case(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    case_id: uuid.UUID,
    put: CasePut,
    if_match: str | None,
) -> tuple[TestCaseOut, str]:
    """(the case after the edit, the dataset sha256 now: the new `ETag`)."""
    opened = Open(session, user, project_id, workflow_id, sub_id, if_match)
    pairs = opened.pairs()
    index = next((i for i, (row, _, _) in enumerate(pairs) if row.id == case_id), None)
    if index is None:
        raise not_found()
    row, kind, body = pairs[index]
    changes = {name: getattr(put, name) for name in put.model_fields_set}
    edited = body.model_copy(update=changes)
    errors = case_errors(opened.isatvon(), kind, CaseBody.model_validate(edited.model_dump()))
    if errors:
        raise _unprocessable(errors)
    if edited == body:  # nothing changed: no new version
        return case_out(row), opened.dataset.sha256
    cases = [(k, edited if i == index else b) for i, (_, k, b) in enumerate(pairs)]
    new, rows = opened.write_version(cases, "TEST_CASE_EDIT", {"case": body.name})
    return case_out(rows[index]), new.sha256


def add_case(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    post: CasePost,
    if_match: str | None,
) -> tuple[TestCaseOut, str]:
    opened = Open(session, user, project_id, workflow_id, sub_id, if_match)
    pairs = opened.pairs()
    body = CaseBody.model_validate(post.model_dump())
    errors = case_errors(opened.isatvon(), CaseKind.FDE, body)
    if body.name in {b.name for _, _, b in pairs}:
        errors.append(FieldError(path="/name", message="a case with this name already exists"))
    if errors:
        raise _unprocessable(errors)
    if len(pairs) >= MAX_CASES:
        raise ApiError(409, ErrorCode.CONFLICT, "Too many cases", f"At most {MAX_CASES} cases.")
    cases = [*((k, b) for _, k, b in pairs), (CaseKind.FDE, body)]
    new, rows = opened.write_version(cases, "TEST_CASE_ADD", {"case": body.name})
    return case_out(rows[-1]), new.sha256


def delete_case(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    case_id: uuid.UUID,
    if_match: str | None,
) -> str:
    """The dataset sha256 now. At least `MIN_CASES` cases must remain (409)."""
    opened = Open(session, user, project_id, workflow_id, sub_id, if_match)
    pairs = opened.pairs()
    gone = next(((row, b) for row, _, b in pairs if row.id == case_id), None)
    if gone is None:
        raise not_found()
    if len(pairs) - 1 < MIN_CASES:
        raise ApiError(
            409,
            ErrorCode.MIN_TEST_CASES,
            "Too few cases",
            f"A Sub-Skill needs at least {MIN_CASES} test cases.",
        )
    cases = [(k, b) for row, k, b in pairs if row.id != case_id]
    new, _ = opened.write_version(cases, "TEST_CASE_DELETE", {"case": gone[1].name})
    return new.sha256
