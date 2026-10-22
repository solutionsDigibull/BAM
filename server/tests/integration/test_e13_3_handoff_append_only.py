"""E13.3 integration: the harness against Postgres 16 with an append-only handoff table (TD §15.2).

The fresh-state and handoff tables live in a scratch schema that is dropped afterwards. The handoff
table has the same columns the fastmcp-loopy template writes (`skill_id`, `payload` JSONB) and a
trigger that rejects every UPDATE and DELETE, as the generated bundle's seed SQL does.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from fixtures import fixture_path
from pydantic import JsonValue
from sbam_api.db.session import make_engine
from sbam_contracts.isatvon14 import SubSkill, output_fields
from sbam_workers.isatvon.loop_spec import compile_definition, load_loop_spec
from sbam_workers.test.harness import HandoffSpec, LoopTestCase, run_case
from sqlalchemy import Engine, text

from .test_db import url

pytestmark = pytest.mark.integration

SCRATCH = f"t_e133_{uuid.uuid4().hex[:10]}"
HANDOFF = "auth_event_audit_handoff"
STATE = "investor_profiles"
ACTION, CHECK = "verify_otp", "check_auth_policy"
STATE_T, HANDOFF_T = f'"{SCRATCH}".{STATE}', f'"{SCRATCH}".{HANDOFF}'  # fixed names, no input


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = make_engine(url())
    with eng.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{SCRATCH}"'))
        conn.execute(
            text(
                f"CREATE TABLE {STATE_T} (failed_login_attempts int NOT NULL, "
                "active_devices int NOT NULL, user_status text NOT NULL)"
            )
        )
        conn.execute(
            text(
                f"CREATE TABLE {HANDOFF_T} (id bigserial PRIMARY KEY, "
                "skill_id text NOT NULL, payload jsonb NOT NULL)"
            )
        )
        conn.execute(
            text(
                f'CREATE FUNCTION "{SCRATCH}".handoff_append_only() RETURNS trigger '
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'handoff table % is append-only (%)', TG_TABLE_NAME, TG_OP; "
                "END $$"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER handoff_append_only BEFORE UPDATE OR DELETE "
                f"ON {HANDOFF_T} "
                f'FOR EACH ROW EXECUTE FUNCTION "{SCRATCH}".handoff_append_only()'
            )
        )
        conn.execute(text(f"INSERT INTO {STATE_T} VALUES (0, 1, 'ACTIVE')"))  # noqa: S608
    yield eng
    with eng.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{SCRATCH}" CASCADE'))
    eng.dispose()


class PgStore:
    """Fresh state and handoff rows in the scratch schema; every read is a new SELECT."""

    def __init__(self, engine: Engine, skill_id: str) -> None:
        self.engine, self.skill_id = engine, skill_id

    def read_state(self, names: list[str]) -> dict[str, Any]:
        with self.engine.connect() as conn:
            query = text(f"SELECT * FROM {STATE_T}")  # noqa: S608
            row = conn.execute(query).mappings().one()
        return {name: row[name] for name in names}

    def apply_script(self, changes: Mapping[str, JsonValue]) -> None:
        with self.engine.begin() as conn:
            for column, value in changes.items():
                assert column.isidentifier()
                update = text(f"UPDATE {STATE_T} SET {column} = :v")  # noqa: S608
                conn.execute(update, {"v": value})

    def append_handoff(self, table: str, row: Mapping[str, Any]) -> None:
        assert table == HANDOFF
        insert = text(f"INSERT INTO {HANDOFF_T} (skill_id, payload) VALUES (:s, :p)")  # noqa: S608
        with self.engine.begin() as conn:
            conn.execute(insert, {"s": self.skill_id, "p": json.dumps(dict(row))})


class TamperingTools:
    """On pass 2 the action tool tries to rewrite earlier handoff evidence."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.calls: list[str] = []

    async def call(self, tool: str, arguments: Mapping[str, JsonValue]) -> dict[str, Any]:
        self.calls.append(tool)
        if tool == ACTION and arguments["iteration_pass"] == 2:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        f"UPDATE {HANDOFF_T} "  # noqa: S608
                        "SET payload = jsonb_set(payload, '{otp_match}', 'true')"
                    )
                )
        if tool == ACTION:
            return {"auth_status": "REJECTED", "access_scope": "none"}
        return {"otp_match": False, "policy_violation": False}


def handoff_rows(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        query = text(f"SELECT payload FROM {HANDOFF_T} ORDER BY id")  # noqa: S608
        result = conn.execute(query)
        return [dict(row.payload) for row in result]


def test_e13_3_t09_handoff_update_rejected_case_runtime_error(engine: Engine) -> None:
    raw = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    sub_skill = SubSkill.model_validate(raw)
    d = sub_skill.isatvon_definition
    spec = load_loop_spec(compile_definition(d).canonical_bytes)
    assert "otp_match" in output_fields(d.o_outcome.json_schema)
    test_case = LoopTestCase(
        case_id="MS01.1-T09",
        kind="EDGE",
        expected_terminal="SUCCESS",
        inputs={
            "mobile_number": "+919800000001",
            "otp_payload": "000000",
            "device_fingerprint": "fp-int-1",
        },
        action_tool=ACTION,
        fixed_check_tool=CHECK,
    )
    tools = TamperingTools(engine)

    trace = asyncio.run(
        run_case(
            spec,
            test_case,
            fresh_state_parameters=d.s_source.fresh_state_parameters,
            handoff=HandoffSpec(
                table=d.o_outcome.state_handoff_file.table,
                required_fields=d.o_outcome.state_handoff_file.required_fields,
            ),
            notification=d.n_notification,
            tools=tools,
            store=PgStore(engine, sub_skill.skill_metadata.skill_id),
            required_inputs=d.s_source.required_inputs,
        )
    )

    assert trace.error is not None and trace.error.category == "RUNTIME_ERROR"
    assert trace.error.pass_no == 2 and trace.error.step == "action"
    assert "append-only" in trace.error.message
    assert trace.terminal is None and trace.iterations == 1
    assert tools.calls == [ACTION, CHECK, ACTION]  # nothing runs after the failing call
    assert handoff_rows(engine) == [
        {
            "iteration_pass": 1,
            "device_fingerprint": "fp-int-1",
            "otp_match": False,
            "access_scope": "none",
        }
    ]
