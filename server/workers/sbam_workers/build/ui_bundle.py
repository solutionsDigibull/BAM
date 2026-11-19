"""The Workflow's UI bundle (WG-20, #768; ADR 0033, ADR 0032).

BAM always delivers a Workflow's UI as a bundle and SRIM shows only that. The bundle is
rendered from a released, hashed template (`server/templates/workflow-ui/<version>`) and the
Workflow's own Sub-Skills: one skill per macro category, one step screen per Sub-Skill, an input
form built from the input schema of its tools, result cards built from the output schema, a human
review checkpoint at the end of every skill, and a data provider that calls the Workflow's own MCP
server (`tools/call`), never mock data.

What is code and what is the model's: the screens are template files copied byte for byte. The
only files written per Workflow are `src/workflow.generated.ts` (the Workflow described as data),
`package.json`, `bundle.json` and `index.html`. The model may fill `UiCopy`: plain text for
summaries, field hints and the hand-over note. It never writes code, and its text only ever lands
inside a JSON string.

Pure and deterministic: the same inputs give the same bytes and the same digest. The drift gate
(`gates/ui_gate.py`) checks a bundle against the same template.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import jinja2
import yaml
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field
from sbam_contracts.canonical import canonical_json
from sbam_contracts.manifest import SkillManifest, TemplateRef, UiBundleEntry

from sbam_workers.build.codegen.errors import TemplateError, TemplateErrorCode
from sbam_workers.build.codegen.store import (
    default_templates_dir,
    read_tree,
    released_hash,
    tree_sha256,
)
from sbam_workers.build.workflow_server import package_name, tool_names

TEMPLATE_NAME: Final = "workflow-ui"
TEMPLATE_VERSION: Final = "1.0.0"
BUNDLE_VERSION: Final = "1.0.0"  # MAJOR.0.PATCH of the bundle entry in the manifest
SPEC_FILE: Final = "template.yaml"
INDEX_TEMPLATE: Final = "index.html.j2"
STATIC_ROOT: Final = "static/"
GENERATED_FILE: Final = "src/workflow.generated.ts"
PACKAGE_FILE: Final = "package.json"
BUNDLE_FILE: Final = "bundle.json"
ICON_CYCLE: Final = ("inbox", "scan", "clipboard", "database", "table", "chart", "link", "gauge")
_ICON_WORDS: Final = (
    ("mail", "mail"),
    ("email", "mail"),
    ("file", "files"),
    ("document", "files"),
    ("supplier", "truck"),
    ("source", "truck"),
    ("report", "chart"),
    ("cost", "chart"),
    ("data", "database"),
    ("schema", "database"),
)
_CATEGORY: Final = re.compile(r"^MS(\d{2})_(.+)$")
_SKILL_ID: Final = re.compile(r"^MS(\d{2})\.(\d+)_")
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TEXT_TYPES: Final = ("text/html", "text/markdown", "text/plain")
_FIELD_TYPES: Final = ("string", "integer", "number", "boolean", "array", "object")
MAX_TEXT: Final = 300


class UiBundleError(ValueError):
    """The bundle cannot be rendered; `code` is stable so a stage can record it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# --- the model's part ----------------------------------------------------------------------------


class SubCopy(BaseModel):
    """Text for one Sub-Skill's screen, keyed by its `skill_id` in `UiCopy`."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=MAX_TEXT)
    field_help: dict[str, str] = Field(default_factory=dict)


class UiCopy(BaseModel):
    """Plain text the model may fill. Everything is optional: a missing entry falls back to the
    Sub-Skill's own text in the manifest."""

    model_config = ConfigDict(extra="forbid")

    sub_skills: dict[str, SubCopy] = Field(default_factory=dict)
    review_notes: dict[str, str] = Field(default_factory=dict)  # by macro category


# --- the template --------------------------------------------------------------------------------


@dataclass(frozen=True)
class UiTemplate:
    name: str
    version: str
    sha256: str
    spec: Mapping[str, Any]
    static: Mapping[str, bytes]  # bundle path -> bytes, copied verbatim
    index_source: str

    @property
    def ref(self) -> TemplateRef:
        return TemplateRef(name=self.name, version=self.version, sha256=self.sha256)

    @property
    def dependencies(self) -> Mapping[str, str]:
        return dict(self.spec["dependencies"])

    @property
    def dev_dependencies(self) -> Mapping[str, str]:
        return dict(self.spec["dev_dependencies"])

    @property
    def required_files(self) -> tuple[str, ...]:
        return tuple(self.spec["required_files"])

    @property
    def required_panes(self) -> tuple[str, ...]:
        return tuple(self.spec["required_panes"])

    @property
    def entry_file(self) -> str:
        return str(self.spec["entry_file"])


