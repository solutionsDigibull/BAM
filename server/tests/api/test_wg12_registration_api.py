"""WG-12 (#760): GET /projects/{id}/workflows/{wf}/registration through the real app and auth."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_contracts.workflow_registry import RegisteredWorkflow, RegistryStatus
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_project_workspace import Harness

WORKFLOW = uuid.UUID("0190f3a2-7b1c-7d4e-8f00-0000000000a1")
DIGEST = "sha256:" + "a" * 64
TOOLS = [
    {
        "name": f"MS01_1__{role}",
        "skill_id": "MS01.1_Investor_Auth_Manager",
        "role": role,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    for role in ("action", "check")
]


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    table = RegisteredWorkflowRow.__table__
    RegisteredWorkflowRow.metadata.create_all(
        harness.sessions.kw["bind"],
        tables=[table],  # type: ignore[list-item]
    )
    yield harness


def add(h: Harness, version: str, status: str = "REGISTERED", **over: Any) -> None:
    major, _, patch = version.split(".")
    with h.sessions() as session:
        session.add(
            RegisteredWorkflowRow(
                tenant_id=TENANT_A,
                project_id=uuid.UUID(h.project),
                workflow_id=WORKFLOW,
                version=version,
                registry_name=f"wf_investor_onboarding_v{major}_{patch}",
                registry_id=f"id-{version}",
                image_digest=DIGEST,
                manifest_hash="sha256:" + "c" * 64,
                certification_id=uuid.uuid4(),
                owner="fde-a1",
                tools=TOOLS,
                status=status,
                registered_at=datetime(2026, 11, 17, tzinfo=UTC),
                **over,
            )
        )
        session.commit()


def get(h: Harness, token: str | None = None, version: str | None = None) -> Any:
    query = f"?version={version}" if version else ""
    path = f"/projects/{h.project}/workflows/{WORKFLOW}/registration{query}"
    return h.call("GET", path, token or h.fde())


def test_wg12_the_assigned_fde_reads_the_current_entry(h: Harness) -> None:
    add(h, "1.0.0", "SUPERSEDED", superseded_by="1.0.1")
    add(h, "1.0.1")
    res = get(h)
    assert res.status_code == 200, res.text
    found = RegisteredWorkflow.model_validate_json(res.text)
    assert found.version == "1.0.1" and found.status is RegistryStatus.REGISTERED
    assert found.image_digest == DIGEST
    assert [t.name for t in found.tools] == ["MS01_1__action", "MS01_1__check"]


def test_wg12_a_superseded_version_is_still_readable_by_its_version(h: Harness) -> None:
    add(h, "1.0.0", "SUPERSEDED", superseded_by="1.0.1")
    add(h, "1.0.1")
    found = RegisteredWorkflow.model_validate_json(get(h, version="1.0.0").text)
    assert found.status is RegistryStatus.SUPERSEDED and found.superseded_by == "1.0.1"


def test_wg12_nothing_registered_or_an_unknown_version_is_404_and_a_bad_one_is_422(
    h: Harness,
) -> None:
    problem(get(h), 404, "NOT_FOUND")  # nothing registered: nothing is visible
    add(h, "1.0.0")
    problem(get(h, version="2.0.0"), 404, "NOT_FOUND")
    assert get(h, version="banana").status_code == 422


def test_wg12_another_tenant_and_an_unassigned_fde_get_404(h: Harness) -> None:
    add(h, "1.0.0")
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(get(h, other_tenant), 404, "NOT_FOUND")
    problem(get(h, h.realm.token(FDE_A2)), 404, "NOT_FOUND")


def test_wg12_there_is_no_route_that_writes_a_registration(h: Harness) -> None:
    add(h, "1.0.0")
    path = f"/projects/{h.project}/workflows/{WORKFLOW}/registration"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert h.call(method, path, h.fde(), json={}).status_code in (404, 405)
