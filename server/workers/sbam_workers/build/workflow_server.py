"""The Workflow's MCP server (WG-10, #758; ADR 0028 decision 3).

One FastMCP server per Workflow holds every Sub-Skill's Action tool and independent Fixed
Check tool, namespaced `MSnn_m__action` and `MSnn_m__check` (no dot: model clients restrict
tool names, and Bifrost adds `<server>-` in front). This module renders that server and the
files around it from the Skill Manifests and the Sub-Skills' generated bundles:

  server.py      the aggregate server (template-owned: no model writes it, and it calls no model)
  tools.json     every tool with its input and output schema (the `McpToolEntry` list)
  schema.sql     the Workflow's schema
  loop_specs/    one declarative loop spec per Sub-Skill (the bounds stay with the Sub-Skill)
  skills/<pkg>/  each Sub-Skill's own generated files (its per-skill server.py is not shipped)
  vendor/        vendored repository code, untouched (#757)
  Dockerfile     one image, pinned by digest: dependencies and vendored code in the base layer,
                 the generated skills and the server on top, so a changed skill rebuilds one layer

The order of the steps is not here: it stays in the manifest DAG (`next` and data edges).
`execution_order` reads it for a runner and for the tests. Pure and deterministic: the same
inputs give the same bytes.
"""

import ast
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import black
from sbam_contracts.canonical import canonical_json
from sbam_contracts.manifest import (
    MCP_SERVER_NAME,
    McpServerEntry,
    McpToolEntry,
    SkillManifest,
    WorkflowManifest,
)

from sbam_workers.build.codegen.bundle import BLACK_MODE, LOOP_SPEC_FILE
from sbam_workers.build.codegen.params import params_from_manifest
from sbam_workers.build.codegen.render import _annotation, docstring, python_literal

SERVER_FILE: Final = "server.py"
TOOLS_FILE: Final = "tools.json"
SCHEMA_FILE: Final = "schema.sql"
LOCK_FILE: Final = "requirements.lock"
MANIFEST_FILE: Final = "manifest.json"
DOCKERFILE: Final = "Dockerfile"
PORT: Final = 8000
# Files a Sub-Skill's bundle must hold for the aggregate server to import and call it.
REQUIRED_SKILL_FILES: Final = ("models.py", "action_tool.py", "check_tool.py", LOOP_SPEC_FILE)
_SKILL_ID: Final = re.compile(r"^MS(\d{2})\.(\d+)_")
_BASE_IMAGE: Final = re.compile(r"^[a-z0-9][a-zA-Z0-9_./:-]*@sha256:[0-9a-f]{64}$")
_NON_ALNUM: Final = re.compile(r"[^a-z0-9]+")
_SAFE_PATH: Final = re.compile(r"^[A-Za-z0-9._@+-]+(/[A-Za-z0-9._@+-]+)*$")


class WorkflowServerError(ValueError):
    """The server cannot be built; `code` is stable so a stage can map it to a finding."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class SkillPart:
    """One Sub-Skill: its validated Skill Manifest and the files of its assembled bundle."""

    manifest: SkillManifest
    files: Mapping[str, bytes]

    @property
    def skill_id(self) -> str:
        return str(self.manifest.dump()["skill_metadata"]["skill_id"])


@dataclass(frozen=True)
class WorkflowServerInput:
    workflow_name: str
    skills: Sequence[SkillPart]
    schema_sql: str
    base_image: str  # the skill runtime, pinned by digest: `name@sha256:<64 hex>`
    requirements_lock: str = ""  # hashed lock of the libraries beyond the runtime's own
    vendor: Mapping[str, bytes] | None = None  # `vendor/<repo>@<commit>/...` (#757), untouched
    manifest_json: bytes | None = None  # the Workflow manifest draft (it has no digest yet)


@dataclass(frozen=True)
class WorkflowServerBundle:
    server_name: str
    files: Mapping[str, bytes]  # path -> bytes, sorted by path; the Docker build context
    tools: tuple[McpToolEntry, ...]
    base_layer_key: str  # dependencies + vendored code (+ the base image)
    top_layer_key: str  # generated skills, server, tools, schema
    bundle_sha256: str

    def entry(self, *, version: str, image_digest: str) -> McpServerEntry:
        """The manifest entry once the image is built and its digest is known."""
        return McpServerEntry(
            name=self.server_name,
            version=version,
            image_digest=image_digest,
            tools=list(self.tools),
        )


# --- names ---------------------------------------------------------------------------------------


def _parts_of(skill_id: str) -> tuple[str, str]:
    match = _SKILL_ID.match(skill_id)
    if match is None:
        raise WorkflowServerError("SKILL_ID_INVALID", f"{skill_id!r} is not MSnn.m_<Name>")
    return match.group(1), match.group(2)


def package_name(skill_id: str) -> str:
    """`MS01.1_Login` -> `MS01_1`: the Python package of the Sub-Skill's files."""
    nn, m = _parts_of(skill_id)
    return f"MS{nn}_{m}"


