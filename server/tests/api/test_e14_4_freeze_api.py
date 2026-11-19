"""E14.4 (#132): the ordered freeze behind `WorkflowFreezer` and the MAJOR.0.PATCH version row.

Uses the Commit harness of WG-18 (a certified Workflow with its evidence, fakes for the outside
world). T01 first freeze; T03 tamper via SQL; T05 idempotent re-run; T06 hash mismatch;
T09 hash reproducibility. The routes (T08, the patch, IDOR) are in test_e14_4_versions_api.py.
"""

import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sbam_api.artifacts.models import Artifact
from sbam_api.audit.models import AuditLog
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.conventions import ApiError
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.manifests.models import SubSkillManifest, WorkflowManifestRow
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.versioning import freezer
from sbam_api.versioning.freezer import OrderedFreezer, verify_manifests
from sbam_api.versioning.models import WorkflowVersionRow
from sbam_api.workflows.models import Workflow
from sbam_contracts.manifest import WorkflowManifest
from sqlalchemy import select, update
from test_auth import TENANT_A
from test_project_workspace import Harness
from test_wg16_connections_api import WORKFLOW

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wg18_commit_api import certify as wg18_certify
from test_wg18_commit_api import ctx, h, seed, storage  # noqa: F401

CID = "cid-e14-4"


PLACEHOLDER = "wf-001/schema.sql"  # the sample manifest's schema.sql key is not a stored file


