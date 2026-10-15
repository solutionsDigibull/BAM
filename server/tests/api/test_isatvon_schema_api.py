"""E8.6 API: GET /schemas/isatvon/{version} through the real app and the real auth chain."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sbam_api.core.auth import Auth, TokenVerifier
from sbam_api.main import create_app
from test_auth import AUDIENCE, ISSUER, FakeJwks, Realm, problem
from test_core_app import ALL_OK, make_settings

URL = "/api/v1/schemas/isatvon/1.4"
GROUPS = ["Metadata", "I", "S", "A", "T", "V", "O", "N", "Status"]


class Api:
    def __init__(self, tmp_path: Path) -> None:
        self.realm = Realm()
        auth = Auth(TokenVerifier(ISSUER, AUDIENCE, FakeJwks(self.realm)))
        self.client = TestClient(create_app(make_settings(tmp_path), ALL_OK, auth))

    def get(self, url: str = URL, **headers: str) -> Any:
        return self.client.get(
            url, headers={"Authorization": f"Bearer {self.realm.token()}", **headers}
        )


@pytest.fixture
def api(tmp_path: Path) -> Api:
    return Api(tmp_path)


def test_e8_6_t02_get_1_4_has_etag_and_the_groups_in_order(api: Api) -> None:
    res = api.get()
    assert res.status_code == 200
    assert res.headers["etag"].startswith('"')
    assert "max-age" in res.headers["cache-control"]
    body = res.json()
    assert body["version"] == "1.4"
    assert [g["header"] for g in body["grid"]["column_groups"]] == GROUPS
    assert body["json_schema"]["title"] == "SubSkill"
    column = body["grid"]["column_groups"][5]["children"][0]
    assert column["field_path"] == "isatvon_definition.v_variables.max_iterations"
    assert (column["editor"], column["min"], column["max"]) == ("number", 1, 10)
    assert "enum" not in column  # absent, not null


def test_e8_6_t03_unknown_version_is_404_problem_json(api: Api) -> None:
    for version in ("9.9", "1.5", "1.4.0", "latest"):
        res = api.get(f"/api/v1/schemas/isatvon/{version}")
        problem(res, 404, "NOT_FOUND")


def test_e8_6_t04_conditional_get_is_304_without_a_body(api: Api) -> None:
    etag = api.get().headers["etag"]
    for header in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        res = api.get(**{"If-None-Match": header})
        assert res.status_code == 304, header
        assert res.content == b""
        assert res.headers["etag"] == etag
    assert api.get(**{"If-None-Match": '"stale"'}).status_code == 200


def test_e8_6_t07_unauthenticated_is_401(api: Api) -> None:
    problem(api.client.get(URL), 401, "UNAUTHENTICATED")
    problem(api.client.get(URL, headers={"Authorization": "Bearer junk"}), 401, "UNAUTHENTICATED")


def test_e8_6_any_portal_role_may_read_and_no_tenant_is_needed(api: Api) -> None:
    for roles, tenant in ((["sbam-admin"], None), (["super-admin"], None), (["sbam-fde"], None)):
        token = api.realm.token(realm_access={"roles": roles}, tenant_id=tenant)
        res = api.client.get(URL, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200, roles


def test_e8_6_response_is_identical_across_callers(api: Api) -> None:
    other = api.realm.token(realm_access={"roles": ["sbam-admin"]})
    first = api.get().content
    second = api.client.get(URL, headers={"Authorization": f"Bearer {other}"}).content
    assert first == second  # no per-caller or tenant data in the body
