"""A-12b (#670): with the auth-state cache ON, a deactivation, a role change or an assignment change
is seen on the very next request, not after the TTL (A-04, E3.x). Invalidation is per process."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.core.auth_cache import AuthStateCache
from sbam_api.identity.models import User
from sbam_api.projects.models import ProjectMember
from sbam_api.projects.repository import SqlMemberships
from sqlalchemy import select
from test_auth import ADMIN_A, FDE_A1, FDE_A2, problem
from test_projects import ADMIN
from test_projects import Harness as ProjectsHarness

BOTH: dict[str, Any] = {"realm_access": {"roles": ["sbam-fde", "sbam-admin"]}}


class Harness(ProjectsHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        ProjectMember.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        auth = self.client.app.state.auth  # type: ignore[attr-defined]
        auth.memberships = SqlMemberships(lambda: self.sessions)
        auth.state_cache = AuthStateCache(3600)  # far past any test: only invalidation can refresh
        self.cache: AuthStateCache = auth.state_cache

    def fde(self, sub: uuid.UUID = FDE_A1, sid: str = "s1", **claims: Any) -> str:
        return self.realm.token(sub, sid=sid, **claims)

    def me(self, token: str) -> Any:
        return self.call("GET", "/me", token)

    def goal(self, project_id: str, token: str) -> Any:
        return self.call("GET", f"/projects/{project_id}/goal", token)

    def user_id(self, sub: uuid.UUID) -> uuid.UUID:
        with self.sessions() as s:
            return s.scalars(select(User.id).where(User.keycloak_sub == str(sub))).one()

    def patch_user(self, sub: uuid.UUID, **body: Any) -> Any:
        with self.sessions() as s:
            version = s.scalars(select(User.row_version).where(User.keycloak_sub == str(sub))).one()
        return self.call(
            "PATCH",
            f"/admin/users/{self.user_id(sub)}",
            self.admin_a(),
            json=body,
            headers={"if-match": f'"{version}"'},
        )

    def put_members(self, project_id: str, subs: list[uuid.UUID]) -> Any:
        ids = [str(self.user_id(s)) for s in subs]
        return self.call(
            "PUT",
            f"/admin/projects/{project_id}/members",
            self.admin_a(),
            json={"user_ids": ids},
        )

    def warm(self, sub: uuid.UUID = FDE_A1, sid: str = "s1") -> None:
        assert self.me(self.fde(sub, sid)).status_code == 200
        assert self.cache.get(str(sub), sid) is not None  # the cache really holds it


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    assert harness.me(harness.realm.token(ADMIN_A, **ADMIN)).status_code == 200  # mirror the admin
    yield harness


def test_a12b_t01_a_deactivated_user_is_refused_on_the_next_request(h: Harness) -> None:
    h.warm()
    assert h.patch_user(FDE_A1, active=False).status_code == 200
    assert h.cache.get(str(FDE_A1), "s1") is None
    problem(h.me(h.fde()), 401, "UNAUTHENTICATED")  # no wait for the TTL


def test_a12b_t02_a_removed_role_is_gone_on_the_next_request(h: Harness) -> None:
    assert h.me(h.fde(**BOTH)).json()["roles"] == ["sbam-admin", "sbam-fde"]
    assert h.cache.get(str(FDE_A1), "s1") is not None
    assert h.patch_user(FDE_A1, roles=["sbam-fde"]).status_code == 200
    assert h.me(h.fde(**BOTH)).json()["roles"] == ["sbam-fde"]
    problem(h.call("GET", "/admin/users", h.fde(**BOTH)), 403, "WRONG_PORTAL")


def test_a12b_t03_an_unassigned_user_gets_404_on_the_next_request(h: Harness) -> None:
    project = h.create()["id"]
    h.me(h.fde())  # mirror the FDE so it can be assigned
    assert h.put_members(project, [FDE_A1]).status_code == 200
    assert h.goal(project, h.fde()).status_code == 200  # warms the cache with the assignment
    assert h.cache.get(str(FDE_A1), "s1") is not None
    assert h.put_members(project, []).status_code == 200
    problem(h.goal(project, h.fde()), 404, "NOT_FOUND")


def test_a12b_t04_invalidation_is_targeted_to_the_changed_user(h: Harness) -> None:
    h.warm(FDE_A1)
    h.warm(FDE_A2)
    other = h.cache.get(str(FDE_A2), "s1")
    assert h.patch_user(FDE_A1, active=False).status_code == 200
    assert h.cache.get(str(FDE_A1), "s1") is None
    assert h.cache.get(str(FDE_A2), "s1") is other  # untouched
    assert h.me(h.fde(FDE_A2)).status_code == 200


def test_a12b_t05_an_assigned_user_reaches_the_project_on_the_next_request(h: Harness) -> None:
    project = h.create()["id"]
    h.warm()
    problem(h.goal(project, h.fde()), 404, "NOT_FOUND")  # cached "no assignments"
    assert h.put_members(project, [FDE_A1]).status_code == 200
    assert h.goal(project, h.fde()).status_code == 200


def test_a12b_t06_a_change_that_changes_nothing_keeps_the_cache(h: Harness) -> None:
    h.warm()
    assert h.patch_user(FDE_A1, roles=["sbam-fde"]).status_code == 200  # already that
    assert h.cache.get(str(FDE_A1), "s1") is not None
