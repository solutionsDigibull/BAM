"""E11.1 unit tests: fastmcp-loopy skeleton templates, hashed registry, golden snapshots.

Golden files live in tests/golden/<fixture>/<file>.py.golden (the suffix keeps ruff, black and
mypy off generated skeletons). Regenerate after an intended template change, with a new template
version and registry entry:

    UPDATE_GOLDEN=1 uv run pytest tests/unit/test_codegen_templates.py -k golden
"""

import ast
import copy
import hashlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import jinja2
import jinja2.meta
import pytest
import yaml
from fixtures import fixture_path
from hypothesis import given, settings
from hypothesis import strategies as st
from sbam_contracts import state_machines
from sbam_contracts.manifest import SkillManifest
from sbam_workers.build.codegen import (
    BUILD_ERROR_EVENT,
    LoadedTemplate,
    RenderedSkeleton,
    TemplateError,
    TemplateErrorCode,
    load_template,
    params_from_manifest,
    render_manifest,
    render_skeleton,
    write_skeleton,
)
from sbam_workers.build.codegen import render as render_module
from sbam_workers.build.codegen.render import docstring, environment, python_literal
from sbam_workers.build.codegen.store import default_templates_dir, released_hash, tree_sha256

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
PY_FILES = (
    "models.py",
    "state.py",
    "state_handoff.py",
    "action_tool.py",
    "check_tool.py",
    "server.py",
)
INJECTION = 'x"""\nimport os\nos.system("id")\n"""'
TEMPLATES = default_templates_dir()


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.0")


