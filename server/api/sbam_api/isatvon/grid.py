"""Grid metadata for the ISATVON workbench (TD §8.8, E8.6), generated from the contract model.

The columns are derived by walking the JSON Schema of `sbam_contracts.isatvon14.SubSkill`, so a
field added to the model shows up here with a default editor chosen by its type. Nothing is
hand-listed except `EDITOR_OVERRIDES` (hints for fields whose type is not enough to pick an editor)
and the platform fields that live on the Sub-Skill row rather than in the model. A contract test
fails when the two diverge. The response holds no tenant data: it describes the model only.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from typing import Any, Final

from sbam_contracts.canonical import canonical_json
from sbam_contracts.isatvon14 import FRAMEWORK_VERSION, MAX_ITERATIONS_RANGE, SHORT_CHARS, schemas

# The response shapes live in the contracts; re-exported (`X as X`) for callers of this module.
from sbam_contracts.isatvon_grid import Editor as Editor
from sbam_contracts.isatvon_grid import Grid as Grid
from sbam_contracts.isatvon_grid import GridColumn as GridColumn
from sbam_contracts.isatvon_grid import GridGroup as GridGroup
from sbam_contracts.isatvon_grid import IsatvonSchemaResponse as IsatvonSchemaResponse
from sbam_contracts.state_machines import SubSkillState

IDEF: Final = "isatvon_definition."
_ACRONYMS: Final = {
    "Json": "JSON",
    "Id": "ID",
    "Sha256": "SHA-256",
    "Ui": "UI",
    "Ml": "ML",
    "Srim": "SRIM",
}


_WIDTHS: Final = {
    Editor.TEXT: 180,
    Editor.LARGE_TEXT: 320,
    Editor.NUMBER: 110,
    Editor.BOOLEAN: 100,
    Editor.JSON: 280,
    Editor.EXPRESSION: 260,
    Editor.STRING_LIST: 240,
    Editor.SELECT: 160,
    Editor.READONLY: 160,
}

# Editor hints where the type alone is not enough: gate expressions are strings in the model.
EDITOR_OVERRIDES: Final[dict[str, Editor]] = {
    IDEF + "v_variables.success_gate": Editor.EXPRESSION,
    IDEF + "v_variables.no_op_condition": Editor.EXPRESSION,
    IDEF + "v_variables.escalation_condition": Editor.EXPRESSION,
}
# Range rules the model enforces in `validate_sub_skill` (rule 5), not in its JSON Schema.
RANGE_OVERRIDES: Final[dict[str, tuple[int, int]]] = {
    IDEF + "v_variables.max_iterations": MAX_ITERATIONS_RANGE,
}
# Model fields the platform owns: shown, never edited from the grid.
READONLY_PATHS: Final = frozenset({"skill_metadata.skill_id"})


@dataclass(frozen=True)
class GroupSpec:
    id: str
    header: str
    description: str
    prefix: str  # dotted path in the SubSkill model


# Column groups in display order; `Status` is appended below (platform fields, not in the model).
MODEL_GROUPS: Final = (
    GroupSpec("metadata", "Metadata", "Identity and masters references", "skill_metadata"),
    GroupSpec("i", "I", "Instructions", IDEF + "i_instructions"),
    GroupSpec("s", "S", "Source", IDEF + "s_source"),
    GroupSpec("a", "A", "Automation", IDEF + "a_automation"),
    GroupSpec("t", "T", "Tech stack", IDEF + "t_tech_stack"),
    GroupSpec("v", "V", "Variables and loop bounds", IDEF + "v_variables"),
    GroupSpec("o", "O", "Outcome", IDEF + "o_outcome"),
    GroupSpec("n", "N", "Next and notification", IDEF + "n_notification"),
)
STATUS_GROUP: Final = GroupSpec("status", "Status", "Platform-managed, read only", "")
# Sub-Skill row fields outside the model (state machine, hashing, validation): (path, type, enum).
PLATFORM_FIELDS: Final = (
    ("state", "string", [s.value for s in SubSkillState]),
    ("content_sha256", "string", None),
    ("validation_errors", "array", None),
)


@dataclass(frozen=True)
class Rendered:
    """The response, built once per process: it only changes when the contract does."""

    body: bytes
    etag: str
    payload: IsatvonSchemaResponse


# --- Walking the JSON Schema ----------------------------------------------------------------


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Follow `$ref` and unwrap `X | None`; a union of several types is a JSON value."""
    while "$ref" in node:
        node = defs[node["$ref"].rsplit("/", 1)[-1]]
    if "anyOf" in node:
        branches = [b for b in node["anyOf"] if b.get("type") != "null"]
        return _resolve(branches[0], defs) if len(branches) == 1 else {"type": "object"}
    return node


