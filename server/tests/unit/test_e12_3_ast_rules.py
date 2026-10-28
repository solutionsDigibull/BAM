"""E12.3 unit tests: AST_DECOUPLING, AST_NO_MANUAL_LOOP, AST_HANDOFF, AST_IMPORTS, AST_SQL_SAFETY.

The bundle is the real assembled MS01.1 output; each negative test inserts one construct into the
action body or a helper slot, the way a wrong (or hostile) LLM answer could. `t01`..`t10` are the
issue's own test cases; the other tests cover the same rules more widely. The inserted code (even
`eval` or `os.system`) is only ever parsed: nothing here imports or executes generated code.
"""

import ast

import pytest
from codegen_cases import ms01_bodies, ms01_loop_spec, ms01_params
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_workers.build.codegen import LoadedTemplate, load_template, render_skeleton
from sbam_workers.build.codegen.bundle import assemble_bundle
from sbam_workers.build.gates.ast_checks import CheckResult
from sbam_workers.build.gates.ast_rules import run_rule_checks

ACTION_OPEN = "    # >>> LLM_BODY:action\n"
HELPERS_OPEN = "# >>> LLM_BODY:helpers\n"
CHECK = "rbac_mcp_policy_limit_validator"
ACTION = "auth_mcp_session_otp_evaluator"


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.0")


@pytest.fixture(scope="module")
def good(template: LoadedTemplate) -> dict[str, bytes]:
    params = ms01_params()
    out = assemble_bundle(
        template,
        render_skeleton(template, params),
        ms01_bodies(),
        params.get("fresh_state", []),
        ms01_loop_spec(),
    )
    assert out.bundle is not None
    return dict(out.bundle.files)


def rules(
    template: LoadedTemplate, files: dict[str, bytes], modules: tuple[str, ...] = ()
) -> dict[GateName, CheckResult]:
    trees = {n: ast.parse(b) for n, b in files.items() if n.endswith(".py")}
    results = run_rule_checks(trees, ms01_params(), template, modules)
    return {r.gate: r for r in results}


def put(files: dict[str, bytes], name: str, old: str, new: str) -> dict[str, bytes]:
    text = files[name].decode()
    assert old in text, f"{old!r} not in {name}"
    return {**files, name: text.replace(old, new, 1).encode()}


def in_action(files: dict[str, bytes], code: str, name: str = "action_tool.py") -> dict[str, bytes]:
    """`code` (already indented 4) as the first statements of the action (or check) body."""
    anchor = ACTION_OPEN if name == "action_tool.py" else ACTION_OPEN.replace("action", "check")
    return put(files, name, anchor, anchor + code)


def in_helpers(
    files: dict[str, bytes], code: str, name: str = "action_tool.py"
) -> dict[str, bytes]:
    return put(files, name, HELPERS_OPEN, HELPERS_OPEN + code)


def codes(results: dict[GateName, CheckResult], gate: GateName) -> set[str]:
    return {f.code for f in results[gate].findings}


def only_failed(results: dict[GateName, CheckResult]) -> set[GateName]:
    return {g for g, r in results.items() if r.status is GateStatus.FAILED}


# --- the good bundle -----


