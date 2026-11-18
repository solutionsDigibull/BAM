"""WG-7 (#755): GET /projects/{id}/repo-scans/{sha256} through the real app, auth chain and
storage."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.storage.base import StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.repo_scan import FetchDecision, RefusalReason, RepoScanReport
from sbam_workers.repo_fetch.report import bundle_tar, store_bundle, store_report
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_project_workspace import Harness

REPORT = RepoScanReport(
    url="https://github.com/acme/pcb-tools",
    host="github.com",
    ref="main",
    commit="a" * 40,
    decision=FetchDecision.REFUSED,
    refusals=[RefusalReason.LICENCE],
    notes=["GPL-3.0-only is not on the allowlist"],
)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def storage(h: Harness) -> LocalDiskBackend:
    root: Path = h.client.app.state.settings.storage_root  # type: ignore[attr-defined]
    return LocalDiskBackend(root)


def scope(h: Harness, tenant: uuid.UUID = TENANT_A) -> StorageScope:
    return StorageScope(tenant, uuid.UUID(h.project))


def get(h: Harness, sha: str, token: str | None = None) -> Any:
    return h.call("GET", f"/projects/{h.project}/repo-scans/{sha}", token or h.fde())


def test_wg7_the_assigned_fde_reads_the_stored_report(h: Harness) -> None:
    stored = store_report(storage(h), scope(h), REPORT)
    res = get(h, stored.sha256)
    assert res.status_code == 200, res.text
    assert RepoScanReport.model_validate(res.json()) == REPORT


def test_wg7_a_hash_with_nothing_stored_is_404(h: Harness) -> None:
    problem(get(h, "0" * 64), 404, "NOT_FOUND")


def test_wg7_a_stored_tree_is_not_a_report(h: Harness) -> None:
    tree = store_bundle(storage(h), scope(h), {"a.py": b"x = 1\n"})
    assert tree.sha256 and bundle_tar({"a.py": b"x = 1\n"})
    problem(get(h, tree.sha256), 404, "NOT_FOUND")


def test_wg7_another_tenant_and_an_unassigned_fde_get_404(h: Harness) -> None:
    stored = store_report(storage(h), scope(h), REPORT)
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(get(h, stored.sha256, other_tenant), 404, "NOT_FOUND")
    problem(get(h, stored.sha256, h.realm.token(FDE_A2)), 404, "NOT_FOUND")


def test_wg7_a_report_of_another_tenant_is_not_readable_by_its_hash(h: Harness) -> None:
    # The same bytes under tenant B's scope: tenant A's route looks only inside tenant A's scope.
    other = store_report(storage(h), scope(h, TENANT_B), REPORT.model_copy(update={"host": "x.io"}))
    problem(get(h, other.sha256), 404, "NOT_FOUND")


def test_wg7_the_hash_must_look_like_a_sha256(h: Harness) -> None:
    assert get(h, "../../etc/passwd").status_code in (404, 422)
    assert get(h, "ABC").status_code == 422


def test_wg7_an_admin_cannot_read_a_project_scan(h: Harness) -> None:
    stored = store_report(storage(h), scope(h), REPORT)
    assert get(h, stored.sha256, h.admin_a()).status_code in (403, 404)
