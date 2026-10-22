"""Assembly (E11.3, TD §12.2): skeleton + bodies -> Black -> a deterministic, hashed bundle.

* Bodies are normalised first (CRLF/CR -> LF, tabs -> 4 spaces), then spliced only between the
  `LLM_BODY` markers by `fill_slots`; the markers stay for AST_SKELETON.
* Every file is formatted with Black in the repo's mode (line length 100, py312). The Black version
  and the sha256 of that mode are returned so the attempt records exactly what formatted it.
* File names are fixed by template.yaml (+ `loop_spec.json`), never by the model, and are checked
  against a strict pattern before anything is hashed or archived.
* `bundle_sha256` = sha256 of the RFC 8785 JSON of `{path: sha256}`. The tar is USTAR (no PAX
  headers, so no extended attributes) with sorted entries, mtime 0, uid/gid 0 and no owner names:
  the same files always give the same bytes.

A body that makes a file unparsable is not an exception: it becomes an AST_SYNTAX finding, so the
correction loop gets it like any other gate failure.
"""

import ast
import hashlib
import io
import re
import tarfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Final

import black
from black.mode import Mode, TargetVersion
from black.parsing import InvalidInput
from sbam_contracts.canonical import canonical_json

from sbam_workers.build.codegen.bodies import STATE_QUERIES_FIELD, fill_slots, queries_literal
from sbam_workers.build.codegen.render import RenderedSkeleton
from sbam_workers.build.codegen.store import LoadedTemplate

LOOP_SPEC_FILE: Final = "loop_spec.json"
TAB: Final = "    "
SYNTAX_GATE: Final = "AST_SYNTAX"
_FILE_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,62}\.(py|json)$")
BLACK_MODE: Final = Mode(
    target_versions={TargetVersion.PY312},
    line_length=100,
)


def black_config() -> dict[str, Any]:
    """The Black settings that affect the output; hashed into `black_config_sha256`."""
    return {
        "line_length": BLACK_MODE.line_length,
        "target_versions": sorted(v.name for v in BLACK_MODE.target_versions),
        "string_normalization": BLACK_MODE.string_normalization,
        "magic_trailing_comma": BLACK_MODE.magic_trailing_comma,
        "preview": BLACK_MODE.preview,
    }


BLACK_VERSION: Final = version("black")
BLACK_CONFIG_SHA256: Final = hashlib.sha256(canonical_json(black_config())).hexdigest()


@dataclass(frozen=True)
class SyntaxFinding:
    """AST_SYNTAX-ready: the shape E12.2 writes into `gate_results.findings`."""

    file: str
    message: str
    line: int | None = None
    col: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": SYNTAX_GATE,
            "code": SYNTAX_GATE,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "message": self.message,
        }


@dataclass(frozen=True)
class Bundle:
    files: Mapping[str, bytes]  # path -> bytes, sorted by path
    hashes: Mapping[str, str]  # path -> sha256, sorted by path
    bundle_sha256: str
    tar: bytes

    @property
    def size(self) -> int:
        return len(self.tar)


@dataclass(frozen=True)
class Assembled:
    bundle: Bundle | None  # None when a file did not parse
    findings: tuple[SyntaxFinding, ...]


def normalize(body: str) -> str:
    """LF line endings and 4-space indentation (a tab is one indent level)."""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.expandtabs(len(TAB)) for line in text.split("\n"))


def _check_name(name: str) -> str:
    if not _FILE_NAME.fullmatch(name):
        raise ValueError(f"bundle file name {name!r} is not a plain module or JSON name")
    return name


def bundle_sha256(hashes: Mapping[str, str]) -> str:
    return hashlib.sha256(canonical_json(dict(hashes))).hexdigest()


def deterministic_tar(files: Mapping[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(_check_name(name))
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def make_bundle(files: Mapping[str, bytes]) -> Bundle:
    ordered = {_check_name(name): files[name] for name in sorted(files)}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in ordered.items()}
    return Bundle(ordered, hashes, bundle_sha256(hashes), deterministic_tar(ordered))


def _format(name: str, text: str) -> tuple[str | None, SyntaxFinding | None]:
    try:
        ast.parse(text, filename=name)
    except SyntaxError as exc:
        return None, SyntaxFinding(name, f"does not parse: {exc.msg}", exc.lineno, exc.offset)
    try:
        return black.format_str(text, mode=BLACK_MODE), None
    except InvalidInput as exc:  # parses for CPython, not for Black's grammar
        return None, SyntaxFinding(name, f"Black cannot parse it: {exc}")


def assemble_bundle(
    template: LoadedTemplate,
    skeleton: RenderedSkeleton,
    bodies: Mapping[str, Any],
    fresh_state: Iterable[str],
    loop_spec: bytes,
) -> Assembled:
    """Splice normalised `bodies` (a CodeBodies dump) into the skeleton, Black every file, bundle.

    Raises ValueError only for a broken template (unbalanced markers, a bad output name); anything
    the bodies get wrong is a finding.
    """
    queries = {k: normalize(v) for k, v in bodies[STATE_QUERIES_FIELD].items()}
    values = {k: normalize(v) for k, v in bodies.items() if k != STATE_QUERIES_FIELD}
    values[STATE_QUERIES_FIELD] = queries_literal(queries, fresh_state)
    files: dict[str, bytes] = {}
    findings: list[SyntaxFinding] = []
    for entry in template.files:
        name = _check_name(entry.output)
        text = skeleton.files[entry.output]
        if entry.slots:
            text = fill_slots(text, {slot: values[field] for slot, field in entry.slots.items()})
        formatted, finding = _format(name, text)
        if finding is not None:
            findings.append(finding)
        elif formatted is not None:
            files[name] = formatted.encode("utf-8")
    if findings:
        return Assembled(None, tuple(findings))
    files[LOOP_SPEC_FILE] = loop_spec
    return Assembled(make_bundle(files), ())
