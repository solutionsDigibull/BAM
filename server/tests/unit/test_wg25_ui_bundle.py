"""WG-25 (#857): the UI bundle entry of the Workflow manifest, the UI_BUNDLE certification
evidence and the registry lookup (ADR 0033).

Nothing existing moves: a manifest frozen before the UI bundle keeps its hash. The registry runs
against the fake Bifrost and SQLite tables of the WG-12 tests.
"""

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_api.db.repository import TenantContext
from sbam_api.manifests import ui_bundle as rules
from sbam_api.manifests.models import GateResultRow
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.workflow_registry import service
from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.manifest import UiBundleEntry, WorkflowManifest, host_problem
from sbam_workers.build.workflow_server import with_server
from sbam_workers.certify.checklist import REQUIRED_GATES
from test_manifest import workflow
from test_wg12_workflow_registry import (
    CTX,
    DIGEST,
    UI_DIGEST,
    WORKFLOW_ID,
    FakeBifrost,
    World,
    detail_of,
    server_entry,
    ui_entry,
)

OTHER_UI_DIGEST = "sha256:" + "e" * 64
BARE = UI_DIGEST.removeprefix("sha256:")


def ui(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": "1.0.0",
        "digest": UI_DIGEST,
        "entry_file": "index.html",
        "allowed_hosts": ["api.example.com", "*.cdn.example.com"],
        "workflow_server": "wf_investor_onboarding",
    }
    return {**base, **over}


def served() -> WorkflowManifest:
    """A manifest built after ADR 0028 and before the UI bundle: a server image, no bundle."""
    return with_server(WorkflowManifest.sealed(workflow()), server_entry())


def built(**over: Any) -> WorkflowManifest:
    """A manifest SBAM built: the server image and the UI bundle."""
    return WorkflowManifest.sealed({**served().dump(), "ui_bundle": ui(**over)})


# --- WG25-T01 / T02: the contract ----------------------------------------------------------------


def test_wg25_t01_a_manifest_with_a_ui_bundle_validates_and_hashes() -> None:
    manifest = built()
    assert manifest.ui_bundle == UiBundleEntry.model_validate(ui())
    assert manifest.computed_hash() == manifest.integrity.content_sha256
    assert manifest.dump()["ui_bundle"]["digest"] == UI_DIGEST
    other = built(digest=OTHER_UI_DIGEST)  # the bundle is part of the frozen hash
    assert other.integrity.content_sha256 != manifest.integrity.content_sha256


def test_wg25_t02_a_legacy_frozen_manifest_is_unchanged() -> None:
    plain = WorkflowManifest.sealed(workflow())
    assert "ui_bundle" not in plain.dump()
    again = WorkflowManifest.model_validate(json.loads(json.dumps(plain.dump())))
    assert again.computed_hash() == plain.integrity.content_sha256
    after_adr_0028 = served()
    assert "ui_bundle" not in after_adr_0028.dump()
    assert after_adr_0028.computed_hash() == after_adr_0028.integrity.content_sha256
    assert not plain.requires_ui_bundle
    assert after_adr_0028.requires_ui_bundle


