"""E11.1 contract tests: rendered models and skeleton behave as the manifest schemas say.

E11.1-T07: input_schema with enum + required -> Pydantic model with Literal for the enum and
non-Optional required fields, clean under `mypy --strict`. The rest checks that the six rendered
files work together as a package (imports, signatures, unfilled slots failing loudly).
"""

import asyncio
import importlib
import json
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from mypy import api as mypy_api
from pydantic import ValidationError
from sbam_contracts.manifest import SkillManifest
from sbam_workers.build.codegen import (
    RenderedSkeleton,
    load_template,
    params_from_manifest,
    render_skeleton,
    write_skeleton,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "isa14"


def params() -> dict[str, Any]:
    data = json.loads((GOLDEN / "edge_enum_optional" / "params.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def render(**changes: Any) -> RenderedSkeleton:
    merged = params()
    merged.update(changes)
    return render_skeleton(load_template("fastmcp-loopy", "1.0.0"), merged)


def mypy_strict(directory: Path, *files: str) -> tuple[str, int]:
    out, err, status = mypy_api.run(
        ["--strict", "--cache-dir", os.devnull, "--no-error-summary", "--ignore-missing-imports"]
        + [str(directory / f) for f in files]
    )
    return out + err, status


@pytest.fixture
def package(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """The rendered skeleton written as an importable package `skel_<id>`."""
    name = f"skel_{uuid.uuid4().hex}"
    directory = tmp_path / name
    write_skeleton(render(), directory)
    (directory / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        yield name, directory
    finally:
        sys.path.remove(str(tmp_path))
        for module in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
            del sys.modules[module]


def test_e11_1_t07_enum_becomes_literal_and_required_fields_are_not_optional(
    tmp_path: Path,
) -> None:
    write_skeleton(render(), tmp_path)
    source = (tmp_path / "models.py").read_text(encoding="utf-8")
    assert 'channel: Literal["sms", "email", "wh\'at"] | None = None' in source
    assert "attempt: Literal[1, 2, 3]\n" in source
    assert "mobile_number: str\n" in source
    out, status = mypy_strict(tmp_path, "models.py")
    assert (out, status) == ("", 0)


def test_e11_1_t07_models_module_enforces_enum_required_and_extra_forbidden(
    package: tuple[str, Path],
) -> None:
    name, _ = package
    models: ModuleType = importlib.import_module(f"{name}.models")
    ok = models.SkillInput(mobile_number="9999999999", attempt=2, channel="sms")
    assert ok.channel == "sms" and ok.tags is None and ok.strict is None
    fields = models.SkillInput.model_fields
    assert fields["mobile_number"].is_required() and fields["attempt"].is_required()
    assert not fields["channel"].is_required()
    with pytest.raises(ValidationError):
        models.SkillInput(mobile_number="1", attempt=2, channel="fax")  # not in the enum
    with pytest.raises(ValidationError):
        models.SkillInput(mobile_number="1", attempt=4)  # not in the integer enum
    with pytest.raises(ValidationError):
        models.SkillInput(attempt=1)  # required field missing
    with pytest.raises(ValidationError):
        models.SkillInput(mobile_number="1", attempt=1, surprise=True)  # unknown key
    with pytest.raises(ValidationError):
        ok.attempt = 3  # frozen
    assert models.SkillOutput(verified=True, scores=[1, "x"]).details is None


def test_e11_1_t07_every_scalar_array_and_object_type_is_mypy_strict_clean(
    tmp_path: Path,
) -> None:
    fields = [
        {"name": "s", "type": "string", "required": True},
        {"name": "i", "type": "integer", "required": True},
        {"name": "n", "type": "number", "required": False},
        {"name": "b", "type": "boolean", "required": False},
        {"name": "o", "type": "object", "required": False},
        {"name": "a", "type": "array", "required": False},
        {"name": "ai", "type": "array", "items": "integer", "required": True},
        {"name": "e", "type": "string", "enum": ["x", "y"], "required": True},
    ]
    write_skeleton(render(inputs=fields, outputs=fields), tmp_path)
    out, status = mypy_strict(tmp_path, "models.py")
    assert (out, status) == ("", 0)


def test_e11_1_t07_ms01_1_models_are_mypy_strict_clean(tmp_path: Path) -> None:
    manifest = SkillManifest.model_validate(
        json.loads((FIXTURES / "ms01_1_manifest.json").read_text(encoding="utf-8"))
    )
    template = load_template("fastmcp-loopy", "1.0.0")
    skeleton = render_skeleton(template, params_from_manifest(manifest))
    write_skeleton(skeleton, tmp_path)
    out, status = mypy_strict(tmp_path, "models.py", "state_handoff.py")
    assert (out, status) == ("", 0)


def test_e11_1_rendered_tool_files_are_mypy_strict_clean_as_a_package(
    package: tuple[str, Path],
) -> None:
    _, directory = package
    out, status = mypy_strict(
        directory, "models.py", "state.py", "state_handoff.py", "action_tool.py", "check_tool.py"
    )
    assert (out, status) == ("", 0)


def test_e11_1_unfilled_tool_slots_raise_at_the_slot_after_reading_fresh_state(
    package: tuple[str, Path],
) -> None:
    name, _ = package
    state = importlib.import_module(f"{name}.state")
    action = importlib.import_module(f"{name}.action_tool")
    check = importlib.import_module(f"{name}.check_tool")
    state.configure(object())  # zero fresh_state parameters: no query ever touches it
    with pytest.raises(NotImplementedError, match="LLM_BODY:action"):
        action.verify_otp(mobile_number="1", attempt=1)
    with pytest.raises(NotImplementedError, match="LLM_BODY:check"):
        check.check_limits(mobile_number="1", attempt=1)
    assert action.TOOL_NAME == "verify_otp" and check.TOOL_NAME == "check_limits"
    assert check.GATE_OUTPUTS == ("verified",)


def test_e11_1_check_tool_is_decoupled_from_the_action_tool(package: tuple[str, Path]) -> None:
    name, _ = package
    importlib.import_module(f"{name}.check_tool")
    assert f"{name}.action_tool" not in sys.modules  # importing the check tool never loads it


def test_e11_1_server_registers_exactly_the_two_tools_when_fastmcp_is_installed(
    package: tuple[str, Path],
) -> None:
    """Skipped where fastmcp is absent: it is a skill-runtime dependency, not a worker one."""
    fastmcp = pytest.importorskip("fastmcp")
    name, _ = package
    server = importlib.import_module(f"{name}.server")

    async def tool_names() -> list[str]:
        async with fastmcp.Client(server.mcp) as client:
            return sorted(tool.name for tool in await client.list_tools())

    assert server.SKILL_ID == "MS09.2_Edge_Case_Skill"
    assert asyncio.run(tool_names()) == ["check_limits", "verify_otp"]
