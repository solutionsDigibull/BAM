"""WG-10 (#758): one FastMCP server per Workflow, namespaced tools, one image (ADR 0028).

The Sub-Skill is the real MS01.1 bundle (the E11 fixtures); a second Sub-Skill is the same manifest
under another id. `fastmcp` is not in the unit-test environment (it lives in the skill-runtime
image), so the generated `server.py` runs here against a small stand-in; the real protocol is
checked in the skill-runtime image (see the PR).
"""

import ast
import importlib
import json
import sys
import types
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from codegen_cases import ms01_bodies, ms01_loop_spec, ms01_params
from fixtures import fixture_path
from pydantic import ValidationError
from sbam_contracts.build_dashboard import GateStatus
from sbam_contracts.manifest import McpServerEntry, SkillManifest, WorkflowManifest
from sbam_workers.build.codegen import LoadedTemplate, load_template, render_skeleton
from sbam_workers.build.codegen.bundle import assemble_bundle
from sbam_workers.build.codegen.params import params_from_manifest
from sbam_workers.build.dag import Node, build_dag
from sbam_workers.build.gates.ast_checks import run_ast_checks
from sbam_workers.build.workflow_server import (
    DOCKERFILE,
    TOOLS_FILE,
    SkillPart,
    WorkflowServerBundle,
    WorkflowServerError,
    WorkflowServerInput,
    build_bundle,
    execution_order,
    imports_action_tool,
    package_name,
    render_dockerfile,
    server_name,
    tool_names,
    with_server,
)
from test_manifest import workflow

FIRST = "MS01.1_Investor_Auth_Manager"
SECOND = "MS01.2_Investor_Auth_Second"
BASE_IMAGE = "ghcr.io/solutionsdigibull/skill-runtime@sha256:" + "c" * 64
LOCK = "fastmcp==4.0.10 --hash=sha256:" + "d" * 64 + "\n"
DIGEST = "sha256:" + "a" * 64


# --- fixtures -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.1")


@pytest.fixture(scope="module")
def ms01_files(template: LoadedTemplate) -> dict[str, bytes]:
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


def manifest_as(skill_id: str) -> SkillManifest:
    text = fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8")
    data = json.loads(text.replace(FIRST, skill_id))
    return SkillManifest.sealed(data)


def part(files: dict[str, bytes], skill_id: str = FIRST) -> SkillPart:
    return SkillPart(manifest_as(skill_id), files)


def request(files: dict[str, bytes], **over: Any) -> WorkflowServerInput:
    base: dict[str, Any] = {
        "workflow_name": "Investor Onboarding",
        "skills": [part(files, FIRST), part(files, SECOND)],
        "schema_sql": "CREATE TABLE t (id int);\n",
        "base_image": BASE_IMAGE,
        "requirements_lock": LOCK,
        "vendor": {
            "vendor/pricing-kit@abc123/LICENSE": b"MIT\n",
            "vendor/pricing-kit@abc123/a.py": b"x = 1\n",
        },
    }
    return WorkflowServerInput(**{**base, **over})


@pytest.fixture(scope="module")
def built(ms01_files: dict[str, bytes]) -> WorkflowServerBundle:
    return build_bundle(request(ms01_files))


