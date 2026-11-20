"""A-02 (#428), decision 5 of ADR 0026: fresh-state SQL runs as a role that can only read.

The runner executes a bundle's `QUERIES` itself. These tests put queries into the real hardened
`sandbox-runner` container that the static check (`state_sql`) would refuse, and show the database
refuses them too: a role without `pg_read_server_files`, a read-only session and a statement
timeout.
Honest queries (MS01.1) keep working in `test_e13_2_runner_container.py`.

Needs Docker and `skill-runtime:local` (`make runtime-registry`); not part of the unit run.
"""

import re
from typing import Any

import pytest
from codegen_cases import ms01_bodies, ms01_loop_spec, ms01_params
from sbam_contracts.sandbox import FailureCategory
from sbam_workers.build.codegen import load_template, render_skeleton
from sbam_workers.build.codegen.bundle import assemble_bundle

from .test_e13_2_runner_container import (  # noqa: F401 - agent_url is a fixture
    agent_url,
    case,
    insert_auth,
    run,
    run_input,
)

pytestmark = pytest.mark.integration


def state_py_with(queries: dict[str, str]) -> str:
    """The assembled MS01.1 `state.py` with its `QUERIES` literal replaced."""
    params = ms01_params()
    template = load_template("fastmcp-loopy", "1.0.1")
    assembled = assemble_bundle(
        template,
        render_skeleton(template, params),
        ms01_bodies(),
        params.get("fresh_state", []),
        ms01_loop_spec(),
    )
    assert assembled.bundle is not None, assembled.findings
    text = assembled.bundle.files["state.py"].decode()
    literal = "QUERIES = {\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in queries.items()) + "}\n"
    changed, count = re.subn(r"QUERIES = \{.*?\n\}\n", lambda _: literal, text, flags=re.S)
    assert count == 1
    return changed


def one_case() -> list[Any]:
    return [case("pass-first", "SUCCESS", insert_auth(0, "active"))]


def failure_of(agent_url: str, query: str) -> str:  # noqa: F811
    queries = {
        "failed_login_attempts": query,
        "active_devices": "SELECT count(*) FROM investor_devices WHERE mobile_number = %s",
        "user_status": "SELECT user_status FROM investor_auth WHERE mobile_number = %s",
    }
    result = run(agent_url, run_input(files={"state.py": state_py_with(queries)}, cases=one_case()))
    assert not result.classified.passed, result.classified
    assert result.classified.category is FailureCategory.RUNTIME_ERROR, result.classified
    assert result.exit_code == 0, "a refused query must fail its case, not the runner"
    return result.classified.findings()[0].message


def test_a02_t10_a_state_query_cannot_read_a_server_file(agent_url: str) -> None:  # noqa: F811
    message = failure_of(
        agent_url, "SELECT pg_read_file('/etc/hostname') WHERE %s::text IS NOT NULL"
    )
    assert "InsufficientPrivilege" in message, message


def test_a02_t11_a_state_query_cannot_import_a_large_object(
    agent_url: str,  # noqa: F811
) -> None:
    message = failure_of(agent_url, "SELECT lo_import('/etc/hostname') WHERE %s::text IS NOT NULL")
    assert "InsufficientPrivilege" in message, message


def test_a02_t12_a_state_query_cannot_sleep_past_the_statement_timeout(
    agent_url: str,  # noqa: F811
) -> None:
    message = failure_of(agent_url, "SELECT pg_sleep(60) WHERE %s::text IS NOT NULL")
    assert "QueryCanceled" in message, message


def test_a02_t13_a_state_query_cannot_read_the_role_catalog(
    agent_url: str,  # noqa: F811
) -> None:
    message = failure_of(agent_url, "SELECT rolpassword FROM pg_authid WHERE %s::text IS NOT NULL")
    assert "InsufficientPrivilege" in message, message
