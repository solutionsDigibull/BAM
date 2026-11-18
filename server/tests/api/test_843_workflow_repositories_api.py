"""#843: the Workflow's repositories (patches, scan report, components), read only."""

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_code_view_api as code_tests
import test_wg15_workflow_code_api as wg15
from sbam_api.core.ids import uuid7
from sbam_api.isatvon.models import SubSkill
from sbam_api.storage.base import Kind, StorageScope
from sbam_contracts.repo_scan import (
    DependencyLicence,
    FetchDecision,
    LicenceFinding,
    LicenceVerdict,
    RepoScanReport,
)
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_wg15_workflow_code_api import Harness

URL = "https://github.com/acme/lib"


class RepoHarness(Harness):
    def repos(self, tail: str = "", token: str | None = None, wf: uuid.UUID | None = None) -> Any:
        url = f"{self.path(wf or self.workflow_id)}/repositories{tail}"
        return self.call("GET", url, token or self.fde())

    @property
    def skill_id(self) -> str:
        with self.sessions() as s:
            found = s.get(SubSkill, self.sub)
            assert found is not None
            return found.skill_id

    def store_report(self, **over: Any) -> None:
        fields: dict[str, Any] = {
            "url": URL,
            "host": "github.com",
            "commit": wg15.COMMIT,
            "decision": FetchDecision.VENDORED,
            "licence": LicenceFinding(spdx="MIT", source="LICENSE", verdict=LicenceVerdict.ALLOWED),
            "dependency_licences": [
                DependencyLicence(
                    ecosystem="PyPI",
                    name="zlib-x",
                    version="2.0",
                    spdx="MIT",
                    verdict=LicenceVerdict.ALLOWED,
                ),
                DependencyLicence(
                    ecosystem="PyPI",
                    name="abc-y",
                    version="1.0",
                    spdx="ISC",
                    verdict=LicenceVerdict.ALLOWED,
                ),
            ],
        }
        fields.update(over)
        report = RepoScanReport(**fields)
        scope = StorageScope(TENANT_A, uuid.UUID(self.project))
        self.storage.put(report.model_dump_json().encode(), scope, Kind.REPOS, "application/json")


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RepoHarness]:
    monkeypatch.setattr(code_tests, "make_bundle", wg15.nested_bundle)
    yield RepoHarness(tmp_path)


def test_843_a_workflow_that_uses_no_repository_lists_none(h: RepoHarness) -> None:
    h.attempt(0, wg15.FILES)  # no Skill manifest records `build.repos`
    body = h.repos().json()
    assert body["repositories"] == []
    assert body["workflow_id"] == str(h.workflow_id)


def test_843_a_repository_without_a_stored_report_is_listed_with_no_report(h: RepoHarness) -> None:
    h.attempt(0, wg15.FILES)
    h.overlay_manifest()
    [repo] = h.repos().json()["repositories"]
    assert (repo["url"], repo["commit"], repo["licence"]) == (URL, wg15.COMMIT, "MIT")
    assert repo["report"] is None
    assert [p["reason"] for p in repo["patches"]] == ["a stated reason"]
    assert [(c["kind"], c["name"], c["version"]) for c in repo["sbom"]] == [
        ("repository", "acme/lib", wg15.COMMIT)
    ]
    assert repo["sub_skills"] == [h.skill_id]


def test_843_the_stored_scan_report_is_found_by_url_and_commit(h: RepoHarness) -> None:
    h.attempt(0, wg15.FILES)
    h.overlay_manifest()
    h.store_report()
    h.store_report(url="https://github.com/acme/other")  # another repository: not this one
    scope = StorageScope(TENANT_A, uuid.UUID(h.project))
    h.storage.put(b"not a report, a tree", scope, Kind.REPOS, "application/x-tar")
    [repo] = h.repos().json()["repositories"]
    assert repo["report"]["decision"] == "VENDORED"
    assert repo["report"]["licence"]["spdx"] == "MIT"
    # The repository first, then its dependencies in a stable (ecosystem, name) order.
    assert [c["name"] for c in repo["sbom"]] == ["acme/lib", "abc-y", "zlib-x"]
    assert repo["sbom"][1] == {
        "kind": "dependency",
        "name": "abc-y",
        "version": "1.0",
        "ecosystem": "PyPI",
        "licence": "ISC",
    }


def test_843_a_report_of_another_project_is_not_shown(h: RepoHarness) -> None:
    h.attempt(0, wg15.FILES)
    h.overlay_manifest()
    other = StorageScope(TENANT_A, uuid7())
    report = RepoScanReport(
        url=URL, host="github.com", commit=wg15.COMMIT, decision=FetchDecision.VENDORED
    )
    h.storage.put(report.model_dump_json().encode(), other, Kind.REPOS, "application/json")
    [repo] = h.repos().json()["repositories"]
    assert repo["report"] is None  # reports are looked up inside this project's scope only


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_843_no_write_route_exists(h: RepoHarness, method: str) -> None:
    h.attempt(0, wg15.FILES)
    assert h.call(method, f"{h.path(h.workflow_id)}/repositories", h.fde()).status_code == 405


def test_843_other_tenants_unassigned_fdes_and_unknown_ids_get_404(h: RepoHarness) -> None:
    h.attempt(0, wg15.FILES)
    h.overlay_manifest()
    h.store_report()
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    unassigned = h.realm.token(FDE_A2)
    for token in (other, unassigned):
        problem(h.repos(token=token), 404, "NOT_FOUND")
    problem(h.repos(wf=uuid7()), 404, "NOT_FOUND")  # an invented Workflow id


def test_843_the_response_never_carries_file_content(h: RepoHarness) -> None:
    h.attempt(0, wg15.FILES)
    h.overlay_manifest()
    h.store_report()
    text = json.dumps(h.repos().json())
    assert "X = 1" not in text and "MIT License" not in text  # names and findings only