class FakeFastMCP:
    """What the generated server needs of FastMCP: a name, `tool(name=...)` and `run`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            assert name not in self.tools, name
            self.tools[name] = fn
            return fn

        return register

    def run(self, **_: Any) -> None:  # pragma: no cover - only under `python server.py`
        raise AssertionError("the server must not start on import")


def load_server(bundle: WorkflowServerBundle, root: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    for path, data in bundle.files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    fake = types.ModuleType("fastmcp")
    fake.FastMCP = FakeFastMCP  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastmcp", fake)
    monkeypatch.syspath_prepend(str(root))
    for name in [n for n in sys.modules if n in ("server", "skills") or n.startswith("skills.")]:
        monkeypatch.delitem(sys.modules, name)
    module = importlib.import_module("server")
    for name in [n for n in sys.modules if n in ("server", "skills") or n.startswith("skills.")]:
        monkeypatch.setitem(sys.modules, name, sys.modules[name])  # restored after the test
    return module


# --- WG10-T01: the tool list matches the manifests ----------------------------------------------


def test_wg10_t01_every_sub_skill_has_both_tools_with_its_schemas(
    built: WorkflowServerBundle,
) -> None:
    assert [t.name for t in built.tools] == [
        "MS01_1__action",
        "MS01_1__check",
        "MS01_2__action",
        "MS01_2__check",
    ]
    for tool in built.tools:
        contracts = manifest_as(tool.skill_id).dump()["contracts"]
        assert tool.input_schema == contracts["input_schema"]
        assert tool.output_schema == contracts["output_schema"]
    assert json.loads(built.files[TOOLS_FILE]) == [t.dump() for t in built.tools]


def test_wg10_t01_the_running_server_registers_exactly_the_listed_tools(
    built: WorkflowServerBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = load_server(built, tmp_path, monkeypatch)
    assert server.mcp.name == built.server_name == "wf_investor_onboarding"
    assert sorted(server.mcp.tools) == sorted(t.name for t in built.tools)


def test_wg10_the_server_entry_is_a_valid_manifest_entry(built: WorkflowServerBundle) -> None:
    entry = built.entry(version="1.0.0", image_digest=DIGEST)
    assert isinstance(entry, McpServerEntry)
    assert entry.name == "wf_investor_onboarding" and entry.image_digest == DIGEST
    assert [t.name for t in entry.tools] == [t.name for t in built.tools]


def test_wg10_the_image_digest_is_recorded_in_the_workflow_manifest(
    built: WorkflowServerBundle,
) -> None:
    plain = WorkflowManifest.sealed(workflow())
    entry = built.entry(version="1.0.0", image_digest=DIGEST)
    recorded = with_server(plain, entry)
    assert recorded.dump()["mcp_server"]["image_digest"] == DIGEST
    assert recorded.integrity.content_sha256 != plain.integrity.content_sha256  # the hash covers it


# --- WG10-T02: a bad input is a schema error ----------------------------------------------------


def test_wg10_t02_a_bad_input_is_refused_before_the_tool_body_runs(
    built: WorkflowServerBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = load_server(built, tmp_path, monkeypatch)
    action = server.mcp.tools["MS01_1__action"]
    with pytest.raises(ValidationError):
        action(
            mobile_number=12345, otp_payload="x", device_fingerprint="y"
        )  # a number, not a string
    with pytest.raises(TypeError):
        action(mobile_number="1")  # required inputs missing


# --- WG10-T03: the check tool does not import the action tool -----------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "from . import action_tool",
        "from .action_tool import act",
        "from skills.MS01_1 import action_tool",
        "from skills.MS01_1.action_tool import act",
        "import action_tool",
        "import skills.MS01_1.action_tool as a",
    ],
)
def test_wg10_t03_every_form_of_importing_the_action_tool_is_found(line: str) -> None:
    assert imports_action_tool(f"{line}\n")


def test_wg10_t03_a_clean_check_tool_is_not_flagged(ms01_files: dict[str, bytes]) -> None:
    assert not imports_action_tool(ms01_files["check_tool.py"].decode())


def test_wg10_t03_a_check_tool_that_imports_the_action_tool_stops_the_build(
    ms01_files: dict[str, bytes],
) -> None:
    bad = {
        **ms01_files,
        "check_tool.py": b"from . import action_tool\n" + ms01_files["check_tool.py"],
    }
    with pytest.raises(WorkflowServerError) as err:
        build_bundle(request(ms01_files, skills=[part(bad)]))
    assert err.value.code == "CHECK_IMPORTS_ACTION"


# --- WG10-T04: a rebuild reuses the unchanged layers --------------------------------------------


def layer_plan(bundle: WorkflowServerBundle) -> list[tuple[str, str]]:
    """The Dockerfile's COPY and RUN steps with the key of the files each one reads."""
    lines = bundle.files[DOCKERFILE].decode().splitlines()
    steps = [line for line in lines if line.startswith(("COPY", "RUN"))]
    keys = {
        "requirements.lock": bundle.base_layer_key,
        "vendor/": bundle.base_layer_key,
        "skills/": bundle.top_layer_key,
        "loop_specs/": bundle.top_layer_key,
        "schema.sql": bundle.top_layer_key,
    }
    return [(s, next((k for p, k in keys.items() if p in s), bundle.base_layer_key)) for s in steps]


def test_wg10_t04_dependencies_and_vendored_code_come_before_the_skills() -> None:
    text = render_dockerfile(BASE_IMAGE, has_lock=True, has_vendor=True)
    order = [text.index(x) for x in ("COPY requirements.lock", "COPY vendor/", "COPY skills/")]
    assert order == sorted(order)
    assert text.splitlines()[2] == f"FROM {BASE_IMAGE}"  # pinned by digest


