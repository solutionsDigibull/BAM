"""E14.5 (#133) export package: the routes through the real app and auth chain, and the activity.

A frozen version 1.0.0 is written as the freeze (#132) leaves it: the sealed Workflow manifest, the
frozen APPROVED Skill Manifest, the stored code bundle and schema.sql, and the `workflow_versions`
row. Temporal is the fake client of WG-18; the activity runs in-process on the same storage.
"""

import hashlib
import io
import json
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sbam_api.artifacts.models import Artifact
from sbam_api.audit.models import AuditLog
from sbam_api.builds.models import CodeAttempt
from sbam_api.isatvon.models import SubSkill
from sbam_api.manifests.models import SubSkillManifest, WorkflowManifestRow
from sbam_api.notifications.models import Notification
from sbam_api.projects.models import ProjectMember
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.versioning.models import WorkflowVersionRow
from sbam_api.workflows.models import Workflow
from sbam_contracts.exports import ExportInput
from sbam_contracts.manifest import SkillManifest, WorkflowManifest
from sbam_workers.exports import activities
from sbam_workers.exports.activities import HASH_MISMATCH, ExportSteps
from sbam_workers.exports.package import UnsafePath, write_package
from sbam_workers.repo_fetch.report import bundle_tar
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from test_auth import FDE_B1, TENANT_A, TENANT_B
from test_project_workspace import Harness
from test_wg16_connections_api import LEDGER, SECRET, WORKFLOW, put_value

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_wg12_workflow_registry import DIGEST, sealed
from test_wg18_commit_api import FakeClient, client, ctx, h, seed, storage  # noqa: F401

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "isa14" / "ms01_1_manifest.json"
KEY = "00000000-0000-4000-8000-000000000001"
OTHER_KEY = "00000000-0000-4000-8000-000000000002"
SIGNING_KEY = "k" * 40
CODE = {
    "action.py": b"def run(x):\n    return x\n",
    "loop_spec.json": b'{"max_iterations": 3}',
    "vendor/lib@abc123/LICENSE": b"MIT",
    "patches/lib@abc123/0001-fix.patch": b"--- a\n+++ b\n",
}
REQUIRED = {
    "workflow_manifest.json",
    "schema.sql",
    "evidence_index.json",
    "sbom.cdx.json",
    ".env.example",
    "tools.json",
    "image.json",
    "ui_bundle.json",
    "CHECKSUMS.sha256",
}


def url(h: Harness, version: str = "1.0.0") -> str:  # noqa: F811
    return f"/projects/{h.project}/workflows/{WORKFLOW}/versions/{version}/export"


def frozen(h: Harness, code: dict[str, bytes] = CODE) -> str:  # noqa: F811
    """Version 1.0.0 frozen as #132 leaves it. Returns the code bundle's storage key."""
    seed(h, LEDGER)
    bind = h.sessions.kw["bind"]
    for table in (WorkflowVersionRow, SubSkillManifest, Artifact, Notification, ProjectMember):
        table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
    project = uuid.UUID(h.project)
    scope = StorageScope(TENANT_A, project, WORKFLOW)
    bundle = storage(h).put(bundle_tar(code), scope, Kind.CODE, "application/x-tar")
    sql = storage(h).put(b"CREATE TABLE t (id int);\n", scope, Kind.CODE, "text/plain")
    skill = json.loads(FIXTURE.read_text(encoding="utf-8"))
    skill_id = skill["skill_metadata"]["skill_id"]
    det = SkillManifest.model_validate(skill).computed_hash()
    data = sealed("1.0.0", DIGEST).dump()
    entry = data["macro_skills"][0]["skills"][0]
    entry.update(skill_id=skill_id, deterministic_hash=det, bundle_sha256=bundle.sha256)
    data["macro_skills"][0]["skills"] = [entry]
    data["schema_sql"] = {"artifact_key": sql.storage_key, "sha256": sql.sha256}
    manifest = WorkflowManifest.sealed(data)
    with h.sessions() as s:
        sub = s.scalars(select(SubSkill).where(SubSkill.workflow_id == WORKFLOW)).one()
        sub.skill_id, sub.state = skill_id, "FROZEN"
        s.add(
            CodeAttempt(
                tenant_id=TENANT_A,
                project_id=project,
                workflow_id=WORKFLOW,
                sub_skill_id=sub.id,
                build_no=1,
                cycle=0,
                attempt=0,
                template_name="t",
                template_version="1.0.0",
                template_sha256="0" * 64,
                status="ASSEMBLED",
                bodies={},
                trace_id="trace",
                bundle_sha256=bundle.sha256,
                bundle_key=bundle.storage_key,
            )
        )
        s.add(
            SubSkillManifest(
                tenant_id=TENANT_A,
                project_id=project,
                sub_skill_id=sub.id,
                build_no=1,
                cycle=0,
                attempt=0,
                manifest=skill,
                sha256=det,
                runtime_digest=DIGEST,
                is_frozen=True,
            )
        )
        row = WorkflowManifestRow(
            tenant_id=TENANT_A,
            project_id=project,
            workflow_id=WORKFLOW,
            build_no=1,
            manifest=json.loads(json.dumps(manifest.dump())),
            sha256=manifest.integrity.content_sha256,
            is_frozen=True,
        )
        s.add(row)
        s.flush()
        s.add(
            WorkflowVersionRow(
                tenant_id=TENANT_A,
                project_id=project,
                workflow_id=WORKFLOW,
                version="1.0.0",
                major=1,
                patch=0,
                sow_version_id=uuid.uuid4(),
                workflow_manifest_id=row.id,
                change_reason="First freeze",
                frozen_at=sub.created_at,
            )
        )
        s.commit()
    h.client.app.state.settings.storage_signing_key = SecretStr(SIGNING_KEY)  # type: ignore[attr-defined]
    return bundle.storage_key