@pytest.mark.parametrize(
    "over",
    [
        {"version": "1.2.3"},
        {"digest": "sha256:xyz"},
        {"digest": "a" * 64},  # no prefix
        {"entry_file": "../index.html"},
        {"entry_file": "/index.html"},
        {"entry_file": "app.js"},
        {"allowed_hosts": ["http://api.example.com"]},
        {"allowed_hosts": ["api.example.com:8443"]},
        {"allowed_hosts": ["*"]},
        {"allowed_hosts": ["10.0.0.1"]},
        {"allowed_hosts": ["localhost"]},
        {"allowed_hosts": ["api.example.com", "api.example.com"]},
        {"workflow_server": "wf-northwind"},
    ],
)
def test_wg25_a_malformed_ui_bundle_entry_is_rejected(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        UiBundleEntry.model_validate(ui(**over))


def test_wg25_the_allowed_hosts_may_be_empty() -> None:
    assert built(allowed_hosts=[]).ui_bundle is not None


def test_wg25_a_bundle_must_belong_to_the_manifests_server() -> None:
    with pytest.raises(ValidationError):
        WorkflowManifest.sealed({**served().dump(), "ui_bundle": ui(workflow_server="wf_other")})
    with pytest.raises(ValidationError):  # and a server must exist
        WorkflowManifest.sealed({**workflow(), "ui_bundle": ui()})


def test_wg25_host_problems_name_the_reason() -> None:
    assert host_problem("api.example.com") is None
    assert host_problem("*.example.com") is None
    assert host_problem("192.168.0.1") is not None
    assert host_problem("Example.com") is not None


# --- the gate rules ------------------------------------------------------------------------------


def test_wg25_the_gate_passes_when_present_matching_and_valid() -> None:
    verdict = rules.check_ui_bundle(built(), UI_DIGEST)
    assert verdict.status is GateStatus.PASSED
    assert verdict.findings == []
    assert rules.check_ui_bundle(built(), BARE).findings == []


def test_wg25_the_gate_fails_for_a_missing_entry_a_missing_bundle_and_a_wrong_hash() -> None:
    assert rules.check_ui_bundle(served(), UI_DIGEST).findings[0]["code"] == "UI_BUNDLE_MISSING"
    assert rules.check_ui_bundle(built(), None).findings[0]["code"] == "UI_BUNDLE_NOT_FOUND"
    wrong = rules.check_ui_bundle(built(), OTHER_UI_DIGEST)
    assert wrong.status is GateStatus.FAILED
    assert wrong.findings[0]["code"] == "UI_BUNDLE_HASH_MISMATCH"


def test_wg25_the_gate_checks_hosts_again_on_an_entry_that_skipped_validation() -> None:
    manifest = built()
    assert manifest.ui_bundle is not None
    bad = UiBundleEntry.model_construct(
        **{**manifest.ui_bundle.model_dump(), "allowed_hosts": ["http://evil.test"]}
    )
    forged = manifest.model_copy(update={"ui_bundle": bad})
    verdict = rules.check_ui_bundle(forged, UI_DIGEST)
    assert [f["code"] for f in verdict.findings] == ["UI_BUNDLE_HOST_INVALID"]


def test_wg25_ui_bundle_is_workflow_evidence_not_a_sub_skill_gate() -> None:
    assert GateName.UI_BUNDLE not in REQUIRED_GATES


# --- WG25-T03 / T04: certification ---------------------------------------------------------------


def row(status: str, sha: str | None) -> GateResultRow:
    return GateResultRow(
        tenant_id=CTX.tenant_id,
        project_id=CTX.project_id,
        workflow_id=WORKFLOW_ID,
        gate=GateName.UI_BUNDLE.value,
        status=status,
        bundle_sha256=sha,
    )


def test_wg25_t03_certification_refuses_a_missing_ui_bundle() -> None:
    assert rules.certification_reasons(served(), []) == [rules.MISSING]  # no entry, no row
    assert rules.certification_reasons(built(), []) == [rules.MISSING]  # entry, never checked
    assert rules.certification_reasons(built(), [row("FAILED", BARE)]) == [rules.FAILED]


def test_wg25_t04_a_wrong_bundle_hash_is_refused() -> None:
    stale = [row("PASSED", OTHER_UI_DIGEST.removeprefix("sha256:"))]
    assert rules.certification_reasons(built(), stale) == [rules.MISMATCH]


def test_wg25_certification_accepts_a_passed_row_on_the_manifests_digest() -> None:
    assert rules.certification_reasons(built(), [row("PASSED", BARE)]) == []


def test_wg25_a_legacy_manifest_is_not_judged() -> None:
    assert rules.certification_reasons(WorkflowManifest.sealed(workflow()), []) == []


def test_wg25_the_newest_row_decides() -> None:
    assert rules.certification_reasons(built(), [row("FAILED", BARE), row("PASSED", BARE)]) == []
    failed_last = [row("PASSED", BARE), row("FAILED", BARE)]
    assert rules.certification_reasons(built(), failed_last) == [rules.FAILED]


def test_wg25_the_row_is_recorded_once_per_verdict_and_is_tenant_scoped() -> None:
    world = World()
    manifest = built()
    with world.sessions() as session:
        first = rules.record_ui_bundle_gate(session, CTX, WORKFLOW_ID, manifest, UI_DIGEST)
        again = rules.record_ui_bundle_gate(session, CTX, WORKFLOW_ID, manifest, UI_DIGEST)
        assert first.id == again.id
        failed = rules.record_ui_bundle_gate(session, CTX, WORKFLOW_ID, manifest, None)
        assert failed.status == "FAILED"
        session.commit()
        latest = GateResultRepository(session, CTX).latest_workflow_gate(WORKFLOW_ID, "UI_BUNDLE")
        assert latest is not None and latest.status == "FAILED"
        other = TenantContext(uuid.uuid4(), CTX.project_id)
        assert GateResultRepository(session, other).workflow_rows(WORKFLOW_ID, "UI_BUNDLE") == []


# --- WG25-T05: registry --------------------------------------------------------------------------


def test_wg25_t05_registry_lookup_returns_the_certified_bundle_digest() -> None:
    world = World()
    manifest = world.freeze()
    world.register(FakeBifrost())
    with world.sessions() as session:
        found = service.lookup(session, CTX, WORKFLOW_ID)
    assert found.ui_bundle is not None
    assert found.ui_bundle.digest == UI_DIGEST == manifest.dump()["ui_bundle"]["digest"]
    assert found.ui_bundle.entry_file == "index.html"
    assert found.ui_bundle.allowed_hosts == ["api.example.com"]
    assert found.image_digest == DIGEST


def test_wg25_a_superseded_version_still_returns_its_own_bundle() -> None:
    world = World()
    fake = FakeBifrost()
    world.freeze("1.0.0", build_no=1)
    world.register(fake)
    world.freeze("1.0.1", build_no=2, ui=ui_entry("1.0.1", OTHER_UI_DIGEST))
    world.register(fake, version="1.0.1")
    with world.sessions() as session:
        old = service.lookup(session, CTX, WORKFLOW_ID, "1.0.0")
        new = service.lookup(session, CTX, WORKFLOW_ID)
    assert old.ui_bundle is not None and old.ui_bundle.digest == UI_DIGEST
    assert new.ui_bundle is not None and new.ui_bundle.digest == OTHER_UI_DIGEST


def test_wg25_registration_refuses_when_there_is_no_ui_evidence() -> None:
    world = World()
    world.freeze(ui_gate=False)  # the manifest has the entry, the gate never ran
    fake = FakeBifrost()
    with pytest.raises(service.NotRegistrable) as caught:
        world.register(fake)
    assert "UI bundle" in detail_of(caught)
    assert fake.writes() == []  # nothing uncertified becomes visible


def test_wg25_registration_refuses_when_the_ui_evidence_failed() -> None:
    world = World()
    manifest = world.freeze(ui_gate=False)
    with world.sessions() as session:
        rules.record_ui_bundle_gate(session, CTX, WORKFLOW_ID, manifest, OTHER_UI_DIGEST)
        session.commit()
    fake = FakeBifrost()
    with pytest.raises(service.NotRegistrable):
        world.register(fake)
    assert fake.writes() == []


def test_wg25_an_entry_registered_before_the_bundle_has_none() -> None:
    world = World()
    world.freeze()
    world.register(FakeBifrost())
    with world.sessions() as session:
        stored = session.query(RegisteredWorkflowRow).one()
        stored.ui_bundle = None
        session.commit()
        assert service.lookup(session, CTX, WORKFLOW_ID).ui_bundle is None