def tool_names(skill_id: str) -> tuple[str, str]:
    """(action, check) tool names of a Sub-Skill: `MS01_1__action`, `MS01_1__check`."""
    pkg = package_name(skill_id)
    return f"{pkg}__action", f"{pkg}__check"


def server_name(workflow_name: str) -> str:
    """`wf_<slug>`: letters, digits and underscores (Bifrost forbids hyphens and spaces)."""
    slug = _NON_ALNUM.sub("_", workflow_name.lower()).strip("_")
    name = f"wf_{slug}"[:64].rstrip("_")
    if not slug or MCP_SERVER_NAME.match(name) is None:
        raise WorkflowServerError("SERVER_NAME_INVALID", f"no server name from {workflow_name!r}")
    return name


# --- checks --------------------------------------------------------------------------------------


def imports_action_tool(source: str) -> bool:
    """True when a module imports `action_tool` in any form."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[-1] == "action_tool" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "action_tool":
                return True
            if any(alias.name == "action_tool" for alias in node.names):
                return True
    return False


def _check_part(part: SkillPart) -> None:
    missing = [name for name in REQUIRED_SKILL_FILES if name not in part.files]
    if missing:
        raise WorkflowServerError(
            "SKILL_FILES_MISSING", f"{part.skill_id}: no {', '.join(missing)}"
        )
    try:
        reaches = imports_action_tool(part.files["check_tool.py"].decode())
    except SyntaxError as exc:
        raise WorkflowServerError("SKILL_SYNTAX", f"{part.skill_id}: check_tool.py: {exc}") from exc
    if reaches:
        raise WorkflowServerError(
            "CHECK_IMPORTS_ACTION",
            f"{part.skill_id}: check_tool.py imports action_tool (the check tool is independent)",
        )


def _safe_path(path: str, where: str) -> str:
    if _SAFE_PATH.match(path) is None or ".." in path.split("/"):
        raise WorkflowServerError("PATH_INVALID", f"{where}: {path!r}")
    return path


# --- rendering -----------------------------------------------------------------------------------


def _signature(params: Mapping[str, Any], names: set[str]) -> list[dict[str, Any]]:
    """The flat tool parameters of a Sub-Skill: required ones first, like the per-skill template."""
    fields = [
        {"name": f["name"], "annotation": _annotation(f, names), "required": f["required"]}
        for f in params["inputs"]
    ]
    return [f for f in fields if f["required"]] + [f for f in fields if not f["required"]]


def _tool_source(
    pkg: str, name: str, call: str, doc: str, signature: Sequence[Mapping[str, Any]]
) -> str:
    args = ", ".join(
        f"{f['name']}: {f['annotation']}" + ("" if f["required"] else " | None = None")
        for f in signature
    )
    kwargs = ", ".join(f"{f['name']}={f['name']}" for f in signature)
    return (
        f"@mcp.tool(name={python_literal(name)})\n"
        f"def {name}({args}) -> dict[str, Any]:\n"
        f'    """{docstring(doc)}"""\n'
        f"    args = {pkg}_models.SkillInput({kwargs})\n"
        f"    return {call}(**args.model_dump())\n"
    )


def render_server(parts: Sequence[SkillPart], workflow_name: str) -> str:
    """The aggregate `server.py`, Black-formatted: no loop and no model call."""
    typing_names: set[str] = {"Any"}
    imports: list[str] = []
    sources: list[str] = []
    for part in sorted(parts, key=lambda p: p.skill_id):
        pkg = package_name(part.skill_id)
        params = params_from_manifest(part.manifest)
        signature = _signature(params, typing_names)
        action, check = tool_names(part.skill_id)
        imports += [
            f"from skills.{pkg} import action_tool as {pkg}_action_tool",
            f"from skills.{pkg} import check_tool as {pkg}_check_tool",
            f"from skills.{pkg} import models as {pkg}_models",
        ]
        sources.append(
            _tool_source(
                pkg,
                action,
                f"{pkg}_action_tool.{params['action_tool']}",
                f"Action tool of {part.skill_id}: {params['action_tool']}.",
                signature,
            )
        )
        sources.append(
            _tool_source(
                pkg,
                check,
                f"{pkg}_check_tool.{params['fixed_check_tool']}",
                f"Fixed check tool of {part.skill_id}: {params['fixed_check_tool']}.",
                signature,
            )
        )
    header = (
        f'"""Workflow MCP server {docstring(workflow_name)}.\n\n'
        "Generated by SBAM (ADR 0028). One FastMCP server holds every Sub-Skill's action tool\n"
        "and its independent fixed check tool, namespaced MSnn_m__action / MSnn_m__check.\n"
        "Template-owned: the\n"
        'LLM writes nothing here, and the server calls no model at run time."""\n\n'
        "from __future__ import annotations\n\n"
        f"from typing import {', '.join(sorted(typing_names))}\n\n"
        "from fastmcp import FastMCP\n\n" + "\n".join(imports) + "\n\n"
        f"SERVER_NAME = {python_literal(server_name(workflow_name))}\n\n"
        "mcp = FastMCP(SERVER_NAME)\n\n\n"
    )
    footer = (
        '\nif __name__ == "__main__":\n'
        f'    mcp.run(transport="http", host="0.0.0.0", port={PORT})\n'
    )
    return black.format_str(header + "\n\n".join(sources) + "\n" + footer, mode=BLACK_MODE)