def manifest_dict() -> dict[str, Any]:
    data = json.loads(fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def ms01_params() -> dict[str, Any]:
    return params_from_manifest(SkillManifest.model_validate(manifest_dict()))


def edge_params() -> dict[str, Any]:
    data = json.loads((GOLDEN / "edge_enum_optional" / "params.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def render(template: LoadedTemplate, **changes: Any) -> RenderedSkeleton:
    params = edge_params()
    params.update(changes)
    return render_skeleton(template, params)


def slots(source: str) -> list[tuple[str, str]]:
    """(kind, slot) for every LLM_BODY marker line, in file order."""
    found = re.findall(r"^\s*# (>>>|<<<) LLM_BODY:(\w+)$", source, flags=re.MULTILINE)
    return [(kind, name) for kind, name in found]


def check_golden(fixture: str, skeleton: RenderedSkeleton) -> None:
    directory = GOLDEN / fixture
    if os.environ.get("UPDATE_GOLDEN") == "1":
        for name, text in skeleton.files.items():
            (directory / f"{name}.golden").write_bytes(text.encode("utf-8"))
    expected = {p.name.removesuffix(".golden") for p in directory.glob("*.py.golden")}
    assert expected == set(skeleton.files), "golden file set differs from the template's files"
    for name, text in skeleton.files.items():
        golden = (directory / f"{name}.golden").read_bytes()
        assert text.encode("utf-8") == golden, f"{fixture}/{name} differs from its golden snapshot"


def load_module(path: Path) -> Any:
    name = f"_skeleton_{uuid.uuid4().hex}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # pydantic resolves postponed annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def copy_templates(tmp_path: Path) -> Path:
    """A private copy of the template tree and registry that a test may modify."""
    root = tmp_path / "templates"
    shutil.copytree(TEMPLATES, root)
    return root


def rehash(root: Path, name: str = "fastmcp-loopy", version: str = "1.0.0") -> None:
    """Write the registry entry for the (modified) copy, as a release would."""
    files = {
        p.relative_to(root / name / version).as_posix(): p.read_bytes()
        for p in (root / name / version).rglob("*")
        if p.is_file()
    }
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    registry["templates"][name][version] = tree_sha256(files)
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")


def error_of(call: Callable[[], object]) -> TemplateError:
    with pytest.raises(TemplateError) as info:
        call()
    return info.value


# --- T01 determinism and golden snapshots --------------------------------------------------------


def test_e11_1_t01_ms01_1_renders_twice_byte_identical_and_equals_golden(
    template: LoadedTemplate,
) -> None:
    manifest = SkillManifest.model_validate(manifest_dict())
    first = render_manifest(template, manifest)
    second = render_manifest(load_template("fastmcp-loopy", "1.0.0"), manifest)
    assert list(first.files) == list(PY_FILES)
    assert {n: t.encode() for n, t in first.files.items()} == {
        n: t.encode() for n, t in second.files.items()
    }
    check_golden("ms01_1", first)
    for name, text in first.files.items():
        ast.parse(text, filename=name)


def test_e11_1_t01_edge_fixture_renders_twice_byte_identical_and_equals_golden(
    template: LoadedTemplate,
) -> None:
    first, second = render(template), render(template)
    assert first.files == second.files
    check_golden("edge_enum_optional", first)


def test_e11_1_t01_render_manifest_accepts_a_plain_mapping(template: LoadedTemplate) -> None:
    assert (
        render_manifest(template, manifest_dict()).files
        == render_manifest(template, SkillManifest.model_validate(manifest_dict())).files
    )


def test_e11_1_t01_ms01_1_mapping_uses_isatvon_structured_form() -> None:
    params = ms01_params()
    assert params["action_tool"] == "auth_mcp_session_otp_evaluator"
    assert params["fixed_check_tool"] == "rbac_mcp_policy_limit_validator"
    assert params["fresh_state"] == ["failed_login_attempts", "active_devices", "user_status"]
    assert params["gate_outputs"] == ["otp_match", "policy_violation"]
    assert [i["name"] for i in params["inputs"] if i["required"]] == [
        "mobile_number",
        "otp_payload",
        "device_fingerprint",
    ]
    assert params["parameters"] == [{"name": "max_device_limit", "value": 3}]


def test_e11_1_t01_registry_entry_is_the_sorted_tree_hash(template: LoadedTemplate) -> None:
    root = TEMPLATES / "fastmcp-loopy" / "1.0.0"
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        digest.update(f"{rel}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    ref = render(template).template
    assert (ref.name, ref.version, ref.sha256) == ("fastmcp-loopy", "1.0.0", digest.hexdigest())
    assert ref.sha256 == released_hash(TEMPLATES, "fastmcp-loopy", "1.0.0")


def test_e11_1_registry_covers_every_template_version_and_nothing_else() -> None:
    registry = json.loads((TEMPLATES / "registry.json").read_text(encoding="utf-8"))["templates"]
    on_disk = {
        (n.name, v.name)
        for n in TEMPLATES.iterdir()
        if n.is_dir()
        for v in n.iterdir()
        if v.is_dir()
    }
    assert on_disk == {(n, v) for n, versions in registry.items() for v in versions}
    for name, version in sorted(on_disk):
        assert load_template(name, version).sha256 == registry[name][version]


def test_e11_1_template_yaml_context_lists_match_the_template_sources(
    template: LoadedTemplate,
) -> None:
    env = environment(template)
    for entry in template.files:
        source = template.sources[entry.template]
        used = jinja2.meta.find_undeclared_variables(env.parse(source))
        assert used == set(entry.context), entry.template


def test_e11_1_template_yaml_declares_isatvon_1_4_and_semver(template: LoadedTemplate) -> None:
    spec = yaml.safe_load((TEMPLATES / "fastmcp-loopy" / "1.0.0" / "template.yaml").read_text())
    assert spec["supports_isatvon"] == ["1.4"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", spec["version"])
    assert set(spec["base_imports"]) == set(template.base_imports)


# --- T02 slots -----------------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["ms01_1", "edge_enum_optional"])
def test_e11_1_t02_each_llm_file_has_exactly_one_slot_with_begin_and_end_markers(
    template: LoadedTemplate, fixture: str
) -> None:
    skeleton = (
        render_manifest(template, manifest_dict()) if fixture == "ms01_1" else render(template)
    )
    for file, slot in (
        ("action_tool.py", "action"),
        ("check_tool.py", "check"),
        ("state.py", "state_queries"),
    ):
        markers = slots(skeleton.files[file])
        assert markers.count((">>>", slot)) == 1, file
        assert markers.count(("<<<", slot)) == 1, file
        assert markers.index((">>>", slot)) < markers.index(("<<<", slot))


def test_e11_1_t02_slots_match_template_yaml_and_template_owned_files_have_none(
    template: LoadedTemplate,
) -> None:
    skeleton = render(template)
    for entry in template.files:
        found = [name for kind, name in slots(skeleton.files[entry.output]) if kind == ">>>"]
        assert found == list(entry.slots), entry.output
        closes = [name for kind, name in slots(skeleton.files[entry.output]) if kind == "<<<"]
        assert closes == found
    for name in ("models.py", "state_handoff.py", "server.py"):
        assert slots(skeleton.files[name]) == []


def test_e11_1_t02_unfilled_slots_raise_not_implemented_and_parse(
    template: LoadedTemplate,
) -> None:
    skeleton = render_manifest(template, manifest_dict())
    for file, slot in (
        ("action_tool.py", "action"),
        ("check_tool.py", "check"),
        ("state.py", "state_queries"),
    ):
        tree = ast.parse(skeleton.files[file])
        raised = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Raise)
            and isinstance(n.exc, ast.Call)
            and ast.unparse(n.exc.func) == "NotImplementedError"
            and ast.unparse(n.exc.args[0]) == repr(f"LLM_BODY:{slot}")
        ]
        assert len(raised) == 1, (file, slot)


def test_e11_1_check_tool_never_imports_the_action_tool(template: LoadedTemplate) -> None:
    tree = ast.parse(render_manifest(template, manifest_dict()).files["check_tool.py"])
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert imported == {"annotations", "Any", "state"}


# --- T03 injection -------------------------------------------------------------------------------


def assert_text_only_in_docstrings(source: str) -> ast.Module:
    tree = ast.parse(source)
    modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "os" not in names | modules
    assert not [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "system"
    ]
    return tree


def test_e11_1_t03_action_step_with_triple_quotes_stays_inside_the_docstring(
    template: LoadedTemplate,
) -> None:
    skeleton = render(template, action_step=INJECTION)
    source = skeleton.files["action_tool.py"]
    tree = assert_text_only_in_docstrings(source)
    assert not [n for n in tree.body if isinstance(n, ast.Import)]
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    doc = ast.get_docstring(function)
    assert doc is not None
    assert 'x""" import os os.system("id")' in " ".join(doc.split())
    assert [type(n).__name__ for n in tree.body[:2]] == ["Expr", "ImportFrom"]


@pytest.mark.parametrize("field", ["action_step", "acceptance_check", "skill_id"])
def test_e11_1_t03_no_manifest_text_field_can_break_out_of_its_literal(
    template: LoadedTemplate, field: str
) -> None:
    skeleton = render(template, **{field: INJECTION.replace("\n", " ")})
    for source in skeleton.files.values():
        assert_text_only_in_docstrings(source)
        assert not [ln for ln in source.splitlines() if ln.strip().startswith(("import os", "os."))]


def test_e11_1_t03_string_parameter_value_and_enum_value_are_literals(
    template: LoadedTemplate,
) -> None:
    payload = "'); import os; os.system('id') #"
    params = edge_params()
    params["parameters"] = [{"name": "banner", "value": payload}]
    params["inputs"][0]["enum"] = [payload, 'say "hi"', "back\\slash"]
    skeleton = render_skeleton(template, params)
    for source in skeleton.files.values():
        assert_text_only_in_docstrings(source)
    state_tree = ast.parse(skeleton.files["state.py"])
    assigned = next(
        n.value
        for n in state_tree.body
        if isinstance(n, ast.AnnAssign) and ast.unparse(n.target) == "PARAMETERS"
    )
    assert isinstance(assigned, ast.Dict)
    assert ast.literal_eval(assigned.values[0]) == payload
    model = ast.parse(skeleton.files["models.py"])
    literal = next(
        n.annotation
        for c in model.body
        if isinstance(c, ast.ClassDef) and c.name == "SkillInput"
        for n in c.body
        if isinstance(n, ast.AnnAssign) and ast.unparse(n.target) == "channel"
    )
    assert isinstance(literal, ast.BinOp)  # Literal[...] | None
    assert ast.literal_eval(literal.left.slice) == (payload, 'say "hi"', "back\\slash")  # type: ignore[attr-defined]


def test_e11_1_t03_nul_and_control_characters_are_escaped_never_raw(
    template: LoadedTemplate,
) -> None:
    text = "a\x00b\x1b[31m\u202e\u2028end\r"
    skeleton = render(template, action_step=text, acceptance_check=text)
    for source in skeleton.files.values():
        assert "\x00" not in source and "\u202e" not in source and "\u2028" not in source
        assert "\r" not in source and "\x1b" not in source
        ast.parse(source)
    tree = ast.parse(skeleton.files["action_tool.py"])
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    doc = ast.get_docstring(function, clean=False)
    assert doc is not None and text in doc


def test_e11_1_t03_multi_line_skill_id_is_rejected(template: LoadedTemplate) -> None:
    err = error_of(lambda: render(template, skill_id=INJECTION))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID


def test_e11_1_t03_lone_surrogate_is_rejected(template: LoadedTemplate) -> None:
    err = error_of(lambda: render(template, action_step="bad \ud800 text"))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID


@given(st.text(alphabet=st.characters(blacklist_categories=["Cs"])))
@settings(max_examples=300, deadline=None)
def test_e11_1_t03_literal_filters_round_trip_any_text(text: str) -> None:
    assert ast.literal_eval(python_literal(text)) == text
    assert ast.literal_eval('"""' + docstring(text) + '"""') == text


@pytest.mark.parametrize("text", ["", '"', '""', '"""', "\\", '\\"', "'", "'\"", "\\'", '"\\"""\\'])
def test_e11_1_t03_literal_filters_handle_quote_and_backslash_edges(text: str) -> None:
    assert ast.literal_eval(python_literal(text)) == text
    assert ast.literal_eval('"""' + docstring(text) + '"""') == text
    assert python_literal(text).startswith('"')


@pytest.mark.parametrize("value", [float("nan"), float("inf"), [1], {"a": 1}, b"x", object()])
def test_e11_1_t03_python_literal_refuses_non_scalar_values(value: object) -> None:
    err = error_of(lambda: python_literal(value))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID


def test_e11_1_t03_sandbox_blocks_attribute_escape(template: LoadedTemplate) -> None:
    env = environment(template)
    probe = env.from_string("{{ x.__class__.__mro__[1].__subclasses__() }}")
    with pytest.raises(jinja2.exceptions.SecurityError):
        probe.render(x="text")
    assert env.autoescape is False
    assert env.undefined is jinja2.StrictUndefined


# --- T04 strict undefined, malformed manifest, nothing written ------------------------------------


def test_e11_1_t04_manifest_without_output_schema_is_a_template_error_and_writes_nothing(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    data = manifest_dict()
    del data["contracts"]["output_schema"]
    out = tmp_path / "skeleton"
    err = error_of(lambda: write_skeleton(render_manifest(template, data), out))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID
    assert any("output_schema" in f for f in err.findings)
    assert err.event == BUILD_ERROR_EVENT == "template_error"
    assert not out.exists()


def test_e11_1_t04_params_missing_a_required_key_fail_before_rendering(
    template: LoadedTemplate, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render_module, "environment", _fail_if_called)
    params = edge_params()
    del params["inputs"]
    err = error_of(lambda: render_skeleton(template, params))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID
    assert any("inputs" in f for f in err.findings)


def _fail_if_called(*_: object, **__: object) -> None:
    raise AssertionError("render must not start for invalid params")


def test_e11_1_t04_strict_undefined_makes_a_template_typo_a_render_error(
    tmp_path: Path,
) -> None:
    root = copy_templates(tmp_path)
    path = root / "fastmcp-loopy" / "1.0.0" / "server.py.j2"
    path.write_text(path.read_text() + "\n# {{ not_a_context_variable }}\n", encoding="utf-8")
    rehash(root)
    loaded = load_template("fastmcp-loopy", "1.0.0", root=root)
    out = tmp_path / "skeleton"
    err = error_of(lambda: write_skeleton(render_skeleton(loaded, edge_params()), out))
    assert err.code is TemplateErrorCode.TEMPLATE_RENDER_ERROR
    assert "not_a_context_variable" in err.message
    assert not out.exists()


def test_e11_1_t04_template_error_is_the_building_to_build_error_event() -> None:
    target = state_machines.can_transition(
        state_machines.MachineName.SUB_SKILL,
        state_machines.SubSkillState.BUILDING,
        TemplateError.event,
        state_machines.Actor.SYSTEM,
    )
    assert target == state_machines.SubSkillState.BUILD_ERROR
    for code in TemplateErrorCode:
        assert TemplateError(code, "x").event == "template_error"


def test_e11_1_t04_manifest_with_unparsable_gate_is_a_template_error_not_a_crash(
    template: LoadedTemplate,
) -> None:
    data = manifest_dict()
    data["sbam_build_record"]["isatvon_detail"]["v_variables"]["success_gate"] = "otp_match =="
    err = error_of(lambda: render_manifest(template, data))
    assert isinstance(err, TemplateError)


# --- T05 identifiers -----------------------------------------------------------------------------

BAD_TOOL_NAMES = [
    "verify-otp",
    "class",
    "list",
    "dict",
    "None",
    "Verify",
    "1abc",
    "",
    "a b",
    "verify_otp\n",
    "verify.otp",
    "a" * 64,
    "state",
    "action_tool",
    "__init__",
    "ünïcode",
    "x; import os",
]


@pytest.mark.parametrize("name", BAD_TOOL_NAMES)
@pytest.mark.parametrize("key", ["action_tool", "fixed_check_tool"])
def test_e11_1_t05_invalid_tool_names_are_rejected_before_render(
    template: LoadedTemplate, monkeypatch: pytest.MonkeyPatch, key: str, name: str
) -> None:
    monkeypatch.setattr(render_module, "environment", _fail_if_called)
    err = error_of(lambda: render(template, **{key: name}))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID
    assert err.findings


@pytest.mark.parametrize(
    ("section", "name"),
    [
        ("inputs", "class"),
        ("inputs", "model_config"),
        ("inputs", "_private"),
        ("inputs", "json"),
        ("inputs", "schema"),
        ("outputs", "def"),
        ("outputs", "dict"),
        ("inputs", "Upper"),
    ],
)
def test_e11_1_t05_invalid_field_names_are_rejected(
    template: LoadedTemplate, section: str, name: str
) -> None:
    params = edge_params()
    params[section].append({"name": name, "type": "string", "required": False})
    err = error_of(lambda: render_skeleton(template, params))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID
    assert any(name in f for f in err.findings)


@pytest.mark.parametrize("key", ["fresh_state", "required_fields", "gate_outputs"])
@pytest.mark.parametrize("name", ["class", "verify-otp", "x\n", "state"])
def test_e11_1_t05_invalid_names_in_identifier_lists_are_rejected(
    template: LoadedTemplate, key: str, name: str
) -> None:
    err = error_of(lambda: render(template, **{key: [name]}))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID


@pytest.mark.parametrize("table", ["audit; DROP TABLE x", "a-b", "select", "T", ""])
def test_e11_1_t05_handoff_table_must_be_a_plain_identifier(
    template: LoadedTemplate, table: str
) -> None:
    err = error_of(lambda: render(template, handoff_table=table))
    assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID


def test_e11_1_t05_every_finding_is_reported_not_just_the_first(template: LoadedTemplate) -> None:
    err = error_of(lambda: render(template, action_tool="class", fixed_check_tool="def"))
    assert len(err.findings) >= 2


def test_e11_1_t05_structural_rules(template: LoadedTemplate) -> None:
    cases: dict[str, Callable[[dict[str, Any]], object]] = {
        "same tool twice": lambda p: p.update(fixed_check_tool=p["action_tool"]),
        "tool equals input": lambda p: p.update(action_tool="mobile_number"),
        "duplicate input": lambda p: p["inputs"].append(dict(p["inputs"][1])),
        "no required input": lambda p: [f.update(required=False) for f in p["inputs"]],
        "no inputs": lambda p: p.update(inputs=[]),
        "enum type mismatch": lambda p: p["inputs"][0].update(enum=[1, 2]),
        "enum duplicate": lambda p: p["inputs"][0].update(enum=["a", "a"]),
        "enum on boolean": lambda p: p["inputs"][4].update(enum=["a"]),
        "items on scalar": lambda p: p["inputs"][0].update(items="string"),
        "unknown type": lambda p: p["inputs"][0].update(type="date"),
        "unknown key": lambda p: p.update(extra="x"),
        "nan parameter": lambda p: p.update(parameters=[{"name": "n", "value": float("nan")}]),
        "duplicate parameter": lambda p: p.update(
            parameters=[{"name": "n", "value": 1}, {"name": "n", "value": 2}]
        ),
        "too many inputs": lambda p: p.update(
            inputs=[{"name": f"f{i}", "type": "string", "required": True} for i in range(1001)]
        ),
        "empty skill id": lambda p: p.update(skill_id=""),
    }
    for label, mutate in cases.items():
        params = edge_params()
        mutate(params)
        err = error_of(lambda p=params: render_skeleton(template, p))  # type: ignore[misc]
        assert err.code is TemplateErrorCode.TEMPLATE_PARAM_INVALID, label


# --- T06 immutability ----------------------------------------------------------------------------


def test_e11_1_t06_unmodified_copy_loads(tmp_path: Path) -> None:
    root = copy_templates(tmp_path)
    assert load_template("fastmcp-loopy", "1.0.0", root=root).sha256 == released_hash(
        root, "fastmcp-loopy", "1.0.0"
    )


@pytest.mark.parametrize("victim", ["server.py.j2", "state_handoff.py.j2", "template.yaml"])
def test_e11_1_t06_one_changed_byte_without_a_version_bump_fails_the_hash_check(
    tmp_path: Path, victim: str
) -> None:
    root = copy_templates(tmp_path)
    path = root / "fastmcp-loopy" / "1.0.0" / victim
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01 if data[-1] != 0x0A else 0x20  # flip one bit of the last byte
    path.write_bytes(bytes(data))
    err = error_of(lambda: load_template("fastmcp-loopy", "1.0.0", root=root))
    assert err.code is TemplateErrorCode.TEMPLATE_HASH_MISMATCH
    assert "bump the version" in err.message


def test_e11_1_t06_an_added_or_removed_file_fails_the_hash_check(tmp_path: Path) -> None:
    root = copy_templates(tmp_path)
    directory = root / "fastmcp-loopy" / "1.0.0"
    (directory / "extra.py.j2").write_text("x", encoding="utf-8")
    assert (
        error_of(lambda: load_template("fastmcp-loopy", "1.0.0", root=root)).code
        is TemplateErrorCode.TEMPLATE_HASH_MISMATCH
    )
    (directory / "extra.py.j2").unlink()
    (directory / "models.py.j2").unlink()
    assert (
        error_of(lambda: load_template("fastmcp-loopy", "1.0.0", root=root)).code
        is TemplateErrorCode.TEMPLATE_HASH_MISMATCH
    )


def test_e11_1_t06_a_version_bump_with_a_new_registry_entry_loads(tmp_path: Path) -> None:
    root = copy_templates(tmp_path)
    shutil.copytree(root / "fastmcp-loopy" / "1.0.0", root / "fastmcp-loopy" / "1.0.1")
    spec = root / "fastmcp-loopy" / "1.0.1" / "template.yaml"
    spec.write_text(spec.read_text().replace("version: 1.0.0", "version: 1.0.1"), encoding="utf-8")
    assert (
        error_of(lambda: load_template("fastmcp-loopy", "1.0.1", root=root)).code
        is TemplateErrorCode.TEMPLATE_NOT_FOUND  # unreleased: no registry entry yet
    )
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    registry["templates"]["fastmcp-loopy"]["1.0.1"] = "0" * 64
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    rehash(root, version="1.0.1")
    loaded = load_template("fastmcp-loopy", "1.0.1", root=root)
    assert loaded.version == "1.0.1"
    assert loaded.sha256 != load_template("fastmcp-loopy", "1.0.0", root=root).sha256


@pytest.mark.parametrize(
    ("name", "version"),
    [("nope", "1.0.0"), ("fastmcp-loopy", "9.9.9"), ("fastmcp-loopy", "1.0"), ("../x", "1.0.0")],
)
def test_e11_1_t06_unknown_or_malformed_template_refs_are_not_found(
    name: str, version: str
) -> None:
    err = error_of(lambda: load_template(name, version))
    assert err.code is TemplateErrorCode.TEMPLATE_NOT_FOUND


def test_e11_1_t06_symlinks_in_a_template_tree_are_refused(tmp_path: Path) -> None:
    root = copy_templates(tmp_path)
    directory = root / "fastmcp-loopy" / "1.0.0"
    (directory / "link.j2").symlink_to(directory / "server.py.j2")
    err = error_of(lambda: load_template("fastmcp-loopy", "1.0.0", root=root))
    assert err.code is TemplateErrorCode.TEMPLATE_INVALID


def test_e11_1_t06_registry_must_hold_a_sha256(tmp_path: Path) -> None:
    root = copy_templates(tmp_path)
    (root / "registry.json").write_text(
        json.dumps({"templates": {"fastmcp-loopy": {"1.0.0": "not-a-hash"}}}), encoding="utf-8"
    )
    err = error_of(lambda: load_template("fastmcp-loopy", "1.0.0", root=root))
    assert err.code is TemplateErrorCode.TEMPLATE_INVALID


def test_e11_1_templates_dir_can_be_overridden_by_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_templates(tmp_path)
    monkeypatch.setenv("SBAM_TEMPLATES_DIR", str(root))
    assert default_templates_dir() == root
    assert load_template("fastmcp-loopy", "1.0.0").name == "fastmcp-loopy"


# --- ISATVON support -----------------------------------------------------------------------------


def test_e11_1_template_version_without_isatvon_1_4_is_a_template_error(tmp_path: Path) -> None:
    root = copy_templates(tmp_path)
    spec = root / "fastmcp-loopy" / "1.0.0" / "template.yaml"
    spec.write_text(
        spec.read_text().replace('supports_isatvon: ["1.4"]', 'supports_isatvon: ["1.3"]'),
        encoding="utf-8",
    )
    rehash(root)
    old = load_template("fastmcp-loopy", "1.0.0", root=root)
    out = tmp_path / "skeleton"
    err = error_of(lambda: write_skeleton(render_manifest(old, manifest_dict()), out))
    assert err.code is TemplateErrorCode.TEMPLATE_UNSUPPORTED
    assert not out.exists()


def test_e11_1_requested_isatvon_version_must_be_supported(template: LoadedTemplate) -> None:
    err = error_of(lambda: render_skeleton(template, edge_params(), isatvon_version="1.5"))
    assert err.code is TemplateErrorCode.TEMPLATE_UNSUPPORTED


# --- edge cases ----------------------------------------------------------------------------------


def test_e11_1_zero_fresh_state_gives_an_empty_state_model_that_imports_and_reads(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    out = tmp_path / "skeleton"
    write_skeleton(render(template), out)
    state = load_module(out / "state.py")
    assert state.FRESH_STATE == ()
    assert state.QUERIES == {}
    assert state.FreshState.model_fields == {}

    class Conn:
        def execute(self, *_: object) -> None:
            raise AssertionError("no query may run without fresh_state parameters")

    state.configure(Conn())
    assert state.read("key") == state.FreshState()


def test_e11_1_state_with_fresh_state_fails_loudly_at_the_unfilled_slot(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    out = tmp_path / "skeleton"
    write_skeleton(render_manifest(template, manifest_dict()), out)
    with pytest.raises(NotImplementedError, match="LLM_BODY:state_queries"):
        load_module(out / "state.py")


def test_e11_1_state_read_binds_the_key_and_refuses_non_select_queries(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    out = tmp_path / "skeleton"
    write_skeleton(render_manifest(template, manifest_dict()), out)
    source = (out / "state.py").read_text()
    # What the assembler (E11.2) writes into the slot: data, never LLM code.
    filled = source.replace(
        'raise NotImplementedError("LLM_BODY:state_queries")',
        'QUERIES = {"active_devices": "SELECT count(*) FROM d WHERE m = %s"}',
    )
    path = out / "state_filled.py"
    path.write_text(filled)
    state = load_module(path)
    calls: list[tuple[str, tuple[object, ...]]] = []

    class Conn:
        def __init__(self, sql_row: tuple[object, ...] | None) -> None:
            self.row = sql_row

        def execute(self, sql: str, args: tuple[object, ...]) -> "Conn":
            calls.append((sql, args))
            return self

        def fetchone(self) -> tuple[object, ...] | None:
            return self.row

    state.configure(Conn((2,)))
    fresh = state.read("9999")
    assert fresh.active_devices == 2 and fresh.user_status is None
    assert calls == [("SELECT count(*) FROM d WHERE m = %s", ("9999",))]
    state.QUERIES = {"x": "DELETE FROM d WHERE m = %s"}
    with pytest.raises(ValueError, match="SELECT"):
        state.read("9999")
    state.QUERIES = {"x": "SELECT 1 WHERE m = %s OR n = %s"}
    with pytest.raises(ValueError, match="exactly one"):
        state.read("9999")


def test_e11_1_optional_inputs_follow_required_ones_and_enum_becomes_literal(
    template: LoadedTemplate,
) -> None:
    files = render(template).files
    action = ast.parse(files["action_tool.py"])
    function = next(n for n in action.body if isinstance(n, ast.FunctionDef))
    assert [a.arg for a in function.args.args] == [
        "mobile_number",
        "attempt",
        "channel",
        "tags",
        "strict",
    ]
    assert 'channel: Literal["sms", "email", "wh\'at"] | None = None' in files["models.py"]
    assert "attempt: Literal[1, 2, 3]\n" in files["models.py"]
    assert "tags: list[str] | None = None" in files["models.py"]
    assert "from typing import Any, Literal" in files["models.py"]
    assert "from typing import Any, Literal" in files["action_tool.py"]


def test_e11_1_ms01_1_without_enums_imports_only_what_it_uses(template: LoadedTemplate) -> None:
    files = render_manifest(template, manifest_dict()).files
    assert "from typing" not in files["models.py"]
    assert "from typing import Any\n" in files["action_tool.py"]


def test_e11_1_very_large_schema_renders_a_large_models_file_deterministically(
    template: LoadedTemplate,
) -> None:
    params = edge_params()
    params["inputs"] += [
        {"name": f"field_{i:04d}", "type": "string", "required": i % 2 == 0, "enum": ["a", "b"]}
        for i in range(900)
    ]
    first, second = render_skeleton(template, params), render_skeleton(template, params)
    assert first.files == second.files
    models = first.files["models.py"]
    assert models.count("field_") >= 900
    ast.parse(models)
    ast.parse(first.files["server.py"])


def test_e11_1_zero_required_fields_and_zero_gate_outputs_render_valid_python(
    template: LoadedTemplate,
) -> None:
    files = render(template, required_fields=[], gate_outputs=[]).files
    for source in files.values():
        ast.parse(source)
    assert "def append() -> dict[str, Any]:" in files["state_handoff.py"]


def test_e11_1_write_skeleton_writes_exactly_the_six_files_as_lf_utf8(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    out = tmp_path / "nested" / "skeleton"
    written = write_skeleton(render(template, skill_id="MS09.2_Edge_é"), out)
    assert sorted(p.name for p in written) == sorted(PY_FILES)
    assert sorted(p.name for p in out.iterdir()) == sorted(PY_FILES)
    for path in written:
        raw = path.read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n") and not raw.endswith(b"\n\n")
        raw.decode("utf-8")


def test_e11_1_rendered_skeletons_are_black_clean(template: LoadedTemplate) -> None:
    black = pytest.importorskip("black")
    mode = black.Mode(line_length=100, target_versions={black.TargetVersion.PY312})
    skeletons = (
        render_manifest(template, manifest_dict()),
        render(template),
        render(template, outputs=[], required_fields=[], gate_outputs=[]),
    )
    for skeleton in skeletons:
        for name, source in skeleton.files.items():
            assert black.format_str(source, mode=mode) == source, name


def test_e11_1_loaded_template_is_independent_of_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    a = render_manifest(load_template("fastmcp-loopy", "1.0.0"), manifest_dict())
    monkeypatch.chdir(Path(__file__).parent)
    b = render_manifest(load_template("fastmcp-loopy", "1.0.0"), manifest_dict())
    assert a.files == b.files


def iter_names(items: Iterator[Path]) -> list[str]:
    return sorted(p.name for p in items)


def test_e11_1_state_handoff_file_has_no_update_or_delete_and_one_statement(
    template: LoadedTemplate,
) -> None:
    source = render_manifest(template, manifest_dict()).files["state_handoff.py"]
    assert not re.search(r"\b(update|delete|drop|alter|truncate)\b", source, flags=re.IGNORECASE)
    assert source.count(".execute(") == 1
    assert copy.deepcopy(source) == source
    assert inspect  # imported for the signature checks in the T08 tests below


# --- T08 state handoff ---------------------------------------------------------------------------


def test_e11_1_t08_append_takes_every_required_field_and_only_inserts(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    out = tmp_path / "skeleton"
    write_skeleton(render(template), out)  # required_fields = [iteration_pass, evidence]
    handoff = load_module(out / "state_handoff.py")
    signature = inspect.signature(handoff.append)
    assert list(signature.parameters) == ["iteration_pass", "evidence"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in signature.parameters.values())
    executed: list[tuple[str, tuple[object, ...]]] = []

    class Conn:
        def execute(self, sql: str, args: tuple[object, ...]) -> None:
            executed.append((sql, args))

    handoff.configure(Conn())
    row = handoff.append(iteration_pass=True, evidence={"b": 1, "a": [2]})
    assert row == {"iteration_pass": True, "evidence": {"b": 1, "a": [2]}}
    assert len(executed) == 1
    sql, args = executed[0]
    assert sql == "INSERT INTO otp_audit (skill_id, payload) VALUES (%s, %s)"
    assert args == (
        "MS09.2_Edge_Case_Skill",
        json.dumps({"evidence": {"a": [2], "b": 1}, "iteration_pass": True}, separators=None),
    )
    with pytest.raises(TypeError):
        handoff.append(iteration_pass=True)
    with pytest.raises(TypeError):
        handoff.append(True, {})
    with pytest.raises(RuntimeError, match="configure"):
        load_module(out / "state_handoff.py").append(iteration_pass=1, evidence=2)


def test_e11_1_t08_the_only_sql_is_a_parameterised_insert(template: LoadedTemplate) -> None:
    source = render(template).files["state_handoff.py"]
    strings = [
        n.value
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and " " in n.value
    ]
    sql = [s for s in strings if re.search(r"\b(INSERT|SELECT|UPDATE|DELETE)\b", s)]
    assert sql == ["INSERT INTO otp_audit (skill_id, payload) VALUES (%s, %s)"]
    assert not re.search(r"\b(UPDATE|DELETE)\b", source, flags=re.IGNORECASE)
    assert 'f"INSERT' not in source and "format(" not in source and "% (" not in source