def load_ui_template(version: str = TEMPLATE_VERSION, *, root: Path | None = None) -> UiTemplate:
    """Load `<root>/workflow-ui/<version>`, verify its tree hash against `registry.json`."""
    base = root if root is not None else default_templates_dir()
    directory = base / TEMPLATE_NAME / version
    if not directory.is_dir():
        raise TemplateError(
            TemplateErrorCode.TEMPLATE_NOT_FOUND, f"no template {TEMPLATE_NAME} {version}"
        )
    tree = read_tree(directory)
    expected, actual = released_hash(base, TEMPLATE_NAME, version), tree_sha256(tree)
    if actual != expected:
        raise TemplateError(
            TemplateErrorCode.TEMPLATE_HASH_MISMATCH,
            f"{TEMPLATE_NAME} {version} changed since release (expected {expected}, found "
            f"{actual}); released templates are immutable, bump the version instead",
        )
    try:
        spec = yaml.safe_load(tree[SPEC_FILE].decode("utf-8"))
        if not isinstance(spec, dict) or spec.get("version") != version:
            raise TypeError(SPEC_FILE)
        for key in ("dependencies", "dev_dependencies", "required_files", "required_panes"):
            if key not in spec:
                raise KeyError(key)
        index_source = tree[INDEX_TEMPLATE].decode("utf-8")
    except (KeyError, TypeError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise TemplateError(
            TemplateErrorCode.TEMPLATE_INVALID, f"{SPEC_FILE} is missing or incomplete"
        ) from exc
    static = {p.removeprefix(STATIC_ROOT): b for p, b in tree.items() if p.startswith(STATIC_ROOT)}
    return UiTemplate(TEMPLATE_NAME, version, actual, spec, static, index_source)


# --- the Workflow as data ------------------------------------------------------------------------


def clean(text: object, limit: int = MAX_TEXT) -> str:
    """Text that is safe to show: NFC, no control characters, one line of at most `limit`."""
    value = unicodedata.normalize("NFC", _CONTROL.sub(" ", str(text)))
    return " ".join(value.split())[:limit]


def label_of(name: str) -> str:
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


def _widget(spec: Mapping[str, Any], kind: str) -> str:
    if spec.get("enum"):
        return "select"
    if kind == "boolean":
        return "checkbox"
    if kind in ("integer", "number"):
        return "number"
    if kind in ("array", "object"):
        return "json"
    if spec.get("format") == "binary" or "contentMediaType" in spec:
        return "file"
    if spec.get("format") == "textarea" or int(spec.get("maxLength") or 0) > 200:
        return "textarea"
    return "text"


def _kind(spec: Mapping[str, Any]) -> str:
    kind = spec.get("type")
    return kind if kind in _FIELD_TYPES else "object"


def input_fields(schema: Mapping[str, Any], help_text: Mapping[str, str]) -> list[dict[str, Any]]:
    """The form of a Sub-Skill, from its tool input schema: required fields first, then by name."""
    properties = schema.get("properties") or {}
    required = [n for n in schema.get("required") or [] if n in properties]
    names = list(dict.fromkeys(required)) + sorted(n for n in properties if n not in required)
    fields: list[dict[str, Any]] = []
    for name in names:
        spec = properties[name] if isinstance(properties[name], Mapping) else {}
        kind = _kind(spec)
        field: dict[str, Any] = {
            "name": name,
            "label": label_of(name),
            "type": kind,
            "required": name in required,
            "widget": _widget(spec, kind),
        }
        if spec.get("enum"):
            field["enum"] = [v for v in spec["enum"] if isinstance(v, str | int)][:100]
        hint = help_text.get(name) or spec.get("description")
        if hint:
            field["description"] = clean(hint, 200)
        fields.append(field)
    return fields


def output_fields(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The result cards of a Sub-Skill, from its tool output schema (table, editable text, or
    key and value)."""
    properties = schema.get("properties") or {}
    fields: list[dict[str, Any]] = []
    for name in sorted(properties):
        spec = properties[name] if isinstance(properties[name], Mapping) else {}
        kind = _kind(spec)
        items = spec.get("items")
        if kind == "array" and isinstance(items, Mapping) and items.get("type") in (None, "object"):
            view = "grid"
        elif kind == "string" and spec.get("contentMediaType") in _TEXT_TYPES:
            view = "editor"
        else:
            view = "kv"
        field: dict[str, Any] = {
            "name": name,
            "label": label_of(name),
            "type": kind,
            "view": view,
        }
        if spec.get("description"):
            field["description"] = clean(spec["description"], 200)
        fields.append(field)
    return fields


def _icon(name: str, index: int) -> str:
    lowered = name.lower()
    for word, icon in _ICON_WORDS:
        if word in lowered:
            return icon
    return ICON_CYCLE[index % len(ICON_CYCLE)]


def _split(category: str) -> tuple[int, str]:
    match = _CATEGORY.match(category)
    if match is None:
        raise UiBundleError("CATEGORY_INVALID", f"{category!r} is not MSnn_<Name>")
    return int(match.group(1)), match.group(2).replace("_", " ")


def _sub(manifest: SkillManifest, copy: UiCopy) -> tuple[str, str, dict[str, Any]]:
    """(macro category, next skill id or '', the Sub-Skill's screen as data)."""
    data = manifest.dump()
    meta = data["skill_metadata"]
    skill_id = str(meta["skill_id"])
    if _SKILL_ID.match(skill_id) is None:
        raise UiBundleError("SKILL_ID_INVALID", f"{skill_id!r} is not MSnn.m_<Name>")
    definition = data["isatvon_definition"]
    automation, stack = definition["a_automation"], definition["t_tech_stack"]
    contracts = data["contracts"]
    text = copy.sub_skills.get(skill_id)
    summary = (
        clean(text.summary)
        if text and text.summary.strip()
        else clean(definition["i_instructions"])
    )
    inputs = input_fields(contracts["input_schema"], text.field_help if text else {})
    outputs = output_fields(contracts["output_schema"])
    action, check = tool_names(skill_id)
    steps: list[dict[str, str]] = []
    if inputs:
        steps.append(
            {
                "name": "Provide the input",
                "note": f"{len(inputs)} field(s) from the input schema",
                "mode": "human",
            }
        )
    steps.append(
        {
            "name": clean(automation["action_step"], 160),
            "note": clean(stack["action_tool"], 120),
            "mode": "auto",
        }
    )
    steps.append(
        {
            "name": clean(automation["acceptance_check"], 160),
            "note": clean(stack["fixed_check_tool"], 120),
            "mode": "auto",
        }
    )
    screen = {
        "id": skill_id,
        "name": clean(meta["skill_name"], 120),
        "summary": summary,
        "actionTool": action,
        "checkTool": check,
        "steps": steps,
        "inputs": inputs,
        "outputs": outputs,
    }
    return str(meta["macro_category"]), str(definition["n_notification"].get("next") or ""), screen


def workflow_spec(
    workflow_name: str,
    server_name: str,
    version: str,
    skills: Sequence[SkillManifest],
    copy: UiCopy,
) -> dict[str, Any]:
    """The Workflow described as data: skills (macro categories) in order, each with its screens."""
    grouped: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for manifest in sorted(skills, key=lambda m: str(m.dump()["skill_metadata"]["skill_id"])):
        category, nxt, screen = _sub(manifest, copy)
        grouped.setdefault(category, []).append((screen["id"], nxt, screen))
    names = {c: _split(c)[1] for c in grouped}
    out: list[dict[str, Any]] = []
    for index, category in enumerate(sorted(grouped, key=lambda c: (_split(c)[0], c))):
        subs = grouped[category]
        following = subs[-1][1]
        target = next(
            (names[c] for c, items in grouped.items() if any(i[0] == following for i in items)),
            None,
        )
        note = copy.review_notes.get(category)
        out.append(
            {
                "n": _split(category)[0],
                "id": category,
                "name": names[category],
                "icon": _icon(names[category], index),
                "hook": (
                    f"Hook passes the result to {target}"
                    if target
                    else "Last skill of the workflow"
                ),
                "subs": [s[2] for s in subs],
                "review": {
                    "name": "Human review",
                    "note": (
                        clean(note)
                        if note and note.strip()
                        else "Check the results of this skill. Nothing moves on until you approve."
                    ),
                },
            }
        )
    return {
        "name": clean(workflow_name, 120),
        "serverName": server_name,
        "version": version,
        "skills": out,
    }


# --- the bundle ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class UiBundleInput:
    workflow_name: str
    server_name: str  # `mcp_server.name`, the server the UI talks to
    skills: Sequence[SkillManifest]
    copy: UiCopy | None = None
    allowed_hosts: tuple[str, ...] = ()  # none: the UI talks to its own server only
    version: str = BUNDLE_VERSION


@dataclass(frozen=True)
class UiBundle:
    files: Mapping[str, bytes]  # path -> bytes, sorted by path
    digest: str  # `sha256:` + the hash of the file map
    template: TemplateRef
    entry: UiBundleEntry

    @property
    def spec(self) -> dict[str, Any]:
        text = self.files[GENERATED_FILE].decode().split("WORKFLOW: WorkflowSpec = ", 1)[1]
        spec: dict[str, Any] = json.loads(text.rstrip().removesuffix(";"))
        return spec


def file_digests(files: Mapping[str, bytes]) -> dict[str, str]:
    return {p: hashlib.sha256(d).hexdigest() for p, d in sorted(files.items())}


def bundle_digest(files: Mapping[str, bytes]) -> str:
    """The digest the manifest records: the hash of every file's path and hash, `sha256:` first."""
    return "sha256:" + hashlib.sha256(canonical_json(file_digests(files))).hexdigest()


def render_generated(spec: Mapping[str, Any]) -> bytes:
    body = json.dumps(spec, indent=2, ensure_ascii=True)
    return (
        "// Generated by SBAM from the Workflow's skills and tool schemas. Do not edit.\n"
        'import type { WorkflowSpec } from "./types";\n\n'
        f"export const WORKFLOW: WorkflowSpec = {body};\n"
    ).encode()


def render_package(template: UiTemplate, server_name: str, version: str) -> bytes:
    package = {
        "name": server_name.replace("_", "-") + "-ui",
        "private": True,
        "version": version,
        "type": "module",
        "scripts": {"dev": "vite", "build": "tsc -b && vite build", "preview": "vite preview"},
        "dependencies": dict(sorted(template.dependencies.items())),
        "devDependencies": dict(sorted(template.dev_dependencies.items())),
    }
    return (json.dumps(package, indent=2) + "\n").encode()


def render_index(template: UiTemplate, title: str) -> bytes:
    env = SandboxedEnvironment(
        autoescape=True, undefined=jinja2.StrictUndefined, keep_trailing_newline=True
    )
    return env.from_string(template.index_source).render(title=clean(title, 120)).encode()


def build_ui_bundle(inp: UiBundleInput, *, template: UiTemplate | None = None) -> UiBundle:
    """Render the bundle; `UiBundleError` when the Workflow cannot be described."""
    if not inp.skills:
        raise UiBundleError("NO_SKILLS", "a Workflow UI needs at least one Sub-Skill")
    ids = [str(s.dump()["skill_metadata"]["skill_id"]) for s in inp.skills]
    if len(set(ids)) != len(ids):
        raise UiBundleError("SKILL_DUPLICATE", "two Sub-Skills share a skill_id")
    for skill_id in ids:
        package_name(skill_id)  # the id must be one the server can name
    tpl = template if template is not None else load_ui_template()
    spec = workflow_spec(
        inp.workflow_name, inp.server_name, inp.version, inp.skills, inp.copy or UiCopy()
    )
    files: dict[str, bytes] = dict(tpl.static)
    files[tpl.entry_file] = render_index(tpl, inp.workflow_name)
    files[PACKAGE_FILE] = render_package(tpl, inp.server_name, inp.version)
    files[GENERATED_FILE] = render_generated(spec)
    info = {
        "template": {"name": tpl.name, "version": tpl.version, "sha256": tpl.sha256},
        "workflow_server": inp.server_name,
        "version": inp.version,
        "entry_file": tpl.entry_file,
        "allowed_hosts": list(inp.allowed_hosts),
        "files": file_digests(files),
    }
    files[BUNDLE_FILE] = (json.dumps(info, indent=2, sort_keys=True) + "\n").encode()
    ordered = dict(sorted(files.items()))
    digest = bundle_digest(ordered)
    entry = UiBundleEntry(
        version=inp.version,
        digest=digest,
        entry_file=tpl.entry_file,
        allowed_hosts=list(inp.allowed_hosts),
        workflow_server=inp.server_name,
    )
    return UiBundle(ordered, digest, tpl.ref, entry)