def _tool_entries(parts: Sequence[SkillPart]) -> tuple[McpToolEntry, ...]:
    tools: list[McpToolEntry] = []
    for part in sorted(parts, key=lambda p: p.skill_id):
        contracts = part.manifest.dump()["contracts"]
        for name, role in zip(tool_names(part.skill_id), ("action", "check"), strict=True):
            tools.append(
                McpToolEntry(
                    name=name,
                    skill_id=part.skill_id,
                    role=role,  # type: ignore[arg-type]
                    input_schema=contracts["input_schema"],
                    output_schema=contracts["output_schema"],
                )
            )
    return tuple(tools)


def render_dockerfile(base_image: str, *, has_lock: bool, has_vendor: bool) -> str:
    """One image: the base layer (dependencies, vendored code), then the generated skills on top."""
    if _BASE_IMAGE.match(base_image) is None:
        raise WorkflowServerError(
            "BASE_IMAGE_NOT_PINNED", f"{base_image!r} is not name@sha256:<hex>"
        )
    lines = [
        "# syntax=docker/dockerfile:1.7",
        "# Generated by SBAM (ADR 0028): the Workflow's MCP server, one image pinned by digest.",
        f"FROM {base_image}",
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app",
        "WORKDIR /app",
        "# Base layer: dependencies and vendored repository code. Rebuilt only when these change.",
    ]
    if has_lock:
        lines += [
            f"COPY {LOCK_FILE} ./",
            f"RUN pip install --no-cache-dir --no-deps --require-hashes -r {LOCK_FILE}",
        ]
    if has_vendor:
        lines.append("COPY vendor/ ./vendor/")
    lines += [
        "# Top layer: the generated skills and the server. A changed skill rebuilds only this.",
        "COPY skills/ ./skills/",
        "COPY loop_specs/ ./loop_specs/",
        f"COPY {SCHEMA_FILE} {TOOLS_FILE} {SERVER_FILE} ./",
    ]
    lines += [
        "USER 10001:10001",
        f"EXPOSE {PORT}",
        f'CMD ["python", "{SERVER_FILE}"]',
    ]
    return "\n".join(lines) + "\n"


# --- the bundle ----------------------------------------------------------------------------------


def _digests(files: Mapping[str, bytes]) -> dict[str, str]:
    return {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())}