def test_e12_3_t10_good_bundle_passes_all_five(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    results = rules(template, good)
    assert len(results) == 5
    assert {r.status for r in results.values()} == {GateStatus.PASSED}


def test_e12_3_t09_bounded_for_over_data_is_allowed(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_action(good, "    total = 0\n    for d in range(3):\n        total += d\n")
    assert only_failed(rules(template, files)) == set()


# --- AST_DECOUPLING -----


@pytest.mark.parametrize(
    "code",
    [
        "    from . import check_tool\n",
        "    from .check_tool import " + CHECK + "\n",
        "    import check_tool\n",
        "    " + CHECK + "(mobile_number, otp_payload, device_fingerprint)\n",
        "    x = check_tool\n",
        "    from . import *\n",
        "    from .. import state\n",
        "    m = sys.modules\n",
        "    n = '" + CHECK + "'\n",
    ],
)
def test_e12_3_decoupling_action_may_not_reach_the_check_tool(
    template: LoadedTemplate, good: dict[str, bytes], code: str
) -> None:
    results = rules(template, in_action(good, code))
    assert "CROSS_IMPORT" in codes(results, GateName.AST_DECOUPLING)


def test_e12_3_decoupling_check_may_not_reach_the_action_tool(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_helpers(good, "from . import action_tool\n", "check_tool.py")
    assert "CROSS_IMPORT" in codes(rules(template, files), GateName.AST_DECOUPLING)
    files = in_action(good, "    " + ACTION + "(1, 2, 3)\n", "check_tool.py")
    assert "CROSS_IMPORT" in codes(rules(template, files), GateName.AST_DECOUPLING)


def test_e12_3_decoupling_shared_modules_are_fine(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_helpers(good, "from . import models\n", "check_tool.py")
    assert GateName.AST_DECOUPLING not in only_failed(rules(template, files))


# --- AST_NO_MANUAL_LOOP -----


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("    while True:\n        break\n", "WHILE_LOOP"),
        ("    while current:\n        current = None\n", "WHILE_LOOP"),
        (
            "    for _ in range(3):\n        try:\n            pass\n        except Exception:\n"
            "            continue\n",
            "RETRY_CONSTRUCT",
        ),
        ("    import time\n    time.sleep(1)\n", "RETRY_CONSTRUCT"),
        (
            "    import itertools\n    for _ in itertools.count():\n        break\n",
            "UNBOUNDED_ITERATION",
        ),
        ("    for _ in iter(lambda: 1, None):\n        break\n", "UNBOUNDED_ITERATION"),
    ],
)
def test_e12_3_no_manual_loop_in_the_action(
    template: LoadedTemplate, good: dict[str, bytes], code: str, expected: str
) -> None:
    results = rules(template, in_action(good, code))
    assert expected in codes(results, GateName.AST_NO_MANUAL_LOOP)


def test_e12_3_no_manual_loop_nested_while_in_a_helper(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    helper = (
        "def _h():\n    def _inner():\n        while True:\n            pass\n    return _inner\n"
    )
    results = rules(template, in_helpers(good, helper))
    assert "WHILE_LOOP" in codes(results, GateName.AST_NO_MANUAL_LOOP)


def test_e12_3_no_manual_loop_retry_decorator_and_library(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    helper = "from tenacity import retry\n\n\n@retry(stop=3)\ndef _h() -> None:\n    pass\n"
    results = rules(template, in_helpers(good, helper), modules=("tenacity",))
    assert "RETRY_DECORATOR" in codes(results, GateName.AST_NO_MANUAL_LOOP)
    helper = "def retry(f):\n    return f\n\n\n@retry\ndef _h() -> None:\n    pass\n"
    results = rules(template, in_helpers(good, helper))
    assert "RETRY_DECORATOR" in codes(results, GateName.AST_NO_MANUAL_LOOP)


def test_e12_3_no_manual_loop_recursion(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    helper = "def _h(n: int) -> int:\n    return _h(n - 1) if n else 0\n"
    results = rules(template, in_helpers(good, helper))
    assert "RECURSION" in codes(results, GateName.AST_NO_MANUAL_LOOP)


def test_e12_3_no_manual_loop_async_for(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    helper = "async def _h(xs):\n    async for _ in xs:\n        pass\n"
    results = rules(template, in_helpers(good, helper))
    assert "MANUAL_LOOP" in codes(results, GateName.AST_NO_MANUAL_LOOP)


# --- AST_HANDOFF -----


def test_e12_3_handoff_action_must_append(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    text = good["action_tool.py"].decode()
    start = text.index("    state_handoff.append(")
    end = text.index("    )\n", start) + len("    )\n")
    files = {**good, "action_tool.py": (text[:start] + text[end:]).encode()}
    assert "APPEND_MISSING" in codes(rules(template, files), GateName.AST_HANDOFF)


def test_e12_3_handoff_every_required_field(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = put(good, "action_tool.py", "        access_scope=", "        scope=")
    results = rules(template, files)
    assert "HANDOFF_FIELD_MISSING" in codes(results, GateName.AST_HANDOFF)
    assert "access_scope" in results[GateName.AST_HANDOFF].findings[0].message


def test_e12_3_handoff_no_star_or_positional_arguments(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    text = good["action_tool.py"].decode()
    start = text.index("    state_handoff.append(")
    end = text.index("    )\n", start) + len("    )\n")
    for call in (
        "    state_handoff.append(**{'iteration_pass': 1})\n",
        "    state_handoff.append(1, 2, 3, 4)\n",
    ):
        files = {**good, "action_tool.py": (text[:start] + call + text[end:]).encode()}
        assert "HANDOFF_FIELD_MISSING" in codes(rules(template, files), GateName.AST_HANDOFF)


def test_e12_3_handoff_call_in_a_nested_function_does_not_count(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    text = good["action_tool.py"].decode()
    start = text.index("    state_handoff.append(")
    end = text.index("    )\n", start) + len("    )\n")
    block = text[start:end]
    nested = "    def never():\n" + "".join("    " + ln + "\n" for ln in block.splitlines())
    files = {**good, "action_tool.py": (text[:start] + nested + text[end:]).encode()}
    assert "APPEND_MISSING" in codes(rules(template, files), GateName.AST_HANDOFF)


@pytest.mark.parametrize(
    "code",
    [
        "    x = 'UPDATE auth_event_audit_handoff SET payload = %s'\n",
        "    x = 'delete from auth_event_audit_handoff'\n",
        "    x = 'TRUNCATE auth_event_audit_handoff'\n",
        "    x = 'DROP TABLE auth_event_audit_handoff'\n",
    ],
)
def test_e12_3_handoff_table_is_append_only(
    template: LoadedTemplate, good: dict[str, bytes], code: str
) -> None:
    results = rules(template, in_action(good, code))
    assert "HANDOFF_MUTATION" in codes(results, GateName.AST_HANDOFF)


def test_e12_3_handoff_writer_private_state_is_off_limits(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_action(good, "    state_handoff._conn.execute('x')\n")
    assert "HANDOFF_PRIVATE_ACCESS" in codes(rules(template, files), GateName.AST_HANDOFF)


# --- AST_IMPORTS -----


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("    import os\n", "IMPORT_NOT_ALLOWED"),
        ("    import requests\n", "IMPORT_NOT_ALLOWED"),
        ("    import pickle\n", "FORBIDDEN_IMPORT"),
        ("    import ctypes\n", "FORBIDDEN_IMPORT"),
        ("    import marshal\n", "FORBIDDEN_IMPORT"),
        ("    import subprocess\n", "FORBIDDEN_IMPORT"),
        ("    import importlib\n", "FORBIDDEN_IMPORT"),
        ("    from os import system\n", "FORBIDDEN_IMPORT"),
        ("    import os.path\n", "IMPORT_NOT_ALLOWED"),
        ("    eval('1')\n", "FORBIDDEN_CALL"),
        ("    exec('x = 1')\n", "FORBIDDEN_CALL"),
        ("    compile('1', 'f', 'eval')\n", "FORBIDDEN_CALL"),
        ("    __import__('os')\n", "FORBIDDEN_CALL"),
        ("    f = eval\n", "FORBIDDEN_CALL"),
        ("    json.__builtins__\n", "FORBIDDEN_CALL"),
        ("    ().__class__.__subclasses__()\n", "FORBIDDEN_CALL"),
        ("    getattr(json, 'sys' + 'tem')\n", "DYNAMIC_ACCESS"),
        ("    globals()['x'] = 1\n", "DYNAMIC_ACCESS"),
    ],
)
def test_e12_3_imports_and_forbidden_names(
    template: LoadedTemplate, good: dict[str, bytes], code: str, expected: str
) -> None:
    results = rules(template, in_action(good, "    import json\n" + code))
    assert expected in codes(results, GateName.AST_IMPORTS)


def test_e12_3_imports_declared_library_is_allowed(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_helpers(good, "import httpx\n")
    assert "IMPORT_NOT_ALLOWED" in codes(rules(template, files), GateName.AST_IMPORTS)
    assert GateName.AST_IMPORTS not in only_failed(rules(template, files, modules=("httpx",)))


def test_e12_3_imports_subprocess_only_when_declared(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_helpers(good, "import subprocess\n")
    assert "FORBIDDEN_IMPORT" in codes(rules(template, files), GateName.AST_IMPORTS)
    assert GateName.AST_IMPORTS not in only_failed(rules(template, files, modules=("subprocess",)))
    # a declared library never unlocks pickle and friends
    files = in_helpers(good, "import pickle\n")
    assert "FORBIDDEN_IMPORT" in codes(
        rules(template, files, modules=("pickle",)), GateName.AST_IMPORTS
    )


def test_e12_3_imports_base_imports_are_allowed(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_helpers(good, "import json\nfrom pydantic import BaseModel\n")
    assert GateName.AST_IMPORTS not in only_failed(rules(template, files))


# --- AST_SQL_SAFETY -----


@pytest.mark.parametrize(
    "code",
    [
        "    q = f'SELECT a FROM t WHERE k = {mobile_number}'\n",
        "    q = 'SELECT a FROM t WHERE k = ' + mobile_number\n",
        "    q = 'SELECT a FROM t WHERE k = %s' % mobile_number\n",
        "    q = 'SELECT a FROM t WHERE k = {}'.format(mobile_number)\n",
        "    q = ' '.join(['SELECT a FROM t WHERE k =', mobile_number])\n",
        "    q = 'DELETE FROM t WHERE k = ' + mobile_number\n",
        '    q = f"INSERT INTO t VALUES ({otp_payload})"\n',
    ],
)
def test_e12_3_sql_built_at_runtime_is_unsafe(
    template: LoadedTemplate, good: dict[str, bytes], code: str
) -> None:
    results = rules(template, in_action(good, code))
    assert "DYNAMIC_SQL" in codes(results, GateName.AST_SQL_SAFETY)


@pytest.mark.parametrize(
    "code",
    [
        "    cur = state._conn\n    cur.execute(mobile_number)\n",
        "    q = mobile_number\n    state.x.execute(q, ())\n",
        "    state.x.executescript('SELECT 1')\n",
        "    state.x.execute()\n",
        "    state.x.executemany(f'{otp_payload}', [])\n",
    ],
)
def test_e12_3_tools_execute_only_literals(
    template: LoadedTemplate, good: dict[str, bytes], code: str
) -> None:
    results = rules(template, in_action(good, code))
    assert "SQL_NOT_LITERAL" in codes(results, GateName.AST_SQL_SAFETY)


def test_e12_3_sql_literal_with_placeholders_is_fine(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    code = "    state.x.execute('SELECT a FROM t WHERE k = %s', (mobile_number,))\n"
    assert GateName.AST_SQL_SAFETY not in only_failed(rules(template, in_action(good, code)))


def test_e12_3_sql_prose_is_not_sql(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    code = "    msg = 'please select one from the list ' + mobile_number\n"
    assert GateName.AST_SQL_SAFETY not in only_failed(rules(template, in_action(good, code)))


def test_e12_3_sql_rebound_name_is_not_trusted(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    code = "    q = 'SELECT 1'\n    q = q + mobile_number\n    state.x.execute(q, ())\n"
    results = rules(template, in_action(good, code))
    assert {"SQL_NOT_LITERAL"} <= codes(results, GateName.AST_SQL_SAFETY)


# --- attack regressions: constructs found while trying to get past the rules ---------------------


def _without_append(good: dict[str, bytes]) -> tuple[str, str, str]:
    text = good["action_tool.py"].decode()
    start = text.index("    state_handoff.append(")
    end = text.index("    )\n", start) + len("    )\n")
    return text[:start], text[start:end], text[end:]


@pytest.mark.parametrize("guard", ["    if False:\n", "    if 0:\n", "    if None:\n"])
def test_e12_3_attack_handoff_in_dead_branch_does_not_count(
    template: LoadedTemplate, good: dict[str, bytes], guard: str
) -> None:
    head, block, tail = _without_append(good)
    dead = guard + "".join("    " + ln + "\n" for ln in block.splitlines())
    files = {**good, "action_tool.py": (head + dead + tail).encode()}
    assert "APPEND_MISSING" in codes(rules(template, files), GateName.AST_HANDOFF)


def test_e12_3_attack_handoff_after_return_does_not_count(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    head, block, tail = _without_append(good)
    files = {**good, "action_tool.py": (head + "    return {}\n" + block + tail).encode()}
    assert "APPEND_MISSING" in codes(rules(template, files), GateName.AST_HANDOFF)


def test_e12_3_attack_handoff_inside_a_live_branch_counts(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    head, block, tail = _without_append(good)
    live = "    if otp_payload:\n" + "".join("    " + ln + "\n" for ln in block.splitlines())
    files = {**good, "action_tool.py": (head + live + tail).encode()}
    assert GateName.AST_HANDOFF not in only_failed(rules(template, files))


def test_e12_3_attack_mutual_recursion_is_a_loop(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    helper = (
        "def _a(n: int) -> int:\n    return _b(n)\n\n\n"
        "def _b(n: int) -> int:\n    return _a(n)\n"
    )
    results = rules(template, in_helpers(good, helper))
    assert "RECURSION" in codes(results, GateName.AST_NO_MANUAL_LOOP)


@pytest.mark.parametrize(
    "code",
    [
        "    open('/etc/passwd').read()\n",
        "    json.__dict__['sys']\n",
        "    breakpoint()\n",
        "    input()\n",
    ],
)
def test_e12_3_attack_file_and_introspection_access(
    template: LoadedTemplate, good: dict[str, bytes], code: str
) -> None:
    results = rules(template, in_action(good, "    import json\n" + code))
    assert "FORBIDDEN_CALL" in codes(results, GateName.AST_IMPORTS)


@pytest.mark.parametrize(
    "code",
    [
        "    q = 'SELECT a FROM t WHERE k = %s'\n    r = q + mobile_number\n",
        "    q = 'SELECT a FROM t WHERE k = %s'\n    q += mobile_number\n",
        "    q = 'SELECT a FROM t WHERE k = %s'\n    r = f'{q}{mobile_number}'\n",
        "    r = 'SELECT a FROM t WHERE k = X'.replace('X', mobile_number)\n",
        "    q = 'SELECT a FROM t WHERE k = %s'\n    r = q.format(mobile_number)\n",
    ],
)
def test_e12_3_attack_sql_built_through_a_variable(
    template: LoadedTemplate, good: dict[str, bytes], code: str
) -> None:
    results = rules(template, in_action(good, code))
    assert "DYNAMIC_SQL" in codes(results, GateName.AST_SQL_SAFETY)


# --- determinism -----


def test_e12_3_findings_are_deterministic(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    files = in_action(good, "    while True:\n        import os\n")
    first = [(g, [f.as_dict() for f in r.findings]) for g, r in rules(template, files).items()]
    second = [(g, [f.as_dict() for f in r.findings]) for g, r in rules(template, files).items()]
    assert first == second


# --- the issue's own scenarios (E12.3-T01 .. T08), with the exact inputs from the issue -----------


def failed_codes(results: dict[GateName, CheckResult], gate: GateName) -> set[str]:
    assert results[gate].status is GateStatus.FAILED
    return codes(results, gate)


def test_e12_3_t01_check_tool_imports_the_action_tool(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_helpers(good, "from .action_tool import verify_otp\n", "check_tool.py")
    results = rules(template, files)
    assert failed_codes(results, GateName.AST_DECOUPLING) == {"CROSS_IMPORT"}
    [finding] = results[GateName.AST_DECOUPLING].findings
    assert finding.actual == "from .action_tool import verify_otp"  # the snippet, <= 200 chars


def test_e12_3_t02_while_retry_loop_in_the_action(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_action(good, "    ok = False\n    while not ok:\n        ok = try_once()\n")
    found = failed_codes(rules(template, files), GateName.AST_NO_MANUAL_LOOP)
    assert "WHILE_LOOP" in found


def test_e12_3_t03_tenacity_retry_decorator(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    helper = "import tenacity\n\n\n@tenacity.retry\ndef _h() -> None:\n    pass\n"
    results = rules(template, in_helpers(good, helper), modules=("tenacity",))
    assert "RETRY_DECORATOR" in failed_codes(results, GateName.AST_NO_MANUAL_LOOP)


def test_e12_3_t04_action_never_appends_to_the_handoff(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    text = good["action_tool.py"].decode()
    start = text.index("    state_handoff.append(")
    end = text.index("    )\n", start) + len("    )\n")
    files = {**good, "action_tool.py": (text[:start] + text[end:]).encode()}
    assert "APPEND_MISSING" in failed_codes(rules(template, files), GateName.AST_HANDOFF)


def test_e12_3_t05_update_on_the_handoff_table(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    code = "    state.x.execute('UPDATE auth_event_audit_handoff SET payload = 1')\n"
    found = failed_codes(rules(template, in_action(good, code)), GateName.AST_HANDOFF)
    assert "HANDOFF_MUTATION" in found


def test_e12_3_t06_f_string_sql(template: LoadedTemplate, good: dict[str, bytes]) -> None:
    code = '    state.x.execute(f"SELECT * FROM t WHERE id={mobile_number}")\n'
    found = failed_codes(rules(template, in_action(good, code)), GateName.AST_SQL_SAFETY)
    assert "DYNAMIC_SQL" in found


def test_e12_3_t07_forbidden_import_and_call(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_action(good, "    import pickle\n    eval(otp_payload)\n")
    found = failed_codes(rules(template, files), GateName.AST_IMPORTS)
    assert {"FORBIDDEN_IMPORT", "FORBIDDEN_CALL"} <= found


def test_e12_3_t08_injection_derived_import_is_rejected(
    template: LoadedTemplate, good: dict[str, bytes]
) -> None:
    files = in_action(good, "    import os\n    os.system(otp_payload)\n")
    found = failed_codes(rules(template, files), GateName.AST_IMPORTS)
    assert found & {"FORBIDDEN_IMPORT", "FORBIDDEN_CALL", "IMPORT_NOT_ALLOWED"}