def test_wg10_t04_changing_one_skill_changes_only_the_top_layer(
    ms01_files: dict[str, bytes],
) -> None:
    before = build_bundle(request(ms01_files))
    edited = {**ms01_files, "action_tool.py": ms01_files["action_tool.py"] + b"\n# changed\n"}
    after = build_bundle(
        request(ms01_files, skills=[part(edited, FIRST), part(ms01_files, SECOND)])
    )
    assert after.base_layer_key == before.base_layer_key
    assert after.top_layer_key != before.top_layer_key
    first_change = next(
        i
        for i, (a, b) in enumerate(zip(layer_plan(before), layer_plan(after), strict=True))
        if a != b
    )
    assert (
        "COPY skills/" in layer_plan(after)[first_change][0]
    )  # Docker reuses every step before it


def test_wg10_t04_changing_the_lock_or_the_vendored_code_changes_the_base_layer(
    ms01_files: dict[str, bytes],
) -> None:
    before = build_bundle(request(ms01_files))
    lock = build_bundle(request(ms01_files, requirements_lock=LOCK + "pydantic==2.12.0\n"))
    vendor = build_bundle(
        request(
            ms01_files,
            vendor={
                "vendor/pricing-kit@abc123/LICENSE": b"MIT\n",
                "vendor/pricing-kit@abc123/a.py": b"x = 2\n",
            },
        )
    )
    assert lock.base_layer_key != before.base_layer_key
    assert vendor.base_layer_key != before.base_layer_key
    assert lock.top_layer_key == vendor.top_layer_key == before.top_layer_key


def test_wg10_the_same_inputs_give_the_same_bytes(ms01_files: dict[str, bytes]) -> None:
    one, two = build_bundle(request(ms01_files)), build_bundle(request(ms01_files))
    assert one.bundle_sha256 == two.bundle_sha256 and list(one.files) == sorted(one.files)


# --- WG10-T05: two sub-skills with a data edge run in order -------------------------------------


def isatvon(outputs: list[str], inputs: list[str], next_skill: str | None = None) -> dict[str, Any]:
    return {
        "isatvon_definition": {
            "o_outcome": {
                "json_schema": {"type": "object", "properties": {o: {} for o in outputs}}
            },
            "s_source": {"required_inputs": inputs},
            "n_notification": {"next": next_skill or ""},
        }
    }


def test_wg10_t05_the_data_edge_decides_the_order_and_the_data_flows(
    ms01_files: dict[str, bytes], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B takes A's `auth_status`, so the DAG orders A first whatever the ids sort like.
    nodes = [
        Node(uuid.uuid4(), SECOND, "MS01", isatvon([], ["auth_status"])),
        Node(uuid.uuid4(), FIRST, "MS01", isatvon(["auth_status"], ["mobile_number"])),
    ]
    order = execution_order(build_dag(nodes).edges)
    assert order == [FIRST, SECOND]

    calls: list[str] = []
    stub_models = (
        "from pydantic import BaseModel\n\n\nclass SkillInput(BaseModel):\n"
        "    mobile_number: str\n    otp_payload: str\n    device_fingerprint: str\n"
    )
    params = params_from_manifest(manifest_as(FIRST))
    stubs = {
        **ms01_files,
        "models.py": stub_models.encode(),
        "action_tool.py": (
            "import sys\n\n\ndef {a}(**kw):\n    sys.modules['_wg10'].calls.append('action')\n"
            "    return {{'auth_status': 'ok', 'seen': kw['mobile_number']}}\n"
        )
        .format(a=params["action_tool"])
        .encode(),
        "check_tool.py": (
            "import sys\n\n\ndef {c}(**kw):\n    sys.modules['_wg10'].calls.append('check')\n"
            "    return {{'auth_status': 'ok'}}\n"
        )
        .format(c=params["fixed_check_tool"])
        .encode(),
    }
    log = types.ModuleType("_wg10")
    log.calls = calls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "_wg10", log)
    bundle = build_bundle(request(ms01_files, skills=[part(stubs, FIRST), part(stubs, SECOND)]))
    tools = load_server(bundle, tmp_path, monkeypatch).mcp.tools

    outputs: dict[str, dict[str, Any]] = {}
    for skill_id in order:  # the runner walks the DAG order and calls the tools
        action, check = tool_names(skill_id)
        mobile = "9999999999" if skill_id == FIRST else str(outputs[FIRST]["auth_status"])
        outputs[skill_id] = tools[action](
            mobile_number=mobile, otp_payload="1", device_fingerprint="d"
        )
        tools[check](mobile_number=mobile, otp_payload="1", device_fingerprint="d")
    assert calls == ["action", "check", "action", "check"]
    assert outputs[SECOND]["seen"] == "ok"  # B received A's output