@pytest.fixture
def hv(h: Harness, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:  # noqa: F811
    real = freezer.read_back
    monkeypatch.setattr(
        freezer,
        "read_back",
        lambda store, files: real(store, [f for f in files if f.key != PLACEHOLDER]),
    )
    bind = h.sessions.kw["bind"]
    for table in (WorkflowVersionRow, MacroSkill, Artifact, SubSkillManifest):
        table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
    yield h


def prepared(h: Harness, *, macro: bool = True) -> str:  # noqa: F811
    """A CERTIFIED Workflow (as certification leaves it) under a Macro Skill still BUILDING."""
    seed(h)
    sha = wg18_certify(h, recorded=True, imaged=True)
    if macro:
        with h.sessions() as s:
            sub = s.scalars(select(SubSkill)).one()
            s.add(
                MacroSkill(
                    id=sub.macro_skill_id,
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(h.project),
                    workflow_id=WORKFLOW,
                    sow_version_id=uuid.uuid4(),
                    macro_category="MS01_Ledger",
                    name="Ledger",
                    description="d",
                    order=1,
                    sow_refs=[],
                    state="BUILDING",
                )
            )
            s.commit()
    return sha


def freeze(h: Harness, sha: str) -> None:  # noqa: F811
    with h.sessions() as s:
        OrderedFreezer().freeze(s, ctx(h), WORKFLOW, sha, CID, storage(h))
        s.commit()


def versions(h: Harness) -> list[WorkflowVersionRow]:  # noqa: F811
    with h.sessions() as s:
        return list(s.scalars(select(WorkflowVersionRow)))


def test_e14_4_t01_first_freeze_is_1_0_0_and_everything_is_frozen(hv: Harness) -> None:
    sha = prepared(hv)
    freeze(hv, sha)
    [version] = versions(hv)
    assert (version.version, version.major, version.patch) == ("1.0.0", 1, 0)
    assert version.change_reason == "First freeze"
    with hv.sessions() as s:
        workflow = s.get(Workflow, WORKFLOW)
        assert workflow is not None and (workflow.state, workflow.version) == ("FROZEN", "1.0.0")
        assert s.scalars(select(SubSkill)).one().state == "FROZEN"
        assert s.scalars(select(MacroSkill)).one().state == "FROZEN"
        sealed = list(s.scalars(select(WorkflowManifestRow).order_by(WorkflowManifestRow.build_no)))
        assert sealed[-1].is_frozen is True and version.workflow_manifest_id == sealed[-1].id
        events = {r.action for r in s.scalars(select(AuditLog))}
        assert {"freeze", "all_sub_skills_certified"} <= events
        assert "WORKFLOW_VERSION_FROZEN" in events  # state changes and the version are audited


def test_e14_4_t05_a_second_run_changes_nothing(hv: Harness) -> None:
    sha = prepared(hv)
    freeze(hv, sha)
    with hv.sessions() as s:
        before = s.query(AuditLog).count()
    freeze(hv, sha)  # the crash-and-retry case: every step finds what the last run did
    assert [v.version for v in versions(hv)] == ["1.0.0"]
    with hv.sessions() as s:
        assert s.get(Workflow, WORKFLOW).state == "FROZEN"  # type: ignore[union-attr]
        assert s.query(AuditLog).count() == before


def test_e14_4_t03_the_database_refuses_to_change_a_frozen_version(hv: Harness) -> None:
    freeze(hv, prepared(hv))
    with hv.sessions() as s:
        with pytest.raises(Exception, match="frozen"):
            s.execute(update(WorkflowVersionRow).values(change_reason="x"))
            s.flush()
    with hv.sessions() as s:
        with pytest.raises(Exception, match="frozen"):
            s.execute(update(WorkflowManifestRow).values(manifest={}))
            s.flush()
    assert versions(hv)[0].change_reason == "First freeze"


def damage_bundle(h: Harness) -> None:  # noqa: F811
    """A code bundle in storage that is then changed on disk after it was written."""
    stored = storage(h).put(
        b"bundle bytes", StorageScope(TENANT_A, uuid.UUID(h.project), WORKFLOW), Kind.CODE, "x/y"
    )
    with h.sessions() as s:
        attempt = s.scalars(select(CodeAttempt)).one()
        attempt.bundle_key = stored.storage_key
        s.commit()
    path = storage(h)._path(stored.storage_key)  # type: ignore[attr-defined]
    path.chmod(0o644)
    path.write_bytes(b"tampered")


def test_e14_4_t06_a_file_that_no_longer_matches_stops_the_freeze(hv: Harness) -> None:
    sha = prepared(hv)
    damage_bundle(hv)
    with hv.sessions() as s:
        with pytest.raises(ApiError) as error:
            OrderedFreezer().freeze(s, ctx(hv), WORKFLOW, sha, CID, storage(hv))
        s.rollback()
    assert error.value.status == 409 and error.value.code.value == "HASH_MISMATCH"
    assert versions(hv) == []
    with hv.sessions() as s:
        assert s.get(Workflow, WORKFLOW).state == "CERTIFIED"  # type: ignore[union-attr]
        assert s.scalars(select(SubSkill)).one().state == "CERTIFIED"


def test_e14_4_a_workflow_that_is_not_certified_is_refused_and_nothing_is_written(
    hv: Harness,
) -> None:
    seed(hv)
    sha = wg18_certify(hv, recorded=True, imaged=True)
    with hv.sessions() as s:
        s.get(Workflow, WORKFLOW).state = "BUILDING"  # type: ignore[union-attr]
        s.commit()
    with hv.sessions() as s:
        with pytest.raises(ApiError) as error:
            OrderedFreezer().freeze(s, ctx(hv), WORKFLOW, sha, CID, storage(hv))
        s.rollback()
    assert error.value.status == 409 and versions(hv) == []


def test_e14_4_t09_every_frozen_manifest_hashes_to_its_recorded_hash(hv: Harness) -> None:
    freeze(hv, prepared(hv))
    with hv.sessions() as s:
        rows = list(s.scalars(select(WorkflowManifestRow)))
        assert rows
        for row in rows:
            manifest = WorkflowManifest.model_validate_json(json.dumps(row.manifest))
            assert manifest.computed_hash() == row.sha256
        verify_manifests(s, ctx(hv), WORKFLOW, [])
