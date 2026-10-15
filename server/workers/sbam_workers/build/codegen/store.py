"""Template store: load a released template from Git and verify its hash (TD §12.3, O-06).

Layout (`server/templates/`, platform-owned)::

    registry.json                       released hashes: {"templates": {name: {version: sha256}}}
    <name>/<semver>/template.yaml       name, version, supports_isatvon, params_schema, ...
    <name>/<semver>/<file>.py.j2

The tree hash is the sha256 over the sorted file tree: for every file, in sorted posix-path
order, ``"<path>\\0<sha256 of the bytes>\\n"``. It is checked on every load against
`registry.json`, so a released template cannot change without a version bump (and a new
registry entry). The loader reads each file once and renders from those bytes, so what was
hashed is exactly what is rendered.

    uv run python -m sbam_workers.build.codegen fastmcp-loopy 1.0.0   # print the tree hash
"""

import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sbam_contracts.manifest import TemplateRef

from sbam_workers.build.codegen.errors import TemplateError, TemplateErrorCode

TEMPLATES_DIR_ENV: Final = "SBAM_TEMPLATES_DIR"
REGISTRY_FILE: Final = "registry.json"
SPEC_FILE: Final = "template.yaml"
_NAME: Final = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SEMVER: Final = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


def default_templates_dir() -> Path:
    """`$SBAM_TEMPLATES_DIR`, else `server/templates` next to this package in the repo."""
    override = os.environ.get(TEMPLATES_DIR_ENV)
    return Path(override) if override else Path(__file__).resolve().parents[4] / "templates"


@dataclass(frozen=True)
class TemplateFile:
    """One rendered file: the template it comes from and the slots the LLM fills in it."""

    output: str
    template: str
    context: tuple[str, ...]
    slots: Mapping[str, str]  # slot name -> CodeBodies field


@dataclass(frozen=True)
class LoadedTemplate:
    name: str
    version: str
    sha256: str
    supports_isatvon: tuple[str, ...]
    params_schema: Mapping[str, Any]
    base_imports: tuple[str, ...]
    files: tuple[TemplateFile, ...]
    sources: Mapping[str, str]  # template file name -> Jinja2 source (the hashed bytes)

    @property
    def ref(self) -> TemplateRef:
        """Registry entry: recorded in `sbam_build_record.build.template`."""
        return TemplateRef(name=self.name, version=self.version, sha256=self.sha256)


def tree_sha256(files: Mapping[str, bytes]) -> str:
    """sha256 of a file tree given as {posix path: bytes}; order of the mapping is irrelevant."""
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(f"{path}\0{hashlib.sha256(files[path]).hexdigest()}\n".encode())
    return digest.hexdigest()


def read_tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, once. Symlinks are refused (a link could point outside Git)."""
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TemplateError(TemplateErrorCode.TEMPLATE_INVALID, f"{path.name}: symlink")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _invalid(message: str) -> TemplateError:
    return TemplateError(TemplateErrorCode.TEMPLATE_INVALID, message)


def released_hash(root: Path, name: str, version: str) -> str:
    """The sha256 recorded in `registry.json` for a released template version."""
    try:
        registry = json.loads((root / REGISTRY_FILE).read_text(encoding="utf-8"))
        entry = registry["templates"][name][version]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TemplateError(
            TemplateErrorCode.TEMPLATE_NOT_FOUND,
            f"{name} {version} is not in {REGISTRY_FILE} (unreleased or registry unreadable)",
        ) from exc
    if not isinstance(entry, str) or not _SHA256.match(entry):
        raise _invalid(f"{REGISTRY_FILE}: {name} {version} is not a lowercase sha256")
    return entry


def _string_list(spec: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = spec.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _invalid(f"{SPEC_FILE}: {key} must be a list of strings")
    return tuple(value)


def _files(spec: Mapping[str, Any], tree: Mapping[str, bytes]) -> tuple[TemplateFile, ...]:
    raw = spec.get("files")
    if not isinstance(raw, list) or not raw:
        raise _invalid(f"{SPEC_FILE}: files must be a non-empty list")
    files: list[TemplateFile] = []
    for item in raw:
        try:
            slots = item["slots"]
            if not isinstance(slots, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in slots.items()
            ):
                raise TypeError("slots")
            entry = TemplateFile(
                output=str(item["output"]),
                template=str(item["template"]),
                context=_string_list(item, "context"),
                slots=dict(slots),
            )
        except (KeyError, TypeError, AttributeError, TemplateError) as exc:
            raise _invalid(f"{SPEC_FILE}: malformed files entry {item!r}") from exc
        if entry.template not in tree:
            raise _invalid(f"{SPEC_FILE}: {entry.template} is not in the template tree")
        files.append(entry)
    if len({f.output for f in files}) != len(files):
        raise _invalid(f"{SPEC_FILE}: duplicate output file")
    return tuple(files)


def load_template(name: str, version: str, *, root: Path | None = None) -> LoadedTemplate:
    """Load `<root>/<name>/<version>/`, verify its tree hash against the registry, parse it.

    Raises TemplateError: TEMPLATE_NOT_FOUND, TEMPLATE_HASH_MISMATCH (T06) or TEMPLATE_INVALID.
    """
    if not _NAME.match(name) or not _SEMVER.match(version):
        raise TemplateError(
            TemplateErrorCode.TEMPLATE_NOT_FOUND, f"bad template name/version: {name!r} {version!r}"
        )
    base = root if root is not None else default_templates_dir()
    directory = base / name / version
    if not directory.is_dir():
        raise TemplateError(TemplateErrorCode.TEMPLATE_NOT_FOUND, f"no template {name} {version}")
    tree = read_tree(directory)
    expected, actual = released_hash(base, name, version), tree_sha256(tree)
    if actual != expected:
        raise TemplateError(
            TemplateErrorCode.TEMPLATE_HASH_MISMATCH,
            f"{name} {version} changed since release (expected {expected}, found {actual}); "
            "released templates are immutable, bump the version instead",
        )
    try:
        spec = yaml.safe_load(tree[SPEC_FILE].decode("utf-8"))
        if not isinstance(spec, dict):
            raise TypeError(SPEC_FILE)
    except (KeyError, UnicodeDecodeError, yaml.YAMLError, TypeError) as exc:
        raise _invalid(f"{SPEC_FILE} is missing or not a YAML mapping") from exc
    if spec.get("name") != name or spec.get("version") != version:
        raise _invalid(f"{SPEC_FILE}: name/version must be {name} {version}")
    schema = spec.get("params_schema")
    try:
        if not isinstance(schema, dict):
            raise SchemaError("params_schema must be an object")
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise _invalid(f"{SPEC_FILE}: params_schema is not a valid JSON Schema: {exc}") from exc
    supports = _string_list(spec, "supports_isatvon")
    if not supports:
        raise _invalid(f"{SPEC_FILE}: supports_isatvon must not be empty")
    return LoadedTemplate(
        name=name,
        version=version,
        sha256=actual,
        supports_isatvon=supports,
        params_schema=schema,
        base_imports=_string_list(spec, "base_imports"),
        files=_files(spec, tree),
        sources={p: b.decode("utf-8") for p, b in tree.items() if p.endswith(".j2")},
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m sbam_workers.build.codegen <name> <version>", file=sys.stderr)
        return 2
    directory = default_templates_dir() / argv[0] / argv[1]
    print(tree_sha256(read_tree(directory)))
    return 0
