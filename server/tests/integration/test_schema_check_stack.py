"""E9.4-T01/T02 against the Compose stack: worker-build applies a merged schema.sql to the
`schema-check` PostgreSQL (a throwaway database it drops), every declared table is there, the
handoff table's guard rejects UPDATE, DELETE and TRUNCATE and allows INSERT; and schema-check is
not reachable from the other workers. No model is involved."""

import json

import pytest

from .conftest import Compose

pytestmark = pytest.mark.integration

# Runs inside worker-build: merge two Sub-Skills' fragments, apply them, then T02 by hand.
SCRIPT = """
import json, os, uuid
import psycopg
from psycopg.conninfo import make_conninfo
from sbam_workers.build.schema_sql.apply import apply
from sbam_workers.build.schema_sql.merge import declared, merge
from sbam_api.schema_sql.validate import check, split

HANDOFF = "investor_auth_handoff"
INVESTORS = "CREATE TABLE investors (id bigserial PRIMARY KEY, name text)"
GUARDED = (
    "CREATE TABLE investor_auth_handoff (id bigserial PRIMARY KEY, "
    "investor_id bigint REFERENCES investors (id), otp_match boolean DEFAULT false)"
)


def sub(skill, tables, handoff=None):
    shf = {"table": handoff, "required_fields": ["otp_match"]}
    o = {"state_handoff_file": shf} if handoff else {}
    v = {"tables": tables}
    return {"skill_metadata": {"skill_id": skill},
            "isatvon_definition": {"v_variables": v, "o_outcome": o}}


want = declared([sub("MS01.1", ["investors"]), sub("MS01.2", [HANDOFF], HANDOFF)])
merged = merge(want, None, {"MS01.1": check(INVESTORS), "MS01.2": check(GUARDED)})
statements = list(split(merged.sql))
dsn = os.environ["SCHEMA_CHECK_DSN"]
found = apply(dsn, statements, merged.tables, [HANDOFF])
out = {"merge": [p.code.value for p in merged.problems], "apply": [p.code.value for p in found]}

db = "sbam_check_t02_" + uuid.uuid4().hex
probes = [
    ("update", "UPDATE investor_auth_handoff SET otp_match = false"),
    ("delete", "DELETE FROM investor_auth_handoff"),
    ("truncate", "TRUNCATE investor_auth_handoff"),
]
with psycopg.connect(dsn, autocommit=True) as admin:
    admin.execute("CREATE DATABASE " + db)
    try:
        with psycopg.connect(make_conninfo(dsn, dbname=db), autocommit=True) as conn:
            for s in statements:
                conn.execute(s.encode())
            conn.execute("INSERT INTO investors (name) VALUES ('a')")
            conn.execute("INSERT INTO investor_auth_handoff (investor_id) VALUES (1)")
            for name, probe in probes:
                try:
                    conn.execute(probe)
                    out[name] = "allowed"
                except psycopg.Error as exc:
                    out[name] = exc.diag.message_primary
            count = "SELECT count(*) FROM investor_auth_handoff"
            out["rows"] = conn.execute(count).fetchone()[0]
    finally:
        admin.execute("DROP DATABASE IF EXISTS " + db + " WITH (FORCE)")
    left = "SELECT count(*) FROM pg_database WHERE datname LIKE 'sbam_check%'"
    out["left"] = admin.execute(left).fetchone()[0]
print(json.dumps(out))
"""


def run(compose: Compose, service: str, *command: str) -> tuple[int, str]:
    result = compose("exec", "-T", service, *command, timeout=180)
    if "is not running" in result.stderr or "No such service" in result.stderr:
        pytest.skip(f"{service} is not running")
    return result.returncode, result.stdout + result.stderr


def test_e9_4_t01_t02_worker_build_applies_the_schema_and_the_guard_holds(
    compose: Compose,
) -> None:
    code, out = run(compose, "worker-build", "python", "-c", SCRIPT)
    assert code == 0, out
    result = json.loads(out.strip().splitlines()[-1])
    assert result["merge"] == [] and result["apply"] == []  # T01: PASSED, both tables, guard
    for op in ("update", "delete", "truncate"):  # T02
        assert result[op].startswith("sbam_handoff_insert_only"), result
    assert result["rows"] == 1  # the INSERT went through, nothing removed it
    assert result["left"] == 0  # no throwaway database is left behind


def test_e9_4_schema_check_is_not_reachable_from_other_workers(compose: Compose) -> None:
    code, out = run(
        compose,
        "worker-llm",
        "python",
        "-c",
        "import socket; socket.create_connection(('schema-check', 5432), timeout=3)",
    )
    assert code != 0 and ("gaierror" in out or "Name or service not known" in out), out
