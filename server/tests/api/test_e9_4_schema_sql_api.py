"""E9.4 `W/schema-sql` through the real app and auth chain: GET the schema.sql and its verdict, PUT
an uploaded `.sql` document as the base (T03-T05), tenant / RBAC / IDOR."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.manifests.models import GateResultRow
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.storage.base import Kind, StorageScope
from sbam_contracts.schema_sql import MAX_SOURCE_BYTES
from sqlalchemy import select
from test_auth import ADMIN_A, FDE_A2, TENANT_A, TENANT_B, problem
from test_isatvon_validate_api import Harness as WorkflowHarness

BASE = (
    "CREATE TABLE investors (id bigserial PRIMARY KEY, name text);\n"
    "CREATE TABLE investor_auth_handoff (id bigserial PRIMARY KEY, otp_match boolean);\n"
)


class Harness(WorkflowHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (WorkflowSchema, GateResultRow):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]

    def sql_document(self, text: str | bytes, **version: Any) -> uuid.UUID:
        """A project document whose latest version is a parsed, clean .sql (unless overridden)."""
        data = text.encode() if isinstance(text, str) else text
        project = uuid.UUID(self.project)
        stored = self.storage.put(data, StorageScope(TENANT_A, project), Kind.UPLOADS, "text/plain")
        with self.sessions() as s:
            doc = Document(tenant_id=TENANT_A, project_id=project, title="schema.sql")
            s.add(doc)
            s.flush()
            values: dict[str, Any] = {
                "tenant_id": TENANT_A,
                "project_id": project,
                "document_id": doc.id,
                "storage_key": stored.storage_key,
                "sha256": stored.sha256,
                "size": stored.size,
                "mime": "text/plain",
                "state": "PARSED",
                "scan_status": "CLEAN",
                "detected_type": "sql",
                "filetype_row_label": "Filetype #9",
            }
            s.add(DocumentVersion(**{**values, **version}))
            s.commit()
            return doc.id

    def path(self, workflow_id: uuid.UUID | None = None) -> str:
        return f"/projects/{self.project}/workflows/{workflow_id or self.wf}/schema-sql"

    def put(self, document_id: uuid.UUID, token: str | None = None, **kw: Any) -> Any:
        body = {"document_id": str(document_id)}
        return self.call("PUT", self.path(**kw) + "/source", token or self.fde(), json=body)

    def get_schema(self, token: str | None = None, **params: Any) -> Any:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.call("GET", self.path() + (f"?{query}" if query else ""), token or self.fde())

    def schemas(self) -> list[WorkflowSchema]:
        with self.sessions() as s:
            rows = list(s.scalars(select(WorkflowSchema).order_by(WorkflowSchema.version)))
            s.expunge_all()
            return rows

    def move_workflow(self, state: str) -> None:
        from sbam_api.workflows.models import Workflow

        with self.sessions() as s:
            row = s.get(Workflow, self.wf)
            assert row is not None
            row.state = state
            s.commit()


@pytest.fixture
def h(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Harness]:
    # a short root: tenant/project/kind/sha256 keys stay under Windows' 260-character paths
    yield Harness(tmp_path_factory.mktemp("s"))


def test_e9_4_t03_an_uploaded_sql_becomes_the_pending_base(h: Harness) -> None:
    doc = h.sql_document(BASE)
    res = h.put(doc)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["version"], body["source"], body["status"]) == (1, "UPLOADED", "PENDING")
    assert body["source_document_id"] == str(doc)
    assert body["tables"] == ["investor_auth_handoff", "investors"]
    assert body["sql"] == BASE  # the document's own bytes: nothing copied, nothing run
    [row] = h.schemas()
    with h.sessions() as s:
        actions = s.scalars(select(AuditLog.action)).all()
    assert "SCHEMA_SQL_SOURCE_SET" in actions
    got = h.get_schema()
    assert got.status_code == 200 and got.json() == body
    # the builder takes the newest PENDING upload as its base
    assert row.source == "UPLOADED" and row.status == "PENDING"


def test_e9_4_a_second_upload_is_a_new_version_and_get_can_ask_for_either(h: Harness) -> None:
    h.put(h.sql_document(BASE))
    h.put(h.sql_document("CREATE TABLE investors (id int PRIMARY KEY);"))
    assert h.get_schema().json()["version"] == 2
    assert h.get_schema(version=1).json()["tables"] == ["investor_auth_handoff", "investors"]
    problem(h.get_schema(version=3), 404, "NOT_FOUND")


def test_e9_4_t04_a_syntax_error_is_422_with_its_statement_and_no_base(h: Harness) -> None:
    doc = h.sql_document("CREATE TABLE a (id int);\nCREATE TABLE b (id int")
    body = problem(h.put(doc), 422, "SCHEMA_PARSE_ERROR")
    assert body["errors"][0]["path"] == "statement[1]"
    assert body["errors"][0]["message"].startswith("PARSE_ERROR")
    assert h.schemas() == []


def test_e9_4_t06_a_forbidden_statement_in_an_upload_is_refused(h: Harness) -> None:
    doc = h.sql_document(BASE + "COPY investors FROM PROGRAM 'curl http://x';")
    body = problem(h.put(doc), 422, "SCHEMA_PARSE_ERROR")
    assert body["errors"] == [
        {"path": "statement[2]", "message": "FORBIDDEN_STATEMENT: COPY is not allowed"}
    ]
    assert h.schemas() == []


@pytest.mark.parametrize(
    ("state", "status", "code"),
    [("BUILDING", 409, "WORKFLOW_BUILDING"), ("FROZEN", 423, "WORKFLOW_FROZEN")],
)
def test_e9_4_t05_no_base_while_building_or_frozen(
    h: Harness, state: str, status: int, code: str
) -> None:
    doc = h.sql_document(BASE)
    h.move_workflow(state)
    problem(h.put(doc), status, code)
    assert h.schemas() == []


@pytest.mark.parametrize(
    ("version", "code"),
    [
        ({"detected_type": "pdf", "filetype_row_label": "Filetype #1"}, "UNSUPPORTED_TYPE"),
        ({"scan_status": "INFECTED", "state": "REJECTED"}, "VALIDATION_ERROR"),
        ({"state": "PARSING"}, "VALIDATION_ERROR"),
        ({"size": MAX_SOURCE_BYTES + 1}, "FILE_TOO_LARGE"),
    ],
)
def test_e9_4_only_a_parsed_clean_sql_document_is_accepted(
    h: Harness, version: dict[str, Any], code: str
) -> None:
    problem(h.put(h.sql_document(BASE, **version)), 422, code)
    assert h.schemas() == []


def test_e9_4_a_non_utf8_file_is_refused(h: Harness) -> None:
    problem(h.put(h.sql_document(b"CREATE TABLE \xff (id int);")), 422, "INVALID_ENCODING")


def test_e9_4_unknown_document_or_workflow_is_404(h: Harness) -> None:
    problem(h.put(uuid7()), 404, "NOT_FOUND")
    problem(h.put(h.sql_document(BASE), workflow_id=uuid7()), 404, "NOT_FOUND")
    problem(h.get_schema(), 404, "NOT_FOUND")  # no schema yet


def test_e9_4_another_tenant_or_an_unassigned_fde_gets_404(h: Harness) -> None:
    doc = h.sql_document(BASE)
    h.put(doc)
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other_tenant, h.realm.token(FDE_A2)):
        problem(h.put(doc, token), 404, "NOT_FOUND")
        problem(h.get_schema(token), 404, "NOT_FOUND")


def test_e9_4_only_the_fde_portal_sets_the_base(h: Harness) -> None:
    # a tenant admin isn't assigned to the project: 404 like any other outsider, never 403
    problem(h.put(h.sql_document(BASE), h.realm.token(ADMIN_A)), 404, "NOT_FOUND")
    assert h.schemas() == []
