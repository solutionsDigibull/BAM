"""E15.3 update 2026-11-18 (#137): a Staging registry entry is not visible to SRIM's production key.

Through the real app and auth (the WG-12 harness, tests/api/test_wg12_registration_api.py): a
Workflow that Commit registered as `staging` only. The lookup route answers 404 for
`kind=production` (with and without a version), as if nothing existed, and the names a production
virtual key may be granted in Bifrost (`names_for_key`) are empty. The staging entry stays readable
to the assigned FDE, so the 404 comes from the kind, not from a missing row.
"""

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

import test_wg12_registration_api as wg12
from sbam_api.db.repository import TenantContext
from sbam_api.workflow_registry import service
from sbam_contracts.workflow_registry import RegistryKind
from test_auth import TENANT_A, problem
from test_project_workspace import Harness

STAGING_NAME = "wf_investor_onboarding_v1_0_stg"


@pytest.fixture
def staged(h: Harness) -> Iterator[Harness]:
    wg12.add(h, "1.0.0", kind=RegistryKind.STAGING.value, registry_name=STAGING_NAME)
    yield h


h = wg12.h  # the WG-12 fixture


def test_e15_3_a_staging_entry_is_not_visible_to_srims_production_key(staged: Harness) -> None:
    for version in (None, "1.0.0"):
        body = problem(wg12.get(staged, version=version, kind="production"), 404, "NOT_FOUND")
        assert STAGING_NAME not in str(body)
    with staged.sessions() as s:
        ctx = TenantContext(TENANT_A, uuid.UUID(staged.project))
        assert service.names_for_key(s, ctx, RegistryKind.PRODUCTION) == []
        assert service.names_for_key(s, ctx, RegistryKind.STAGING) == [STAGING_NAME]
    # control: the row is there, and the assigned FDE's staging lookup finds it
    res = wg12.get(staged, kind="staging")
    assert res.status_code == 200, res.text
    assert res.json()["kind"] == "staging" and res.json()["registry_name"] == STAGING_NAME
