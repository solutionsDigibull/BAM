"""Test data endpoints (E13.4, TD §8.8). FDE portal; see `testgen/service.py` for the rules.

* `GET W/sub-skills/{sub_id}/tests`: the newest dataset with its cases (`TestsResponse`); `ETag` is
  the dataset's `sha256`. No dataset yet: `{"dataset": null}`.
* `PUT W/sub-skills/{sub_id}/tests/cases/{case_id}` (`CasePut`, `If-Match`): change a case.
* `POST W/sub-skills/{sub_id}/tests/cases` (`CasePost`, `If-Match`): add a case (kind `FDE`), 201.
* `DELETE W/sub-skills/{sub_id}/tests/cases/{case_id}` (`If-Match`): 204; 409 when fewer than three
  cases would remain.

Every write makes a new dataset version and answers its `sha256` as the new `ETag`. 422 names the
field (`/input/amount`); 423 a certified or frozen Sub-Skill; 428 / 412 for `If-Match`; 404 another
tenant's, an unassigned project's or another Workflow's Sub-Skill.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from sbam_contracts.testgen import CasePost, CasePut, TestCaseOut, TestsResponse
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.projects.guard import active_project
from sbam_api.temporal.deps import session_factory
from sbam_api.testgen import service

router = APIRouter(tags=["testgen"])

W = "/projects/{project_id}/workflows/{workflow_id}"
S = W + "/sub-skills/{sub_id}/tests"
Project = Annotated[UUID, Depends(active_project)]  # assigned FDE; archived: writes 409
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
IfMatch = Annotated[str | None, Header()]


def _etag(sha: str) -> str:
    return f'"{sha}"'


@router.get(S)
def get_tests(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    user: User,
    sessions: Sessions,
    response: Response,
) -> TestsResponse:
    with sessions() as session:
        tests = service.get_tests(session, user.context(project_id), workflow_id, sub_id)
        if tests.dataset is not None:
            response.headers["ETag"] = _etag(tests.dataset.sha256)
        return tests


@router.put(S + "/cases/{case_id}")
def put_case(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    case_id: UUID,
    body: CasePut,
    user: User,
    sessions: Sessions,
    response: Response,
    if_match: IfMatch = None,
) -> TestCaseOut:
    with sessions() as session:
        case, sha = service.edit_case(
            session, user, project_id, workflow_id, sub_id, case_id, body, if_match
        )
        session.commit()
    response.headers["ETag"] = _etag(sha)
    return case


@router.post(S + "/cases", status_code=201)
def post_case(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    body: CasePost,
    user: User,
    sessions: Sessions,
    response: Response,
    if_match: IfMatch = None,
) -> TestCaseOut:
    with sessions() as session:
        case, sha = service.add_case(session, user, project_id, workflow_id, sub_id, body, if_match)
        session.commit()
    response.headers["ETag"] = _etag(sha)
    return case


@router.delete(S + "/cases/{case_id}", status_code=204)
def delete_case(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    case_id: UUID,
    user: User,
    sessions: Sessions,
    if_match: IfMatch = None,
) -> Response:
    with sessions() as session:
        sha = service.delete_case(session, user, project_id, workflow_id, sub_id, case_id, if_match)
        session.commit()
    return Response(status_code=204, headers={"ETag": _etag(sha)})