def _layer_key(files: Mapping[str, bytes], *extra: str) -> str:
    return hashlib.sha256(
        canonical_json({"files": _digests(files), "extra": list(extra)})
    ).hexdigest()


def build_bundle(inp: WorkflowServerInput) -> WorkflowServerBundle:
    """Render the server and its Docker context; `WorkflowServerError` if unsound."""
    if not inp.skills:
        raise WorkflowServerError("NO_SKILLS", "a Workflow server needs at least one Sub-Skill")
    ids = [p.skill_id for p in inp.skills]
    if len(set(ids)) != len(ids):
        raise WorkflowServerError("SKILL_DUPLICATE", "two Sub-Skills share a skill_id")
    packages = [package_name(i) for i in ids]
    if len(set(packages)) != len(packages):
        raise WorkflowServerError("PACKAGE_CLASH", "two Sub-Skills give the same package name")
    name = server_name(inp.workflow_name)
    for part in inp.skills:
        _check_part(part)

    base: dict[str, bytes] = {}
    if inp.requirements_lock.strip():
        base[LOCK_FILE] = inp.requirements_lock.encode()
    for path, data in (inp.vendor or {}).items():
        if not _safe_path(path, "vendor").startswith("vendor/"):
            raise WorkflowServerError("PATH_INVALID", f"vendor file outside vendor/: {path!r}")
        base[path] = data

    top: dict[str, bytes] = {"skills/__init__.py": b""}
    for part in sorted(inp.skills, key=lambda p: p.skill_id):
        pkg = package_name(part.skill_id)
        top[f"skills/{pkg}/__init__.py"] = b""
        for fname, data in part.files.items():
            if fname == "server.py":
                continue  # one server per Workflow: the Sub-Skill's own is not shipped
            top[f"skills/{pkg}/{_safe_path(fname, part.skill_id)}"] = data
        top[f"loop_specs/{pkg}.json"] = part.files[LOOP_SPEC_FILE]
    tools = _tool_entries(inp.skills)
    top[SERVER_FILE] = render_server(inp.skills, inp.workflow_name).encode()
    top[TOOLS_FILE] = canonical_json([t.dump() for t in tools])
    top[SCHEMA_FILE] = inp.schema_sql.encode()
    if inp.manifest_json is not None:
        top[MANIFEST_FILE] = inp.manifest_json

    dockerfile = render_dockerfile(
        inp.base_image,
        has_lock=LOCK_FILE in base,
        has_vendor=any(p.startswith("vendor/") for p in base),
    )
    files = {**base, **top, DOCKERFILE: dockerfile.encode()}
    ordered = dict(sorted(files.items()))
    return WorkflowServerBundle(
        server_name=name,
        files=ordered,
        tools=tools,
        base_layer_key=_layer_key(base, inp.base_image),
        top_layer_key=_layer_key(top),
        bundle_sha256=hashlib.sha256(canonical_json(_digests(ordered))).hexdigest(),
    )


def with_server(manifest: WorkflowManifest, entry: McpServerEntry) -> WorkflowManifest:
    """The Workflow manifest with the built server (name, version, tools, image digest) recorded.

    The server entry is covered by the content hash, so the manifest is sealed again."""
    return WorkflowManifest.sealed({**manifest.dump(), "mcp_server": entry.dump()})


# --- order ---------------------------------------------------------------------------------------


def execution_order(edges: Iterable[Mapping[str, Any]]) -> list[str]:
    """Sub-Skills in the order the manifest DAG asks for (`data` edges, then `next`), ties by id.

    The server does not enforce it: a runner walks this order and calls the tools.
    """
    after: dict[str, set[str]] = {}
    skills: set[str] = set()
    for edge in edges:
        if edge["kind"] == "contains":
            skills.add(str(edge["to"]))
    for edge in edges:
        if edge["kind"] in ("data", "next") and {edge["from"], edge["to"]} <= skills:
            after.setdefault(str(edge["to"]), set()).add(str(edge["from"]))
    order: list[str] = []
    remaining = set(skills)
    while remaining:
        ready = sorted(s for s in remaining if not (after.get(s, set()) & remaining))
        if not ready:
            raise WorkflowServerError("DAG_CYCLE", f"no order for {sorted(remaining)}")
        order.append(ready[0])
        remaining.discard(ready[0])
    return order