def _leaves(
    node: dict[str, Any], defs: dict[str, Any], path: tuple[str, ...], required: bool
) -> Iterator[tuple[tuple[str, ...], dict[str, Any], bool]]:
    """Leaf fields: everything that is not a nested model. Lists, dicts and JSON are leaves."""
    node = _resolve(node, defs)
    properties = node.get("properties")
    if node.get("type") == "object" and properties:
        needed = set(node.get("required", []))
        for name, child in properties.items():
            yield from _leaves(child, defs, (*path, name), required and name in needed)
    else:
        yield path, node, required


def _type(node: dict[str, Any]) -> str:
    return "string" if "enum" in node else str(node.get("type", "object"))


def default_editor(node: dict[str, Any]) -> Editor:
    """Editor for a field with no hint, chosen by its type."""
    if "enum" in node:
        return Editor.SELECT
    match node.get("type"):
        case "boolean":
            return Editor.BOOLEAN
        case "integer" | "number":
            return Editor.NUMBER
        case "string":
            return Editor.LARGE_TEXT if node.get("maxLength", 0) > SHORT_CHARS else Editor.TEXT
        case "array":
            items = node.get("items", {})
            return Editor.STRING_LIST if items.get("type") == "string" else Editor.JSON
        case _:
            return Editor.JSON


def _header(path: str) -> str:
    words = path.replace(".", " ").replace("_", " ").title().split()
    return " ".join(_ACRONYMS.get(w, w) for w in words)


def _column(
    path: tuple[str, ...], node: dict[str, Any], required: bool, group: GroupSpec
) -> GridColumn:
    dotted = ".".join(path)
    editable = dotted not in READONLY_PATHS
    editor = EDITOR_OVERRIDES.get(dotted) or default_editor(node)
    low, high = RANGE_OVERRIDES.get(dotted) or (node.get("minimum"), node.get("maximum"))
    if not editable:
        editor = Editor.READONLY
    return GridColumn(
        field_path=dotted,
        header=_header(dotted.removeprefix(group.prefix).lstrip(".")),
        type=_type(node),
        editor=editor,
        required=required,
        editable=editable,
        platform=not editable,
        enum=[str(v) for v in node["enum"]] if "enum" in node else None,
        min=low if editor is Editor.NUMBER else None,
        max=high if editor is Editor.NUMBER else None,
        width=_WIDTHS[editor],
    )


def _group_of(dotted: str) -> GroupSpec:
    for group in MODEL_GROUPS:
        if dotted == group.prefix or dotted.startswith(group.prefix + "."):
            return group
    raise ValueError(f"{dotted!r} belongs to no grid column group; add one to MODEL_GROUPS")


def build_grid(json_schema: dict[str, Any]) -> Grid:
    defs = json_schema.get("$defs", {})
    by_group: dict[str, list[GridColumn]] = {g.id: [] for g in MODEL_GROUPS}
    for path, node, required in _leaves(json_schema, defs, (), True):
        group = _group_of(".".join(path))
        by_group[group.id].append(_column(path, node, required, group))
    groups = [
        GridGroup(id=g.id, header=g.header, description=g.description, children=by_group[g.id])
        for g in MODEL_GROUPS
    ]
    status = [
        GridColumn(
            field_path=name,
            header=_header(name),
            type=kind,
            editor=Editor.READONLY,
            required=False,
            editable=False,
            platform=True,
            enum=values,
            width=_WIDTHS[Editor.READONLY],
        )
        for name, kind, values in PLATFORM_FIELDS
    ]
    groups.append(
        GridGroup(
            id=STATUS_GROUP.id,
            header=STATUS_GROUP.header,
            description=STATUS_GROUP.description,
            children=status,
        )
    )
    return Grid(column_groups=groups)


def resolves(json_schema: dict[str, Any], dotted: str) -> bool:
    """Whether a dotted field path names a field in `json_schema` (used by the contract test)."""
    defs = json_schema.get("$defs", {})
    node = json_schema
    for part in dotted.split("."):
        node = _resolve(node, defs)
        properties = node.get("properties")
        if not isinstance(properties, dict) or part not in properties:
            return False
        node = properties[part]
    return True


# --- Response -------------------------------------------------------------------------------


@cache
def render(version: str) -> Rendered | None:
    """The 1.4 response with its strong ETag, or None for a version not served."""
    if version != FRAMEWORK_VERSION:
        return None
    json_schema = schemas()["sub_skill"]
    payload = IsatvonSchemaResponse(
        version=FRAMEWORK_VERSION, json_schema=json_schema, grid=build_grid(json_schema)
    )
    document = {
        "version": payload.version,
        "json_schema": json_schema,  # verbatim: `default: null` entries are meaningful there
        "grid": payload.grid.model_dump(mode="json", exclude_none=True),
    }
    # The ETag hashes the canonical form, so it does not depend on key order or formatting.
    etag = '"' + hashlib.sha256(canonical_json(document)).hexdigest() + '"'
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    return Rendered(body=body, etag=etag, payload=payload)
