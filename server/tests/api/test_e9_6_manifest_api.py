"""E9.6 manifest read API through the real app and auth chain: the Sub-Skill manifests and their
versions, the Workflow manifest (E9.5 draft), the manifest JSON Schemas; tenant / RBAC / IDOR."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.isatvon.models import SubSkill
from sbam_api.manifests.models import SubSkillManifest, WorkflowManifestRow
from sbam_contracts.canonical import sha256_of
from sqlalchemy import update
from test_auth import ADMIN_A, FDE_A2, TENANT_A, TENANT_B, problem
from test_isatvon_validate_api import Harness as WorkbenchHarness
from test_isatvon_validate_api import sample

XSS = "<img src=x onerror=alert(1)>"


def skill_manifest(patch: int = 0, name: str = "Investor Login") -> dict[str, Any]:
    """The parts of a SkillManifest the viewer reads (the API returns what is stored)."""
    return {
        "skill_metadata": {"skill_id": "MS01.1_Login", "skill_name": name},
        "isatvon_definition": {"i_instructions": f"Log in (rev {patch})."},
        "governance": {
            "deterministic_hash": sha256_of({"patch": patch, "name": name}),
            "versioning": {"major": 1, "minor": 0, "patch": patch},
        },
    }


class Harness(WorkbenchHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (SubSkillManifest, WorkflowManifestRow):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.sub = self.add_sub_skill(sample())

    def manifest(self, build_no: int, attempt: int = 0, **kw: Any) -> str:
        data = skill_manifest(**kw)
        sha = data["governance"]["deterministic_hash"]
        with self.sessions() as s:
            s.add(
                SubSkillManifest(
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    sub_skill_id=self.sub,
                    build_no=build_no,
                    cycle=1,
                    attempt=attempt,
                    manifest=data,
                    sha256=sha,
                    runtime_digest="sha256:" + "34" * 32,
                    created_by="SYSTEM",
                )
            )
            s.commit()
        return str(sha)

    def workflow_manifest(self, build_no: int) -> str:
        sha = sha256_of({"build": build_no})
        with self.sessions() as s:
            s.add(
                WorkflowManifestRow(
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    workflow_id=self.wf,
                    build_no=build_no,
                    manifest={"version": "1.0.0", "integrity": {"content_sha256": sha}},
                    sha256=sha,
                    created_by="SYSTEM",
                )
            )
            s.commit()
        return sha

    def read(self, path: str, token: str | None = None) -> Any:
        base = f"/projects/{self.project}/workflows/{self.wf}"
        return self.call("GET", base + path, token or self.fde())


@pytest.fixture
def h(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Harness]:
    yield Harness(tmp_path_factory.mktemp("s"))


def test_e9_6_the_newest_manifest_by_default_and_any_version_by_number(h: Harness) -> None:
    first = h.manifest(1)
    second = h.manifest(2, patch=1)
    res = h.read(f"/sub-skills/{h.sub}/manifest")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["version"], body["sha256"], body["build_no"]) == ("1.0.1", second, 2)
    assert body["manifest"]["governance"]["deterministic_hash"] == second  # the stored JSON
    assert body["schema_version"] == "manifest/skill_manifest@1.0" and not body["reused"]
    older = h.read(f"/sub-skills/{h.sub}/manifest?version=1.0.0").json()
    assert (older["sha256"], older["build_no"]) == (first, 1)
    assert h.read(f"/sub-skills/{h.sub}/manifest?build_no=1").json()["sha256"] == first
    versions = h.read(f"/sub-skills/{h.sub}/manifests").json()["items"]
    assert [(v["version"], v["build_no"]) for v in versions] == [("1.0.1", 2), ("1.0.0", 1)]


def test_e9_6_a_rebuilt_identical_manifest_is_one_version_marked_reused(h: Harness) -> None:
    h.manifest(1)
    h.manifest(1, attempt=1)  # a retried attempt, same hash
    assert len(h.read(f"/sub-skills/{h.sub}/manifests").json()["items"]) == 1
    with h.sessions() as s:
        s.execute(update(SubSkill).values(reused_from_id=h.sub))
        s.commit()
    assert h.read(f"/sub-skills/{h.sub}/manifest").json()["reused"] is True


@pytest.mark.parametrize("version", ["1.2.0", "latest", "1.0"])
def test_e9_6_a_version_is_major_0_patch(h: Harness, version: str) -> None:
    h.manifest(1)
    problem(h.read(f"/sub-skills/{h.sub}/manifest?version={version}"), 422, "VALIDATION_ERROR")


def test_e9_6_t07_no_manifest_yet_is_404(h: Harness) -> None:
    problem(h.read(f"/sub-skills/{h.sub}/manifest"), 404, "NOT_FOUND")
    assert h.read(f"/sub-skills/{h.sub}/manifests").json() == {"items": []}
    h.manifest(1)
    problem(h.read(f"/sub-skills/{h.sub}/manifest?version=2.0.0"), 404, "NOT_FOUND")


def test_e9_6_t05_manifest_text_comes_back_as_data(h: Harness) -> None:
    h.manifest(1, name=XSS)
    res = h.read(f"/sub-skills/{h.sub}/manifest")
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["manifest"]["skill_metadata"]["skill_name"] == XSS


def test_e9_6_the_workflow_manifest_of_each_build(h: Harness) -> None:
    problem(h.read("/manifest"), 404, "NOT_FOUND")
    first = h.workflow_manifest(1)
    second = h.workflow_manifest(2)
    assert h.read("/manifest").json()["sha256"] == second
    assert h.read("/manifest?build_no=1").json()["sha256"] == first
    assert [v["build_no"] for v in h.read("/manifests").json()["items"]] == [2, 1]


def test_e9_6_a_sub_skill_or_workflow_of_another_project_is_404(h: Harness) -> None:
    h.manifest(1)
    other = h.workflow(major=2, sow_version=h.generated(h.structured))  # same project
    path = f"/projects/{h.project}/workflows/{other}/sub-skills/{h.sub}/manifest"
    problem(h.call("GET", path, h.fde()), 404, "NOT_FOUND")
    problem(h.read(f"/sub-skills/{uuid.uuid4()}/manifests"), 404, "NOT_FOUND")
    unknown = f"/projects/{h.project}/workflows/{uuid.uuid4()}/manifests"
    problem(h.call("GET", unknown, h.fde()), 404, "NOT_FOUND")


def test_e9_6_t06_another_tenant_or_an_unassigned_fde_gets_404(h: Harness) -> None:
    h.manifest(1)
    h.workflow_manifest(1)
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other_tenant, h.realm.token(FDE_A2)):
        for path in (
            f"/sub-skills/{h.sub}/manifest",
            f"/sub-skills/{h.sub}/manifests",
            "/manifest",
        ):
            problem(h.read(path, token), 404, "NOT_FOUND")


def test_e9_6_only_the_fde_portal_reads_manifests(h: Harness) -> None:
    h.manifest(1)
    # refused before the handler; tests/security/rbac_matrix.yaml pins every role's answer
    problem(h.read(f"/sub-skills/{h.sub}/manifest", h.realm.token(ADMIN_A)), 404, "NOT_FOUND")


@pytest.mark.parametrize("name", ["skill_manifest", "workflow_manifest"])
def test_e9_6_the_manifest_schema_is_served_with_an_etag(h: Harness, name: str) -> None:
    res = h.call("GET", f"/schemas/manifest/{name}", h.fde())
    assert res.status_code == 200, res.text
    schema = res.json()
    assert schema["$id"].startswith(f"urn:sbam:contracts:manifest/{name}:")
    assert "governance" in schema["properties"] or "integrity" in schema["properties"]
    etag = res.headers["etag"]
    again = h.call("GET", f"/schemas/manifest/{name}", h.fde(), headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    problem(h.call("GET", "/schemas/manifest/loop_spec", h.fde()), 404, "NOT_FOUND")
