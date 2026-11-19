"""WG-28-T03 (#862): the Skill manifest carries `build.repos` only for a build that attached a
repository, read from the `repo_attached` record; the others hash as before."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from knowledge_db import TENANT
from sbam_api.builds.repo_attached import AttachStatus, RepoAttached, overlays
from sbam_contracts.manifest import SkillManifest
from sbam_workers.build.manifest import build_for
from sbam_workers.build.repo_attach import SqlAttachStore
from sbam_workers.repo_fetch.overlay import build_overlay
from test_e9_1_manifest_store import Build

URL = "https://github.com/acme/lib"
COMMIT = "d" * 40


@pytest.fixture
def b(tmp_path: Path) -> Iterator[Build]:
    yield Build(tmp_path)


def attached(status: AttachStatus) -> RepoAttached:
    overlay = build_overlay(
        url=URL,
        commit=COMMIT,
        vendor={"LICENSE": b"MIT\n", "lib.py": b"X = 1\n"},
        licence="MIT",
        scan_report_sha256="e" * 64,
    ).overlay
    return RepoAttached(
        status=status,
        url=URL,
        commit=COMMIT,
        report_sha256="e" * 64,
        overlay=overlay.model_dump(mode="json") if status is AttachStatus.ATTACHED else None,
    )


def repos_of(row_manifest: dict[str, Any]) -> Any:
    return SkillManifest.model_validate(row_manifest).sbam_build_record.build.repos


def test_wg28_t03_a_build_without_a_repository_leaves_repos_unset(b: Build) -> None:
    build_for(b.store, b.inp())
    [row] = b.manifests()
    assert "repos" not in row.manifest["sbam_build_record"]["build"]  # hashes as before
    assert repos_of(row.manifest) is None


def test_wg28_t03_an_attached_repository_is_recorded_in_the_manifest(b: Build) -> None:
    SqlAttachStore(b.p.w.sessions).put(b.inp(), attached(AttachStatus.ATTACHED))
    build_for(b.store, b.inp())
    [row] = b.manifests()
    [repo] = repos_of(row.manifest)
    assert (repo.url, repo.commit, repo.licence) == (URL, COMMIT, "MIT")
    assert repo.scan_report_sha256 == "e" * 64 and repo.patches == []
    with b.p.w.sessions() as s:
        assert len(overlays(s, TENANT, b.sub_skill_id, 1)) == 1
        assert overlays(s, TENANT, b.sub_skill_id, 2) == ()  # another build attached nothing


def test_wg28_t03_a_refused_repository_leaves_repos_unset(b: Build) -> None:
    SqlAttachStore(b.p.w.sessions).put(b.inp(), attached(AttachStatus.REFUSED))
    build_for(b.store, b.inp())
    [row] = b.manifests()
    assert repos_of(row.manifest) is None
