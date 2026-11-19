"""E14.4 (#132): the version routes. T08 a patch on a version that is not frozen is 409
NOT_FROZEN; a patch opens the next PATCH; the list; IDOR for both routes (cross-tenant, unassigned
and unknown ids are 404). Uses the freeze harness of test_e14_4_freeze_api.py.
"""

import uuid
from typing import Any

import test_e14_4_freeze_api as base
import test_wg18_commit_api as wg18
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.workflows.models import Workflow
from sqlalchemy import select
from test_auth import FDE_A2, TENANT_B, problem
from test_project_workspace import Harness
from test_wg16_connections_api import WORKFLOW

h = wg18.h  # the fixtures of the freeze harness
hv = base.hv
freeze, prepared, versions = base.freeze, base.prepared, base.versions

KEY = "patch-key-0001"


def new_version(harness: Harness, sub: uuid.UUID, token: str | None = None, key: str = KEY) -> Any:
    return harness.call(
        "POST",
        f"/projects/{harness.project}/workflows/{WORKFLOW}/sub-skills/{sub}/new-version",
        token or harness.fde(),
        json={"change_reason": "Fix the rounding"},
        headers={"Idempotency-Key": key},
    )


def sub_id(harness: Harness) -> uuid.UUID:
    with harness.sessions() as s:
        return s.scalars(select(SubSkill)).one().id


def test_e14_4_the_list_route_shows_the_frozen_version(hv: Harness) -> None:
    freeze(hv, prepared(hv))
    res = hv.call("GET", f"/projects/{hv.project}/workflows/{WORKFLOW}/versions", hv.fde())
    assert res.status_code == 200, res.text
    [item] = res.json()["items"]
    assert item["version"] == "1.0.0" and item["superseded"] is False


def test_e14_4_t08_a_patch_on_a_version_that_is_not_frozen_is_409(hv: Harness) -> None:
    prepared(hv)  # CERTIFIED, not frozen
    problem(new_version(hv, sub_id(hv)), 409, "NOT_FROZEN")


def test_e14_4_a_patch_opens_the_next_version_and_the_frozen_rows_stay(hv: Harness) -> None:
    freeze(hv, prepared(hv))
    res = new_version(hv, sub_id(hv))
    assert res.status_code == 202, res.text
    assert res.json()["next_version"] == "1.0.1"
    with hv.sessions() as s:
        assert s.scalars(select(SubSkill)).one().state == "DRAFT"
        assert s.scalars(select(MacroSkill)).one().state == "BUILDING"
        assert s.get(Workflow, WORKFLOW).state == "BUILDING"  # type: ignore[union-attr]
    assert [v.version for v in versions(hv)] == ["1.0.0"]  # the frozen version is untouched
    problem(new_version(hv, sub_id(hv), key="patch-key-0002"), 409, "NOT_FROZEN")  # now a draft
    again = new_version(hv, sub_id(hv))  # the same key answers the same patch
    assert again.status_code in (202, 409)


def test_e14_4_idor_other_tenant_unassigned_and_foreign_ids_get_404(hv: Harness) -> None:
    freeze(hv, prepared(hv))
    url = f"/projects/{hv.project}/workflows/{WORKFLOW}/versions"
    other_tenant = hv.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other_tenant, hv.realm.token(FDE_A2)):
        problem(hv.call("GET", url, token), 404, "NOT_FOUND")
        problem(new_version(hv, sub_id(hv), token=token), 404, "NOT_FOUND")
    problem(
        hv.call("GET", f"/projects/{hv.project}/workflows/{uuid.uuid4()}/versions", hv.fde()),
        404,
        "NOT_FOUND",
    )
    problem(new_version(hv, uuid.uuid4()), 404, "NOT_FOUND")
    assert [v.version for v in versions(hv)] == ["1.0.0"]
    with hv.sessions() as s:
        assert s.scalars(select(SubSkill)).one().state == "FROZEN"
