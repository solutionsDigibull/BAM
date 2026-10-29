"""E4.2 upload API: the upload token, the tusd hooks and document registration.

Same stand-ins as the projects tests (SQLite with schema `sbam` mapped away, a local-disk storage
root, a fake Temporal client). The tests play tusd: they call the hook endpoint with the secret and
put the bytes where tusd would stage them. T06 (resume) and T10's real-Temporal half need the
Compose stack and are in tests/integration.
"""

import hashlib
import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from runs_db import FakeClient
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.ingest import tokens
from sbam_api.ingest.models import (
    Document,
    DocumentVersion,
    ReservationStatus,
    UploadReservation,
)
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.documents import MAX_ACTIVE_FILES, MAX_FILE_BYTES
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy import func, select
from test_auth import FDE_A1, FDE_A2, TENANT_A, TENANT_B, problem
from test_projects import Harness as ProjectsHarness

SECRET = "hook-secret-for-tests"  # noqa: S105 - a test value
HOOKS = "/internal/tusd/hooks"
PDF = b"%PDF-1.7 a small test document\n" * 40


class Harness(ProjectsHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.tmp = tmp_path
        self.temporal = FakeClient()
        state = self.client.app.state  # type: ignore[attr-defined]
        state.settings.tusd_hook_secret = SecretStr(SECRET)
        state.temporal = self.temporal
        bind = self.sessions.kw["bind"]
        for table in (Document, DocumentVersion, UploadReservation, TemporalRun):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.project = self.create("Quote Bot")["id"]
        self.members.assigned[FDE_A1] = {uuid.UUID(self.project)}

    # -- the FDE side --------------------------------------------------------------------------
    def fde(self) -> str:
        return self.realm.token(FDE_A1)

    def files(self, *names: str, size: int = len(PDF), **extra: Any) -> dict[str, Any]:
        return {
            "files": [{"name": n, "size": size, "mime": "application/pdf", **extra} for n in names]
        }

    def announce(self, body: dict[str, Any], project: str | None = None) -> Any:
        return self.call(
            "POST", f"/projects/{project or self.project}/uploads", self.fde(), json=body
        )

    # -- the tusd side -------------------------------------------------------------------------
    def hook(self, payload: dict[str, Any], secret: str | None = SECRET) -> Any:
        headers = {"x-tusd-hook-secret": secret} if secret is not None else {}
        return self.client.post(f"/api/v1{HOOKS}", json=payload, headers=headers)

    def pre_create(
        self,
        token: str,
        name: str,
        size: int,
        project: str | None = None,
        hook_secret: str | None = SECRET,
        **meta: Any,
    ) -> Any:
        upload = {
            "Size": size,
            "SizeIsDeferred": False,
            "MetaData": {
                "token": token,
                "filename": name,
                "project_id": project or self.project,
                **meta,
            },
        }
        return self.hook({"Type": "pre-create", "Event": {"Upload": upload}}, hook_secret)

    def post_finish(self, upload_id: str, token: str, size: int, offset: int | None = None) -> Any:
        upload = {
            "ID": upload_id,
            "Size": size,
            "Offset": size if offset is None else offset,
            "MetaData": {"token": token, "project_id": self.project},
        }
        return self.hook({"Type": "post-finish", "Event": {"Upload": upload}})

    def stage(self, upload_id: str, data: bytes) -> Path:
        path = self.tmp / "uploads" / upload_id
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        path.with_name(path.name + ".info").write_text("{}")
        return path

    def upload(self, name: str = "spec.pdf", data: bytes = PDF) -> tuple[str, str]:
        """The whole happy path for one file: announce, pre-create, stage, post-finish."""
        res = self.announce(self.files(name, size=len(data)))
        assert res.status_code == 201, res.text
        token = res.json()["token"]
        decision = self.pre_create(token, name, len(data))
        assert decision.status_code == 200, decision.text
        upload_id = decision.json()["ChangeFileInfo"]["ID"]
        self.stage(upload_id, data)
        assert self.post_finish(upload_id, token, len(data)).status_code == 200
        return upload_id, token

    # -- the database --------------------------------------------------------------------------
    def versions(self) -> list[DocumentVersion]:
        with self.sessions() as s:
            return list(s.scalars(select(DocumentVersion)))

    def reservations(self) -> list[UploadReservation]:
        with self.sessions() as s:
            return list(s.scalars(select(UploadReservation)))

    def count(self, model: Any) -> int:
        with self.sessions() as s:
            return int(s.scalar(select(func.count()).select_from(model)) or 0)

    def seed_versions(self, count: int, state: str = S.UPLOADED.value) -> None:
        with self.sessions() as s:
            for i in range(count):
                doc = Document(
                    tenant_id=TENANT_A, project_id=uuid.UUID(self.project), title=f"d{i}.pdf"
                )
                s.add(doc)
                s.flush()
                s.add(
                    DocumentVersion(
                        tenant_id=TENANT_A,
                        project_id=uuid.UUID(self.project),
                        document_id=doc.id,
                        storage_key="k",
                        sha256=f"{i:064x}",
                        size=1,
                        mime="application/pdf",
                        state=state,
                    )
                )
            s.commit()

    def audit_actions(self, entity_id: str) -> list[str]:
        with self.sessions() as s:
            rows = s.scalars(select(AuditLog).where(AuditLog.entity_id == entity_id))
            return [r.action for r in rows]


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def rejected(res: Any) -> dict[str, Any]:
    """The tusd response body of a refused pre-create: its status code and problem+json."""
    body = res.json()
    assert res.status_code == 200 and body["RejectUpload"] is True, res.text
    problem_body: dict[str, Any] = json.loads(body["HTTPResponse"]["Body"])
    assert problem_body["status"] == body["HTTPResponse"]["StatusCode"]
    return problem_body


# --- T01 ----------------------------------------------------------------------------------------


def test_e4_2_t01_valid_upload_registers_a_version(h: Harness) -> None:
    res = h.announce(h.files("spec.pdf"))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["accepted"] == [{"name": "spec.pdf", "size": len(PDF), "mime": "application/pdf"}]
    assert body["rejected"] == [] and body["tus_endpoint"].endswith("/files/")

    decision = h.pre_create(body["token"], "spec.pdf", len(PDF))
    upload_id = decision.json()["ChangeFileInfo"]["ID"]
    staged = h.stage(upload_id, PDF)
    assert h.post_finish(upload_id, body["token"], len(PDF)).status_code == 200

    (version,) = h.versions()
    sha = hashlib.sha256(PDF).hexdigest()
    assert version.state == "UPLOADED" and version.sha256 == sha and version.size == len(PDF)
    assert str(version.id) == upload_id
    assert version.storage_key == f"{TENANT_A}/{h.project}/uploads/{sha}"  # content hash, not name
    assert (h.tmp / version.storage_key).read_bytes() == PDF
    assert not staged.exists() and not staged.with_name(staged.name + ".info").exists()
    with h.sessions() as s:
        assert s.scalars(select(Document.title)).one() == "spec.pdf"
    assert h.audit_actions(upload_id) == ["UPLOAD_ACCEPTED", "upload", "DOCUMENT_UPLOADED"]
    assert [r.status for r in h.reservations()] == ["FINISHED"]


def test_e4_2_registered_version_starts_one_ingestion_workflow(h: Harness) -> None:
    upload_id, _ = h.upload()
    assert [c["workflow"] for c in h.temporal.calls] == ["DocumentIngestion"]
    assert h.temporal.calls[0]["id"] == f"ingest-{upload_id}"
    assert h.temporal.calls[0]["task_queue"] == "sbam-ingest"
    assert h.versions()[0].ingestion_started_at is not None
    assert h.count(TemporalRun) == 1


# --- T02 / T03: size ----------------------------------------------------------------------------


def test_e4_2_t02_oversize_is_413_and_stores_nothing(h: Harness) -> None:
    res = h.announce(h.files("big.pdf", size=MAX_FILE_BYTES + 1))
    problem(res, 413, "FILE_TOO_LARGE")
    assert h.count(UploadReservation) == 0 and h.count(DocumentVersion) == 0
    with h.sessions() as s:
        assert [r.action for r in s.scalars(select(AuditLog))].count("UPLOAD_REJECTED") == 1


def test_e4_2_t03_exactly_15_mb_is_accepted_and_uploaded(h: Harness) -> None:
    data = b"x" * MAX_FILE_BYTES
    h.upload("max.pdf", data)
    (version,) = h.versions()
    assert version.size == 15_728_640 and version.state == "UPLOADED"


def test_e4_2_pre_create_refuses_a_size_over_15_mb(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    body = rejected(h.pre_create(token, "a.pdf", MAX_FILE_BYTES + 1))
    assert body["code"] == "FILE_TOO_LARGE" and body["status"] == 413
    assert h.reservations()[0].status == "RESERVED"  # nothing started


# --- T04: file count ----------------------------------------------------------------------------


def test_e4_2_t04_twenty_first_file_is_409(h: Harness) -> None:
    h.seed_versions(MAX_ACTIVE_FILES)
    problem(h.announce(h.files("one-more.pdf")), 409, "FILE_LIMIT_REACHED")
    assert h.count(UploadReservation) == 0


def test_e4_2_batch_is_cut_at_the_limit_and_reports_each_refusal(h: Harness) -> None:
    h.seed_versions(MAX_ACTIVE_FILES - 1)
    res = h.announce(h.files("a.pdf", "b.pdf", "c.pdf"))
    assert res.status_code == 201, res.text
    body = res.json()
    assert [f["name"] for f in body["accepted"]] == ["a.pdf"]
    assert body["rejected"] == [
        {"name": "b.pdf", "code": "FILE_LIMIT_REACHED"},
        {"name": "c.pdf", "code": "FILE_LIMIT_REACHED"},
    ]


def test_e4_2_announced_but_unfinished_files_count_toward_the_limit(h: Harness) -> None:
    assert h.announce(h.files(*[f"f{i}.pdf" for i in range(MAX_ACTIVE_FILES)])).status_code == 201
    problem(h.announce(h.files("late.pdf")), 409, "FILE_LIMIT_REACHED")


def test_e4_2_deleted_and_rejected_versions_do_not_count(h: Harness) -> None:
    h.seed_versions(10, S.DELETED.value)
    h.seed_versions(10, S.REJECTED.value)
    assert h.announce(h.files("fits.pdf")).status_code == 201


def test_e4_2_pre_create_checks_the_count_again(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    h.seed_versions(MAX_ACTIVE_FILES)  # the project filled up since the token was issued
    body = rejected(h.pre_create(token, "a.pdf", len(PDF)))
    assert body["code"] == "FILE_LIMIT_REACHED" and body["status"] == 409


# --- T05: type ----------------------------------------------------------------------------------


def test_e4_2_t05_disallowed_type_is_415(h: Harness) -> None:
    problem(h.announce(h.files("setup.exe")), 415, "UNSUPPORTED_TYPE")
    assert h.count(UploadReservation) == 0


def test_e4_2_mixed_batch_accepts_good_files_and_names_the_bad_one(h: Harness) -> None:
    body = h.announce(h.files("ok.pdf", "setup.exe", "noextension")).json()
    assert [f["name"] for f in body["accepted"]] == ["ok.pdf"]
    assert {r["name"]: r["code"] for r in body["rejected"]} == {
        "setup.exe": "UNSUPPORTED_TYPE",
        "noextension": "UNSUPPORTED_TYPE",
    }


def test_e4_2_pre_create_refuses_a_type_even_with_a_valid_token(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    body = rejected(h.pre_create(token, "a.exe", len(PDF)))
    assert body["code"] == "UNSUPPORTED_TYPE" and body["status"] == 415


# --- T07: forged hook ---------------------------------------------------------------------------


@pytest.mark.parametrize("secret", [None, "", "wrong", SECRET + "x", SECRET[:-1]])
def test_e4_2_t07_forged_hook_is_401_and_creates_nothing(h: Harness, secret: str | None) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    upload = {"Size": len(PDF), "MetaData": {"token": token, "filename": "a.pdf"}}
    res = h.hook({"Type": "pre-create", "Event": {"Upload": upload}}, secret)
    problem(res, 401, "UNAUTHENTICATED")
    assert [r.status for r in h.reservations()] == ["RESERVED"]  # not claimed
    assert h.count(DocumentVersion) == 0


def test_e4_2_t07_forged_post_finish_creates_no_row(h: Harness) -> None:
    upload_id = str(uuid7())
    h.stage(upload_id, PDF)
    res = h.hook({"Type": "post-finish", "Event": {"Upload": {"ID": upload_id}}}, None)
    problem(res, 401, "UNAUTHENTICATED")
    assert h.count(DocumentVersion) == 0 and h.temporal.calls == []


def test_e4_2_garbage_hook_body_with_the_secret_is_422(h: Harness) -> None:
    res = h.client.post(
        f"/api/v1{HOOKS}", content=b"not json", headers={"x-tusd-hook-secret": SECRET}
    )
    problem(res, 422, "VALIDATION_ERROR")


def test_e4_2_without_a_configured_secret_uploads_are_503(h: Harness) -> None:
    h.client.app.state.settings.tusd_hook_secret = None  # type: ignore[attr-defined]
    problem(h.announce(h.files("a.pdf")), 503, "SERVICE_UNAVAILABLE")
    problem(h.hook({"Type": "pre-create"}), 503, "SERVICE_UNAVAILABLE")


# --- T08: token bound to project and user -------------------------------------------------------


def test_e4_2_t08_token_of_project_a_is_refused_for_project_b(h: Harness) -> None:
    other = h.create("Other Project")["id"]
    token = h.announce(h.files("a.pdf")).json()["token"]
    body = rejected(h.pre_create(token, "a.pdf", len(PDF), project=other))
    assert body["status"] == 403 and body["code"] == "FORBIDDEN"
    assert h.count(DocumentVersion) == 0 and h.reservations()[0].status == "RESERVED"


def test_e4_2_token_without_a_project_is_refused(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    upload = {"Size": len(PDF), "MetaData": {"token": token, "filename": "a.pdf"}}
    body = rejected(h.hook({"Type": "pre-create", "Event": {"Upload": upload}}))
    assert body["status"] == 403


@pytest.mark.parametrize(
    "mangle", [lambda t: t + "A", lambda t: "x" + t, lambda t: "", lambda t: "."]
)
def test_e4_2_tampered_or_missing_token_is_refused(h: Harness, mangle: Any) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    assert rejected(h.pre_create(mangle(token), "a.pdf", len(PDF)))["status"] == 403


def test_e4_2_token_signed_with_another_secret_is_refused(h: Harness) -> None:
    claims = tokens.UploadClaims(
        TENANT_A, uuid.UUID(h.project), FDE_A1, uuid7(), int(time.time()) + 600
    )
    forged = tokens.issue("not-the-secret", claims)
    assert rejected(h.pre_create(forged, "a.pdf", len(PDF)))["status"] == 403


def test_e4_2_expired_token_is_refused_at_pre_create(h: Harness) -> None:
    h.announce(h.files("a.pdf"))
    batch = h.reservations()[0].batch_id
    claims = tokens.UploadClaims(
        TENANT_A, uuid.UUID(h.project), FDE_A1, batch, int(time.time()) - 1
    )
    assert rejected(h.pre_create(tokens.issue(SECRET, claims), "a.pdf", len(PDF)))["status"] == 403


def test_e4_2_a_slot_can_be_used_once(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    assert "ChangeFileInfo" in h.pre_create(token, "a.pdf", len(PDF)).json()
    assert rejected(h.pre_create(token, "a.pdf", len(PDF)))["status"] == 403


def test_e4_2_upload_must_match_an_announced_name_and_size(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    assert rejected(h.pre_create(token, "other.pdf", len(PDF)))["status"] == 403
    assert rejected(h.pre_create(token, "a.pdf", len(PDF) + 1))["status"] == 403
    assert h.reservations()[0].status == "RESERVED"


def test_e4_2_deferred_length_is_refused(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    upload = {"SizeIsDeferred": True, "MetaData": {"token": token, "filename": "a.pdf"}}
    body = rejected(h.hook({"Type": "pre-create", "Event": {"Upload": upload}}))
    assert body["status"] == 400


def test_e4_2_pre_create_refuses_an_archived_project(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    res = h.call("POST", f"/admin/projects/{h.project}/archive", h.admin_a())
    assert res.status_code == 200, res.text
    assert rejected(h.pre_create(token, "a.pdf", len(PDF)))["code"] == "PROJECT_ARCHIVED"


# --- T09: names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["../../etc/passwd.txt", "a/b.txt", "..\\x.txt", "a\x00.txt", ".."]
)
def test_e4_2_t09_path_traversal_name_is_422_at_files_0_name(h: Harness, name: str) -> None:
    body = problem(h.announce(h.files(name)), 422, "VALIDATION_ERROR")
    # The API renders list indexes with dots (core/errors.py): files.0.name.
    assert [e["path"] for e in body["errors"]] == ["files.0.name"]
    assert h.count(UploadReservation) == 0


# --- who may ask --------------------------------------------------------------------------------


def test_e4_2_unassigned_or_other_tenant_fde_gets_404(h: Harness) -> None:
    for token in (
        h.realm.token(FDE_A2),
        h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)]),
    ):
        res = h.call("POST", f"/projects/{h.project}/uploads", token, json=h.files("a.pdf"))
        problem(res, 404, "NOT_FOUND")
    assert h.count(UploadReservation) == 0


def test_e4_2_admin_cannot_use_the_fde_upload_route(h: Harness) -> None:
    res = h.call("POST", f"/projects/{h.project}/uploads", h.admin_a(), json=h.files("a.pdf"))
    assert res.status_code == 403 and res.json()["code"] == "WRONG_PORTAL"


def test_e4_2_archived_project_takes_no_uploads(h: Harness) -> None:
    h.call("POST", f"/admin/projects/{h.project}/archive", h.admin_a())
    # An archived project also disappears from the FDE's assignments only in the list, not here.
    problem(h.announce(h.files("a.pdf")), 409, "PROJECT_ARCHIVED")


def test_e4_2_request_body_is_strict(h: Harness) -> None:
    bad: list[dict[str, Any]] = [
        {"files": []},
        {"files": h.files("a.pdf")["files"] * 21},
        {"nope": 1},
    ]
    for body in bad:
        problem(h.announce(body), 422, "VALIDATION_ERROR")
    problem(h.announce(h.files("a.pdf", size=0)), 422, "VALIDATION_ERROR")


def test_e4_2_token_lifetime_is_at_most_15_minutes(h: Harness) -> None:
    body = h.announce(h.files("a.pdf")).json()
    claims = tokens.verify(SECRET, body["token"])
    assert 0 < claims.expires_at - time.time() <= 15 * 60
    assert claims.tenant_id == TENANT_A and claims.user_id == FDE_A1
    assert str(claims.project_id) == h.project


# --- post-finish edge cases ---------------------------------------------------------------------


def test_e4_2_same_file_twice_is_linked_not_stored_twice(h: Harness) -> None:
    first, _ = h.upload("a.pdf")
    second, _ = h.upload("copy-of-a.pdf")
    assert h.count(DocumentVersion) == 1 and h.count(Document) == 1
    dup = next(r for r in h.reservations() if str(r.id) == second)
    assert dup.status == ReservationStatus.DUPLICATE and str(dup.document_version_id) == first
    assert len(h.temporal.calls) == 1  # no second workflow
    assert "UPLOAD_DUPLICATE" in h.audit_actions(second)


def test_e4_2_retried_post_finish_does_not_register_twice(h: Harness) -> None:
    upload_id, token = h.upload()
    assert h.post_finish(upload_id, token, len(PDF)).status_code == 200
    assert h.count(DocumentVersion) == 1 and len(h.temporal.calls) == 1


def test_e4_2_upload_can_finish_after_its_token_expired(h: Harness) -> None:
    token = h.announce(h.files("big.pdf")).json()["token"]
    upload_id = h.pre_create(token, "big.pdf", len(PDF)).json()["ChangeFileInfo"]["ID"]
    h.stage(upload_id, PDF)
    batch = h.reservations()[0].batch_id
    old = tokens.UploadClaims(
        TENANT_A, uuid.UUID(h.project), FDE_A1, batch, int(time.time()) - 3600
    )
    assert h.post_finish(upload_id, tokens.issue(SECRET, old), len(PDF)).status_code == 200
    assert h.count(DocumentVersion) == 1


@pytest.mark.parametrize("upload_id", ["../../etc/passwd", "not-a-uuid", "", str(uuid.uuid4())])
def test_e4_2_post_finish_ignores_ids_that_are_not_ours(h: Harness, upload_id: str) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    h.post_finish(upload_id, token, len(PDF))
    assert h.count(DocumentVersion) == 0


def test_e4_2_post_finish_needs_a_complete_upload_and_a_staged_file(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    upload_id = h.pre_create(token, "a.pdf", len(PDF)).json()["ChangeFileInfo"]["ID"]
    h.post_finish(upload_id, token, len(PDF))  # nothing staged
    h.stage(upload_id, PDF)
    h.post_finish(upload_id, token, len(PDF), offset=len(PDF) - 1)  # not complete
    h.stage(upload_id, PDF[:-1])
    h.post_finish(upload_id, token, len(PDF))  # staged file is shorter than announced
    assert h.count(DocumentVersion) == 0
    h.stage(upload_id, PDF)
    h.post_finish(upload_id, token, len(PDF))
    assert h.count(DocumentVersion) == 1


def test_e4_2_post_finish_of_a_never_created_slot_is_ignored(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    slot = str(h.reservations()[0].id)
    h.stage(slot, PDF)  # a file at the right path, but pre-create never accepted it
    h.post_finish(slot, token, len(PDF))
    assert h.count(DocumentVersion) == 0


def test_e4_2_other_tenants_token_cannot_register_my_slot(h: Harness) -> None:
    token = h.announce(h.files("a.pdf")).json()["token"]
    slot = h.pre_create(token, "a.pdf", len(PDF)).json()["ChangeFileInfo"]["ID"]
    h.stage(slot, PDF)
    batch = h.reservations()[0].batch_id
    claims = tokens.UploadClaims(
        TENANT_B, uuid.UUID(h.project), FDE_A1, batch, int(time.time()) + 60
    )
    h.post_finish(slot, tokens.issue(SECRET, claims), len(PDF))
    assert h.count(DocumentVersion) == 0


# --- T10: Temporal down at post-finish ----------------------------------------------------------


def test_e4_2_t10_temporal_down_keeps_the_version_and_starts_it_later(h: Harness) -> None:
    h.temporal.down = True
    first, _ = h.upload("a.pdf", PDF)
    (version,) = h.versions()
    assert version.state == "UPLOADED" and version.ingestion_started_at is None
    assert h.count(TemporalRun) == 0

    h.temporal.down = False  # Temporal is back; the next upload of the project starts both
    second, _ = h.upload("b.pdf", PDF + b"more")
    started = {c["id"] for c in h.temporal.calls if c["id"].startswith("ingest-")}
    assert {f"ingest-{first}", f"ingest-{second}"} <= started
    assert all(v.ingestion_started_at is not None for v in h.versions())
    assert h.count(TemporalRun) == 2


def test_e4_2_temporal_unreachable_does_not_fail_the_hook(h: Harness) -> None:
    h.client.app.state.temporal = None  # type: ignore[attr-defined]  # real connect: refused
    h.upload()
    assert h.versions()[0].ingestion_started_at is None


# --- tenant isolation of the data ---------------------------------------------------------------


def test_e4_2_repositories_are_scoped_to_the_callers_tenant(h: Harness) -> None:
    h.upload()
    from sbam_api.ingest.repository import DocumentVersionRepository

    with h.sessions() as s:
        mine = DocumentVersionRepository(s, TenantContext(TENANT_A, uuid.UUID(h.project)))
        theirs = DocumentVersionRepository(s, TenantContext(TENANT_B, uuid.UUID(h.project)))
        assert mine.active_count() == 1 and theirs.active_count() == 0


# --- A-14: upload tokens have their own key ------------------------------------------------------

TOKEN_KEY = "upload-token-key-for-tests"  # noqa: S105 - a test value


def _separate_keys(h: Harness) -> None:
    h.client.app.state.settings.upload_token_key = SecretStr(TOKEN_KEY)  # type: ignore[attr-defined]


def test_a14_t01_tokens_are_signed_with_the_token_key_not_the_hook_secret(h: Harness) -> None:
    _separate_keys(h)
    token = h.announce(h.files("a.pdf")).json()["token"]
    assert tokens.verify(TOKEN_KEY, token, time.time())  # the token key opens it
    with pytest.raises(Exception):  # noqa: B017 - TokenError, whatever its name
        tokens.verify(SECRET, token, time.time())  # the hook secret does not


def test_a14_t02_a_token_signed_with_the_hook_secret_is_refused_once_keys_are_separate(
    h: Harness,
) -> None:
    _separate_keys(h)
    claims = tokens.UploadClaims(
        TENANT_A, uuid.UUID(h.project), FDE_A1, uuid7(), int(time.time()) + 600
    )
    forged = tokens.issue(SECRET, claims)  # someone who learned the hook secret
    assert rejected(h.pre_create(forged, "a.pdf", len(PDF)))["status"] == 403


def test_a14_t03_rotating_the_hook_secret_does_not_invalidate_issued_tokens(h: Harness) -> None:
    _separate_keys(h)
    token = h.announce(h.files("a.pdf")).json()["token"]
    h.client.app.state.settings.tusd_hook_secret = SecretStr("rotated-hook-secret")  # type: ignore[attr-defined]
    res = h.pre_create(token, "a.pdf", len(PDF), hook_secret="rotated-hook-secret")  # noqa: S106
    assert res.status_code == 200 and "RejectUpload" not in res.text


def test_a14_t04_rotating_the_token_key_does_not_break_the_hook_auth(h: Harness) -> None:
    _separate_keys(h)
    token = h.announce(h.files("a.pdf")).json()["token"]
    h.client.app.state.settings.upload_token_key = SecretStr("rotated-token-key")  # type: ignore[attr-defined]
    assert rejected(h.pre_create(token, "a.pdf", len(PDF)))["status"] == 403  # old token: refused
    fresh = h.announce(h.files("b.pdf")).json()["token"]  # a new one works, same hook secret
    assert "RejectUpload" not in h.pre_create(fresh, "b.pdf", len(PDF)).text


def test_a14_t05_the_hook_secret_alone_cannot_mint_an_upload_token(h: Harness) -> None:
    _separate_keys(h)
    h.client.app.state.settings.upload_token_key = None  # type: ignore[attr-defined]
    h.client.app.state.settings.tusd_hook_secret = None  # type: ignore[attr-defined]
    assert h.announce(h.files("a.pdf")).status_code == 503
