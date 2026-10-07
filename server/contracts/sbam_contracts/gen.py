"""Contracts → JSON Schema → TypeScript, and the drift check (issue E1.2).

    python -m sbam_contracts.gen           # write every generated file (make contracts)
    python -m sbam_contracts.gen --check   # exit 1 naming each differing file (contracts-check)

Rules worth knowing
  * Pydantic is the only source. The generated files are
    `server/contracts/schemas/**/*.schema.json` (one per `registry.ROOTS` entry) and
    `client/src/contracts/index.ts`. Never edit them by hand.
  * TypeScript comes from one bundled schema (shared `$defs`, so every model is declared once),
    compiled by the pinned `json-schema-to-typescript` in `client/scripts/gen-contracts.mjs`. The
    header carries the RFC 8785 SHA-256 of that bundle.
  * Output is deterministic: model order comes from the registry, JSON is written the same way every
    time, and every file uses LF line endings on every OS.
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from sbam_contracts._jsonschema import DRAFT, contract_schema, schema_id
from sbam_contracts.canonical import sha256_of
from sbam_contracts.registry import ROOTS
from sbam_contracts.state_machines import export as state_machine_tables

CONTRACTS_DIR: Final = Path(__file__).resolve().parent.parent  # server/contracts
REPO: Final = CONTRACTS_DIR.parent.parent
SCHEMA_DIR: Final = CONTRACTS_DIR / "schemas"
TS_FILE: Final = REPO / "client" / "src" / "contracts" / "index.ts"
STATE_MACHINES_FILE: Final = SCHEMA_DIR / "state_machines.json"  # read-only tables for the UI
NODE_SCRIPT: Final = REPO / "client" / "scripts" / "gen-contracts.mjs"
TS_HEADER: Final = (
    "// GENERATED — DO NOT EDIT. Source: server/contracts (Pydantic).\n"
    "// Regenerate with `make contracts`; CI fails if this file differs from a fresh generation.\n"
)

JSON_VALUE_TS: Final = (
    "string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }"
)

Roots = Sequence[tuple[str, type[BaseModel]]]


def _json(data: Any) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _document_patterns(node: Any) -> None:
    """Copy each string `pattern` into the description, so it shows up in the TS doc comments."""
    if isinstance(node, dict):
        pattern = node.get("pattern")
        if isinstance(pattern, str):
            note = f"Pattern: `{pattern}`"
            text = node.get("description")
            node["description"] = f"{text}\n\n{note}" if text else note
        for value in node.values():
            _document_patterns(value)
    elif isinstance(node, list):
        for value in node:
            _document_patterns(value)


_NAME_MAPS: Final = frozenset({"properties", "patternProperties", "$defs"})


def _strip_for_ts(node: Any, names: bool = False) -> None:
    """Remove the `title` and `default` keywords. The TS compiler would export each title as its
    own alias and treat `{"$ref": X, "default": ...}` as a new type; TypeScript uses neither.

    `names` marks a map whose keys are field or definition names (`properties`, `$defs`), so a
    field that is itself called `title` or `default` is kept.
    """
    if isinstance(node, dict):
        if not names:
            node.pop("title", None)
            node.pop("default", None)
        for key, value in node.items():
            _strip_for_ts(value, names=not names and key in _NAME_MAPS)
    elif isinstance(node, list):
        for value in node:
            _strip_for_ts(value)


def ts_bundle(roots: Roots = ROOTS) -> dict[str, Any]:
    """One schema holding every root model under shared `$defs`, for the TypeScript compiler."""
    refs, definitions = models_json_schema([(model, "validation") for _, model in roots])
    defs: dict[str, Any] = definitions.get("$defs", {})
    for name, definition in defs.items():  # keep only each model's / enum's own name
        _strip_for_ts(definition)
        definition["title"] = name
    if "JsonValue" in defs:  # Pydantic's "any JSON" is an empty schema; say so precisely
        defs["JsonValue"] = {"title": "JsonValue", "tsType": JSON_VALUE_TS}
    bundle: dict[str, Any] = {
        "$schema": DRAFT,
        "$id": schema_id("bundle"),
        "title": "SbamContracts",
        "description": "Every SBAM contract root model (sbam_contracts/registry.py).",
        "type": "object",
        "additionalProperties": False,
        "properties": {model.__name__: refs[(model, "validation")] for _, model in roots},
        "$defs": defs,
    }
    _document_patterns(bundle)
    return bundle


def _compile_ts(bundle: dict[str, Any]) -> str:
    result = subprocess.run(  # noqa: S603 - fixed script path, our own JSON on stdin
        ["node", str(NODE_SCRIPT)],  # noqa: S607 - node from PATH, as for pnpm
        input=json.dumps(bundle).encode("utf-8"),
        capture_output=True,
        cwd=NODE_SCRIPT.parent.parent,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "TypeScript generation failed:\n" + result.stderr.decode("utf-8", "replace")
        )
    return result.stdout.decode("utf-8").replace("\r\n", "\n")


def generate(roots: Roots = ROOTS) -> dict[Path, bytes]:
    """Every generated file (absolute path) and its exact bytes."""
    files = {
        SCHEMA_DIR / f"{path}.schema.json": _json(contract_schema(model, path))
        for path, model in roots
    }
    files[STATE_MACHINES_FILE] = _json(state_machine_tables())
    bundle = ts_bundle(roots)
    header = TS_HEADER + f"// source-{sha256_of(bundle)}\n\n"
    files[TS_FILE] = (header + _compile_ts(bundle)).encode("utf-8")
    return files


def write(files: dict[Path, bytes]) -> None:
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def drift(files: dict[Path, bytes], root: Path = REPO) -> list[str]:
    """Repo-relative paths whose committed bytes differ from `files`, plus stale schema files.

    `root` lets tests compare against a copy of the tree; `files` are always computed for `REPO`.
    """
    differing = []
    for path, content in files.items():
        committed = root / path.relative_to(REPO)
        if not committed.is_file() or committed.read_bytes() != content:
            differing.append(path.relative_to(REPO).as_posix())
    expected = {path.relative_to(REPO) for path in files}
    schema_root = root / SCHEMA_DIR.relative_to(REPO)
    for stale in sorted(schema_root.rglob("*.schema.json")):
        relative = stale.relative_to(root)
        if relative not in expected:
            differing.append(f"{relative.as_posix()} (not generated by any registry entry)")
    return sorted(differing)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sbam_contracts.gen", description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if committed output differs")
    args = parser.parse_args(list(argv) if argv is not None else None)
    files = generate()
    if args.check:
        differing = drift(files)
        for path in differing:
            sys.stdout.write(f"contract drift: {path}\n")
        if differing:
            sys.stdout.write("run `make contracts` and commit the result\n")
            return 1
        sys.stdout.write(f"contracts up to date ({len(files)} files)\n")
        return 0
    write(files)
    sys.stdout.write(f"wrote {len(files)} files\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