def test_wg10_the_order_follows_next_edges_and_ties_by_skill_id() -> None:
    nodes = [
        Node(uuid.uuid4(), "MS01.2_B", "MS01", isatvon([], [])),
        Node(uuid.uuid4(), "MS01.1_A", "MS01", isatvon([], [], next_skill="MS01.2_B")),
    ]
    assert execution_order(build_dag(nodes).edges) == ["MS01.1_A", "MS01.2_B"]


# --- the server calls no model, and the Sub-Skills' own rules still hold ------------------------


def test_wg10_the_server_imports_only_fastmcp_typing_and_the_skills(
    built: WorkflowServerBundle,
) -> None:
    tree = ast.parse(built.files["server.py"])
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
    assert modules == {"__future__", "typing", "fastmcp", "skills"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.For | ast.While | ast.AsyncFor)]


def test_wg10_the_skill_files_ship_byte_for_byte_and_still_pass_the_ast_gate(
    built: WorkflowServerBundle, ms01_files: dict[str, bytes], template: LoadedTemplate
) -> None:
    for pkg in ("MS01_1", "MS01_2"):
        for name, data in ms01_files.items():
            if name != "server.py":
                assert built.files[f"skills/{pkg}/{name}"] == data
        assert f"skills/{pkg}/server.py" not in built.files  # one server per Workflow
    params = ms01_params()
    results = run_ast_checks(ms01_files, render_skeleton(template, params), template, params)
    assert [r.status for r in results] == [GateStatus.PASSED] * 3


def test_wg10_the_declarative_loop_specs_travel_with_each_skill(
    built: WorkflowServerBundle, ms01_files: dict[str, bytes]
) -> None:
    for pkg in ("MS01_1", "MS01_2"):
        assert built.files[f"loop_specs/{pkg}.json"] == ms01_files["loop_spec.json"]
    assert built.files["schema.sql"] == b"CREATE TABLE t (id int);\n"


# --- names and refusals -------------------------------------------------------------------------


def test_wg10_names() -> None:
    assert tool_names(FIRST) == ("MS01_1__action", "MS01_1__check")
    assert package_name("MS12.30_X") == "MS12_30"
    assert server_name("Northwind RFQ v1.0") == "wf_northwind_rfq_v1_0"
    with pytest.raises(WorkflowServerError):
        package_name("login")
    with pytest.raises(WorkflowServerError):
        server_name("---")


def test_wg10_an_unpinned_base_image_is_refused() -> None:
    with pytest.raises(WorkflowServerError) as err:
        render_dockerfile("python:3.12", has_lock=False, has_vendor=False)
    assert err.value.code == "BASE_IMAGE_NOT_PINNED"


def test_wg10_the_dockerfile_leaves_out_steps_it_does_not_need() -> None:
    text = render_dockerfile(BASE_IMAGE, has_lock=False, has_vendor=False)
    assert "requirements.lock" not in text and "vendor/" not in text and "COPY skills/" in text


@pytest.mark.parametrize(
    "vendor",
    [
        {"other/a.py": b""},
        {"vendor/../etc/passwd": b""},
        {"/vendor/a.py": b""},
        {"vendor/a b.py": b""},
    ],
)
def test_wg10_a_vendored_path_must_stay_inside_vendor(
    ms01_files: dict[str, bytes], vendor: dict[str, bytes]
) -> None:
    with pytest.raises(WorkflowServerError) as err:
        build_bundle(request(ms01_files, vendor=vendor))
    assert err.value.code == "PATH_INVALID"


def test_wg10_incomplete_or_duplicate_skills_are_refused(ms01_files: dict[str, bytes]) -> None:
    no_loop = {k: v for k, v in ms01_files.items() if k != "loop_spec.json"}
    for skills, code in (
        ([part(no_loop)], "SKILL_FILES_MISSING"),
        ([part(ms01_files), part(ms01_files)], "SKILL_DUPLICATE"),
        ([], "NO_SKILLS"),
    ):
        with pytest.raises(WorkflowServerError) as err:
            build_bundle(request(ms01_files, skills=skills))
        assert err.value.code == code