def post(h: Harness, key: str | None = KEY, token: str | None = None) -> Any:  # noqa: F811
    headers = {} if key is None else {"Idempotency-Key": key}
    return h.call("POST", url(h), token or h.fde(), headers=headers)


def run(h: Harness, fake: FakeClient, index: int = -1) -> Any:  # noqa: F811
    inp = fake.started[index][1]
    assert isinstance(inp, ExportInput)
    return ExportSteps(h.sessions, storage(h)).run(inp)


def zip_of(h: Harness, key: str) -> zipfile.ZipFile:  # noqa: F811
    return zipfile.ZipFile(io.BytesIO(storage(h).get(key)))


def sha256sum_c(archive: zipfile.ZipFile) -> list[str]:
    """`sha256sum -c CHECKSUMS.sha256` in Python: the paths it verified."""
    checked = []
    for line in archive.read("CHECKSUMS.sha256").decode().splitlines():
        digest, path = line.split("  ", 1)
        assert hashlib.sha256(archive.read(path)).hexdigest() == digest, path
        checked.append(path)
    return checked


def exports(h: Harness) -> list[Artifact]:  # noqa: F811
    with h.sessions() as s:
        return list(s.scalars(select(Artifact).where(Artifact.kind == "EXPORT")))


def test_e14_5_t01_happy_export_has_every_entry_and_checksums_verify(
    h: Harness, client: FakeClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    notified: list[Any] = []

    def notify(*_: Any, **kwargs: Any) -> int:
        notified.append(kwargs)
        return 1

    monkeypatch.setattr(activities, "notify_project", notify)
    frozen(h)
    put_value(h, LEDGER, SECRET)  # a stored secret value must never reach the package
    res = post(h)
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["version"] == "1.0.0" and body["run"]["type"] == "ExportVersion"
    result = run(h, client)
    archive = zip_of(h, result.storage_key)
    names = archive.namelist()
    skill = "skills/MS01.1_Investor_Auth_Manager"
    expected = REQUIRED | {
        f"{skill}/manifest.json",
        f"{skill}/loop_spec.json",
        f"{skill}/code/action.py",
        f"{skill}/code/vendor/lib@abc123/LICENSE",
        f"{skill}/code/patches/lib@abc123/0001-fix.patch",
    }
    assert set(names) == expected  # every required entry, no extra file
    assert names[-1] == "CHECKSUMS.sha256" and names[:-1] == sorted(names[:-1])
    assert sorted(sha256sum_c(archive)) == sorted(expected - {"CHECKSUMS.sha256"})
    for info in archive.infolist():
        assert info.date_time == (1980, 1, 1, 0, 0, 0) and not info.is_dir()
        assert info.external_attr >> 16 == 0o100644  # a regular file, never a symlink
    assert archive.read(".env.example").decode().count(f"{LEDGER}=\n") == 1
    raw = storage(h).get(result.storage_key)
    assert SECRET.encode() not in raw and not any(n.split("/")[-1] == ".env" for n in names)
    for artifact in exports(h):
        assert artifact.is_frozen and artifact.sha256 == result.sha256
    assert hashlib.sha256(raw).hexdigest() == result.sha256
    with h.sessions() as s:
        actions = {r.action for r in s.scalars(select(AuditLog))}
        assert "EXPORTED" in actions
    assert [n["type"].value for n in notified] == ["EXPORT_READY"]  # in-app 'Export ready'
    download = h.call("GET", url(h), h.fde(), follow_redirects=False)
    assert download.status_code == 302
    location = download.headers["location"]
    assert result.storage_key in location and "exp=" in location and "sig=" in location
    fetched = h.client.get(location)
    assert fetched.status_code == 200 and fetched.content == raw


def test_e14_5_t02_a_certified_version_that_is_not_frozen_is_409(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    frozen(h)
    with h.sessions() as s:
        workflow = s.get(Workflow, WORKFLOW)
        assert workflow is not None
        workflow.version, workflow.state = "1.0.1", "CERTIFIED"
        s.commit()
    res = h.call("POST", url(h, "1.0.1"), h.fde(), headers={"Idempotency-Key": KEY})
    assert res.status_code == 409 and res.json()["code"] == "NOT_FROZEN"
    assert client.started == []
    unknown = h.call("POST", url(h, "7.0.0"), h.fde(), headers={"Idempotency-Key": KEY})
    assert unknown.status_code == 404


def test_e14_5_t03_the_same_key_is_the_same_run_and_one_artifact(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    frozen(h)
    first = post(h)
    assert first.status_code == 202
    run(h, client)
    again = post(h)
    assert again.status_code == 202 and again.json()["run"]["id"] == first.json()["run"]["id"]
    assert len(client.started) == 1  # not started twice
    run(h, client)  # a retried activity answers what it stored
    assert len(exports(h)) == 1
    with h.sessions() as s:
        assert len([r for r in s.scalars(select(AuditLog)) if r.action == "EXPORTED"]) == 1


def test_e14_5_t04_two_keys_give_byte_identical_zips(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    frozen(h)
    assert post(h, KEY).status_code == 202
    assert post(h, OTHER_KEY).status_code == 202
    assert client.started[0][2] != client.started[1][2]  # two runs
    one, two = run(h, client, 0), run(h, client, 1)
    assert one.sha256 == two.sha256 and one.storage_key == two.storage_key
    assert len(exports(h)) == 1


def test_e14_5_t05_zip_slip_paths_are_refused_and_never_written(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    frozen(h)
    post(h)
    archive = zip_of(h, run(h, client).storage_key)
    for name in archive.namelist():
        assert ".." not in name.split("/") and not name.startswith("/") and "\\" not in name
    for bad in ("../evil", "/etc/passwd", "a/../../b", "a\\b", "a\x00b", "C:/x", "a//b", ".env"):
        with pytest.raises(UnsafePath):
            write_package({bad: b"x"}, io.BytesIO())


def test_e14_5_t06_tenant_b_cannot_download_tenant_a_export(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    frozen(h)
    post(h)
    run(h, client)
    token = h.realm.token(FDE_B1, tenant_id=[str(TENANT_B)])
    res = h.call("GET", url(h), token, follow_redirects=False)
    assert res.status_code == 404
    assert post(h, token=token).status_code == 404


def test_e14_5_t07_a_tampered_frozen_file_aborts_with_an_alert_and_no_zip(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    key = frozen(h)
    post(h)
    path = storage(h)._path(key)  # type: ignore[attr-defined]
    path.chmod(0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(ApplicationError) as error:
        run(h, client)
    assert error.value.type == HASH_MISMATCH and error.value.non_retryable
    assert exports(h) == []
    with h.sessions() as s:
        actions = [r.action for r in s.scalars(select(AuditLog))]
    assert "EXPORT_HASH_MISMATCH" in actions and "EXPORTED" not in actions
    assert h.call("GET", url(h), h.fde(), follow_redirects=False).status_code == 404


def test_e14_5_t05_a_bundle_holding_a_dotenv_is_refused_without_retry(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    frozen(h, {**CODE, ".env": b"SECRET=x"})
    post(h)
    with pytest.raises(ApplicationError) as error:
        run(h, client)
    assert error.value.type == "ExportRefused" and error.value.non_retryable
    assert exports(h) == []


def test_e14_5_t08_every_skill_manifest_is_isa14_and_approved(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    frozen(h)
    post(h)
    archive = zip_of(h, run(h, client).storage_key)
    found = [
        n for n in archive.namelist() if n.startswith("skills/") and n.endswith("/manifest.json")
    ]
    assert found
    for name in found:
        manifest = SkillManifest.model_validate_json(archive.read(name))
        assert manifest.skill_metadata.status.value == "APPROVED"
        assert manifest.computed_hash() == manifest.governance.deterministic_hash


def test_e14_5_the_idempotency_key_is_required_and_must_be_a_uuid(
    h: Harness, client: FakeClient  # noqa: F811
) -> None:
    frozen(h)
    missing = post(h, key=None)
    # the repo's convention (`validate_idempotency_key`): a missing or malformed key is 400
    assert missing.status_code == 400 and missing.json()["code"] == "VALIDATION_ERROR"
    assert post(h, key="not-a-uuid-key").status_code == 400
    assert client.started == []


def test_e14_5_get_before_any_export_is_404(h: Harness, client: FakeClient) -> None:  # noqa: F811
    frozen(h)
    assert h.call("GET", url(h), h.fde(), follow_redirects=False).status_code == 404


def test_e14_5_the_package_is_byte_deterministic_for_the_same_entries() -> None:
    entries: dict[str, Any] = {"b.txt": b"2", "a/x.json": b"{}", "c": lambda: b"3"}
    one, two = io.BytesIO(), io.BytesIO()
    assert write_package(entries, one).sha256 == write_package(dict(entries), two).sha256
    assert one.getvalue() == two.getvalue()
