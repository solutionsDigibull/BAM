"""The AST gate's checks (E12.2, TD §12.5): AST_SYNTAX, AST_SKELETON, AST_CONFORMANCE.

Pure functions over bytes: the code is parsed with `ast.parse` and never imported, compiled or
executed. The persistence and the activity around them are in `ast_gate.py`.

* `read_bundle` verifies the tar against the hashes recorded when it was assembled (E11.3) before
  any check runs; any difference is a `HashMismatch`, never a finding.
* AST_SKELETON compares `ast.dump` of the bundle's file with a fresh render of the same template,
  outside the `LLM_BODY` slots. Slot bodies are masked to a single `pass` on the marker line, so
  line numbers still point into the bundle. Docstrings are compared whitespace-insensitively
  because Black rewrites their indentation.
* AST_CONFORMANCE checks the code against the manifest (not against the template): the tools
  registered in server.py by their exact names, every tool's signature, and that the check tool
  returns every gate identifier. A return it cannot prove statically fails closed.

Findings are sorted, so two runs over the same bundle give the same JSON (E12.2-T07).
"""

import ast
import hashlib
import io
import tarfile
import tokenize
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.canonical import canonical_json

from sbam_workers.build.codegen.bodies import _MARKER
from sbam_workers.build.codegen.bundle import _FILE_NAME, bundle_sha256
from sbam_workers.build.codegen.render import RenderedSkeleton, build_context, identifier
from sbam_workers.build.codegen.store import LoadedTemplate

GATE_VERSION: Final = "ast-gate/1.0.0"
MAX_TAR_BYTES: Final = 8 * 1024 * 1024
MAX_FILE_BYTES: Final = 1024 * 1024
MAX_FILES: Final = 32
MAX_DUMP_CHARS: Final = 240
EXPECTED_RETURN: Final = "dict[str, Any]"

CODES: Final = {
    GateName.AST_SYNTAX: ["AST_SYNTAX", "FILE_BOM", "FILE_NOT_UTF8"],
    GateName.AST_SKELETON: [
        "FILE_MISSING",
        "SKELETON_MISMATCH",
        "SLOT_MARKERS_MISMATCH",
        "SLOT_SHADOWS_SKELETON",
        "UNEXPECTED_FILE",
    ],
    GateName.AST_CONFORMANCE: [
        "CHECK_OUTPUT_MISSING",
        "PARAM_DEFAULT_MISMATCH",
        "PARAM_SIGNATURE_MISMATCH",
        "PARAM_TYPE_MISMATCH",
        "RETURN_TYPE_MISMATCH",
        "TOOL_FUNCTION_MISSING",
        "TOOL_NOT_REGISTERED",
        "TOOL_UNEXPECTED",
    ],
}
# Identifies the rules a verdict was made with; changes whenever a code or a limit changes.
CONFIG_SHA256: Final = hashlib.sha256(
    canonical_json(
        {
            "version": GATE_VERSION,
            "codes": {gate.value: codes for gate, codes in CODES.items()},
            "limits": {"tar": MAX_TAR_BYTES, "file": MAX_FILE_BYTES, "files": MAX_FILES},
        }
    )
).hexdigest()


class BundleError(Exception):
    """The stored bundle cannot be read as the tar E11.3 wrote (malformed, oversized)."""


class HashMismatchError(BundleError):
    """The bundle's bytes are not the ones whose hashes were recorded: it was changed afterwards."""


@dataclass(frozen=True)
class Finding:
    gate: GateName
    code: str
    message: str
    file: str | None = None
    line: int | None = None
    col: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """The `Finding` contract's shape (`sbam_contracts.build_dashboard.Finding`)."""
        return {
            "gate": self.gate.value,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "col": self.col,
        }

    def key(self) -> tuple[str, int, int, str, str]:
        return (self.file or "", self.line or 0, self.col or 0, self.code, self.message)


@dataclass(frozen=True)
class CheckResult:
    gate: GateName
    status: GateStatus
    findings: tuple[Finding, ...] = ()


def _result(gate: GateName, findings: Iterable[Finding]) -> CheckResult:
    ordered = tuple(sorted(findings, key=Finding.key))
    return CheckResult(gate, GateStatus.FAILED if ordered else GateStatus.PASSED, ordered)


# --- the bundle -----------------------------------------------------------------------------------


def read_bundle(
    tar: bytes, hashes: Mapping[str, str], expected_bundle_sha256: str
) -> dict[str, bytes]:
    """path -> bytes of a stored bundle, only if every hash recorded at assembly still holds."""
    if bundle_sha256(hashes) != expected_bundle_sha256:
        raise HashMismatchError("the recorded file hashes do not match the recorded bundle hash")
    if len(tar) > MAX_TAR_BYTES:
        raise BundleError(f"bundle is {len(tar)} bytes, over the {MAX_TAR_BYTES} limit")
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(tar), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) > MAX_FILES:
                raise BundleError(f"bundle has {len(members)} entries, over the {MAX_FILES} limit")
            for member in members:
                if not member.isreg() or not _FILE_NAME.fullmatch(member.name):
                    raise BundleError("bundle has an entry that is not a plainly named file")
                if member.name in files:
                    raise BundleError(f"bundle holds {member.name!r} twice")
                if member.size > MAX_FILE_BYTES:
                    raise BundleError(f"{member.name} is over the {MAX_FILE_BYTES} byte limit")
                handle = archive.extractfile(member)
                if handle is None:  # pragma: no cover - isreg() members always have one
                    raise BundleError(f"{member.name} cannot be read")
                files[member.name] = handle.read()
    except tarfile.TarError as exc:
        raise BundleError(f"bundle is not a readable tar: {exc}") from exc
    actual = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    if actual != dict(hashes):
        names = set(actual) | set(hashes)
        changed = sorted(n for n in names if actual.get(n) != hashes.get(n))
        raise HashMismatchError(f"bundle differs from its recorded hashes in: {', '.join(changed)}")
    return dict(sorted(files.items()))


# --- AST_SYNTAX -----------------------------------------------------------------------------------


def parse_file(name: str, data: bytes) -> tuple[ast.Module | None, Finding | None]:
    """Parse one file without executing it; a BOM, bad UTF-8 or a syntax error is a finding."""
    gate = GateName.AST_SYNTAX
    if data.startswith(b"\xef\xbb\xbf"):
        return None, Finding(gate, "FILE_BOM", "file starts with a UTF-8 BOM", name)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, Finding(gate, "FILE_NOT_UTF8", f"file is not UTF-8 (byte {exc.start})", name)
    try:
        return ast.parse(text, filename=name), None
    except SyntaxError as exc:
        message = f"does not parse: {exc.msg}"
        return None, Finding(gate, "AST_SYNTAX", message, name, exc.lineno, exc.offset)
    except (ValueError, RecursionError, MemoryError) as exc:
        message = f"cannot be parsed: {type(exc).__name__}"
        return None, Finding(gate, "AST_SYNTAX", message, name)


# --- AST_SKELETON ---------------------------------------------------------------------------------


def _mask(source: str) -> tuple[list[tuple[str, str]], str]:
    """(marker sequence, source with every slot body replaced by one `pass`, same line count)."""
    markers: list[tuple[str, str]] = []
    out: list[str] = []
    inside = False
    for line in source.split("\n"):
        match = _MARKER.match(line)
        if match:
            markers.append((match["kind"], match["slot"]))
            if match["kind"] == ">>>":
                out.append(f"{match['indent']}pass  # slot {match['slot']}")
                inside = True
            else:
                out.append(line)
                inside = False
        else:
            out.append("" if inside else line)
    return markers, "\n".join(out)


def _normalise_docstrings(tree: ast.AST) -> None:
    """Black re-indents docstrings; compare them with the whitespace around each line removed."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                lines = first.value.value.strip().split("\n")
                first.value.value = "\n".join(line.strip() for line in lines)


def _differs(a: object, b: object) -> bool:
    if isinstance(a, ast.AST) and isinstance(b, ast.AST):
        return ast.dump(a) != ast.dump(b)
    return a != b


def _first_difference(expected: ast.AST, actual: ast.AST, parent: ast.AST) -> tuple[Any, Any, Any]:
    """The deepest (expected, actual, anchor) where two trees differ; anchor has a line."""
    anchor = actual if hasattr(actual, "lineno") else parent
    if type(expected) is not type(actual):
        return expected, actual, anchor
    for field in expected._fields:
        e, a = getattr(expected, field, None), getattr(actual, field, None)
        if isinstance(e, list) and isinstance(a, list):
            for e_item, a_item in zip(e, a, strict=False):
                if _differs(e_item, a_item):
                    if isinstance(e_item, ast.AST) and isinstance(a_item, ast.AST):
                        return _first_difference(e_item, a_item, anchor)
                    return expected, actual, anchor
            if len(e) != len(a):
                return expected, actual, anchor
        elif _differs(e, a):
            if isinstance(e, ast.AST) and isinstance(a, ast.AST):
                return _first_difference(e, a, anchor)
            return expected, actual, anchor
    return expected, actual, anchor


def _dump(node: ast.AST | None) -> str:
    text = "<nothing>" if node is None else ast.dump(node, include_attributes=False)
    return text if len(text) <= MAX_DUMP_CHARS else text[:MAX_DUMP_CHARS] + "..."


def _marker_lines(source: str) -> list[int]:
    return [i for i, line in enumerate(source.split("\n"), 1) if _MARKER.match(line)]


def _comment_lines(source: str) -> set[int] | None:
    """Lines holding a real COMMENT token; None if the file cannot be tokenized."""
    try:
        return {
            tok.start[0]
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type == tokenize.COMMENT
        }
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None


def _slot_regions(source: str) -> list[tuple[int, int]]:
    """(first, last) line of each slot body, from the marker lines."""
    regions: list[tuple[int, int]] = []
    start = 0
    for number, line in enumerate(source.split("\n"), 1):
        match = _MARKER.match(line)
        if match and match["kind"] == ">>>":
            start = number + 1
        elif match:
            regions.append((start, number - 1))
    return regions


def _module_bindings(nodes: Iterable[ast.stmt]) -> set[str]:
    """Names the module-level statements `nodes` bind (not names local to a nested function)."""
    names: set[str] = set()
    for stmt in nodes:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(stmt.name)
        for node in [stmt, *_own_nodes(stmt)]:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                names.add(node.id)
            elif isinstance(node, ast.Import | ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if node is not stmt:
                    names.add(node.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
    return names


def _slot_escapes(name: str, fresh_tree: ast.Module, tree: ast.Module, text: str) -> list[Finding]:
    """A slot body must not rebind a name the skeleton defines, nor use `global`."""
    gate = GateName.AST_SKELETON
    regions = _slot_regions(text)

    def inside(node: ast.AST) -> bool:
        line = getattr(node, "lineno", 0)
        return any(first <= line <= last for first, last in regions)

    findings: list[Finding] = []
    # a bare annotation (`QUERIES: dict[str, str]`) declares a name for a slot to assign
    owned = _module_bindings(
        s for s in fresh_tree.body if not (isinstance(s, ast.AnnAssign) and s.value is None)
    )
    for stmt in tree.body:
        if inside(stmt):
            for clash in sorted(_module_bindings([stmt]) & owned):
                message = f"a slot body rebinds {clash!r}, which the skeleton defines"
                at = (name, stmt.lineno, stmt.col_offset)
                findings.append(Finding(gate, "SLOT_SHADOWS_SKELETON", message, *at))
    for node in ast.walk(tree):
        if isinstance(node, ast.Global) and inside(node):
            message = "a slot body must not use `global`"
            at = (name, node.lineno, node.col_offset)
            findings.append(Finding(gate, "SLOT_SHADOWS_SKELETON", message, *at))
    return findings


def _skeleton_file(name: str, fresh: str, bundle_text: str) -> list[Finding]:
    gate = GateName.AST_SKELETON
    fresh_markers, fresh_masked = _mask(fresh)
    markers, masked = _mask(bundle_text)
    comments = _comment_lines(bundle_text)
    if comments is not None and not set(_marker_lines(bundle_text)) <= comments:
        message = "an LLM_BODY marker is not a comment (it sits inside a string)"
        return [Finding(gate, "SLOT_MARKERS_MISMATCH", message, name)]
    if markers != fresh_markers:
        expected = ", ".join(f"{k}{s}" for k, s in fresh_markers)
        actual = ", ".join(f"{k}{s}" for k, s in markers)
        message = f"LLM_BODY markers differ: expected [{expected}], actual [{actual}]"
        return [Finding(gate, "SLOT_MARKERS_MISMATCH", message, name)]
    try:
        expected_tree, actual_tree = ast.parse(fresh_masked), ast.parse(masked)
        _normalise_docstrings(expected_tree)
        _normalise_docstrings(actual_tree)
        if ast.dump(expected_tree) == ast.dump(actual_tree):
            return _slot_escapes(name, expected_tree, ast.parse(bundle_text), bundle_text)
        e, a, anchor = _first_difference(expected_tree, actual_tree, actual_tree)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        # a body that closes its own region early leaves code the skeleton does not have
        message = "outside the LLM_BODY slots the file cannot be compared with the skeleton"
        return [Finding(gate, "SKELETON_MISMATCH", message, name)]
    message = f"differs from the skeleton: expected {_dump(e)}, actual {_dump(a)}"
    line, col = getattr(anchor, "lineno", None), getattr(anchor, "col_offset", None)
    return [Finding(gate, "SKELETON_MISMATCH", message, name, line, col)]


def check_skeleton(
    files: Mapping[str, bytes], skeleton: RenderedSkeleton, template: LoadedTemplate
) -> list[Finding]:
    gate = GateName.AST_SKELETON
    findings: list[Finding] = []
    expected_names = {entry.output for entry in template.files}
    for name in sorted(files):
        if name.endswith(".py") and name not in expected_names:
            findings.append(Finding(gate, "UNEXPECTED_FILE", "not a file of the template", name))
    for entry in template.files:
        if entry.output not in files:
            message = "the template's file is missing from the bundle"
            findings.append(Finding(gate, "FILE_MISSING", message, entry.output))
            continue
        findings += _skeleton_file(
            entry.output, skeleton.files[entry.output], files[entry.output].decode("utf-8")
        )
    return findings


# --- AST_CONFORMANCE ------------------------------------------------------------------------------

_Fn = ast.FunctionDef | ast.AsyncFunctionDef


def _unparse(node: ast.expr | None) -> str:
    return "<missing>" if node is None else ast.unparse(node)


def _normal(annotation: str) -> str:
    return ast.unparse(ast.parse(annotation, mode="eval").body)


def _registered(tree: ast.Module) -> dict[str, list[_Fn]]:
    """Tool name -> functions decorated `@mcp.tool(name=...)` (or bare `@mcp.tool`)."""
    found: dict[str, list[_Fn]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            call = deco if isinstance(deco, ast.Call) else None
            target = call.func if call else deco
            if not (
                isinstance(target, ast.Attribute)
                and target.attr == "tool"
                and isinstance(target.value, ast.Name)
                and target.value.id == "mcp"
            ):
                continue
            name = node.name
            for keyword in call.keywords if call else []:
                if keyword.arg == "name":
                    value = keyword.value
                    literal = isinstance(value, ast.Constant) and isinstance(value.value, str)
                    name = str(getattr(value, "value", "")) if literal else "<dynamic>"
            found.setdefault(name, []).append(node)
    return found


def _signature_findings(file: str, fn: _Fn, signature: list[dict[str, Any]]) -> list[Finding]:
    gate = GateName.AST_CONFORMANCE
    args = fn.args
    if args.posonlyargs or args.vararg or args.kwonlyargs or args.kwarg:
        message = f"{fn.name}: only plain positional-or-keyword parameters are allowed"
        return [Finding(gate, "PARAM_SIGNATURE_MISMATCH", message, file, fn.lineno, fn.col_offset)]
    names = [a.arg for a in args.args]
    expected_names = [f["name"] for f in signature]
    if names != expected_names:
        message = f"{fn.name}: parameters expected {expected_names}, actual {names}"
        return [Finding(gate, "PARAM_SIGNATURE_MISMATCH", message, file, fn.lineno, fn.col_offset)]
    findings: list[Finding] = []
    first_default = len(args.args) - len(args.defaults)
    for index, (arg, spec) in enumerate(zip(args.args, signature, strict=True)):
        expected = _normal(spec["annotation"] + ("" if spec["required"] else " | None"))
        actual = _unparse(arg.annotation)
        if actual != expected:
            message = f"{fn.name}({arg.arg}): annotation expected {expected}, actual {actual}"
            findings.append(
                Finding(gate, "PARAM_TYPE_MISMATCH", message, file, arg.lineno, arg.col_offset)
            )
        default = args.defaults[index - first_default] if index >= first_default else None
        has_none = isinstance(default, ast.Constant) and default.value is None
        if (spec["required"] and default is not None) or (not spec["required"] and not has_none):
            kind = "required" if spec["required"] else "optional"
            want = "no default" if spec["required"] else "a None default"
            message = f"{fn.name}({arg.arg}): a {kind} parameter must have {want}"
            findings.append(
                Finding(gate, "PARAM_DEFAULT_MISMATCH", message, file, arg.lineno, arg.col_offset)
            )
    if _unparse(fn.returns) != EXPECTED_RETURN:
        actual = _unparse(fn.returns)
        message = f"{fn.name}: return annotation expected {EXPECTED_RETURN}, actual {actual}"
        at = (file, fn.lineno, fn.col_offset)
        findings.append(Finding(gate, "RETURN_TYPE_MISMATCH", message, *at))
    return findings


def _function(tree: ast.Module, name: str) -> _Fn | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def _own_nodes(fn: ast.AST) -> Iterable[ast.AST]:
    """Nodes of `fn` that belong to it, not to a nested function, lambda or class."""
    todo = list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            todo.extend(ast.iter_child_nodes(node))


def _dict_keys(node: ast.expr) -> set[str] | None:
    """The constant string keys of a dict display or `dict(k=...)`; None if any key is dynamic."""
    if isinstance(node, ast.Dict):
        keys: set[str] = set()
        for key in node.keys:
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None  # `**spread` (key None) or a computed key
            keys.add(key.value)
        return keys
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        if node.args or any(k.arg is None for k in node.keywords):
            return None
        return {k.arg for k in node.keywords if k.arg is not None}
    return None


def _returned_keys(fn: _Fn, ret: ast.Return) -> set[str] | None:
    """Keys the dict returned by `ret` provably has, or None when that cannot be shown."""
    value = ret.value
    if value is None:
        return None
    direct = _dict_keys(value)
    if direct is not None or not isinstance(value, ast.Name):
        return direct
    # `return result`: `result` is one dict display, plus `result["k"] = ...` stores; anything
    # else touching the name (rebinding, augmented assignment, other targets) is not provable
    name = value.id
    keys: set[str] | None = None
    ordered = sorted(
        _own_nodes(fn), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))
    )
    for node in ordered:
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if isinstance(node, ast.AugAssign) or node.value is None or keys is not None:
                        return None
                    keys = _dict_keys(node.value)
                    if keys is None:
                        return None
                elif (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == name
                ):
                    index = target.slice
                    if keys is None or not (
                        isinstance(index, ast.Constant) and isinstance(index.value, str)
                    ):
                        return None
                    keys = keys | {index.value}
    return keys


def _terminates(body: list[ast.stmt]) -> bool:
    """True only if every path through `body` provably ends in `return` or `raise`."""
    if not body:
        return False
    last = body[-1]
    if isinstance(last, ast.Return | ast.Raise):
        return True
    if isinstance(last, ast.If):
        return _terminates(last.body) and _terminates(last.orelse)
    if isinstance(last, ast.With | ast.AsyncWith):
        return _terminates(last.body)
    if isinstance(last, ast.Try):
        if _terminates(last.finalbody):
            return True
        body_ok = _terminates(last.orelse) if last.orelse else _terminates(last.body)
        return body_ok and all(_terminates(h.body) for h in last.handlers)
    return False


def _check_outputs(file: str, fn: _Fn, outputs: list[str]) -> list[Finding]:
    gate = GateName.AST_CONFORMANCE
    returns = [n for n in _own_nodes(fn) if isinstance(n, ast.Return)]
    if not returns:
        message = f"{fn.name} never returns a dict; missing {', '.join(outputs)}"
        return [Finding(gate, "CHECK_OUTPUT_MISSING", message, file, fn.lineno, fn.col_offset)]
    findings: list[Finding] = []
    if not _terminates(fn.body):
        message = f"{fn.name} may fall through and return None; every path must return the outputs"
        findings.append(
            Finding(gate, "CHECK_OUTPUT_MISSING", message, file, fn.lineno, fn.col_offset)
        )
    for ret in returns:
        keys = _returned_keys(fn, ret)
        for output in outputs:
            if keys is None or output not in keys:
                reason = "not provable statically" if keys is None else "missing"
                message = f"{fn.name} must return {output}: {reason}"
                findings.append(
                    Finding(gate, "CHECK_OUTPUT_MISSING", message, file, ret.lineno, ret.col_offset)
                )
    return findings


def check_conformance(
    trees: Mapping[str, ast.Module], params: Mapping[str, Any], template: LoadedTemplate
) -> list[Finding]:
    gate = GateName.AST_CONFORMANCE
    signature: list[dict[str, Any]] = build_context(params, template)["signature"]
    action, check = identifier(params["action_tool"]), identifier(params["fixed_check_tool"])
    outputs = [identifier(g) for g in params["gate_outputs"]]
    findings: list[Finding] = []

    server = trees.get("server.py")
    if server is not None:
        registered = _registered(server)
        for name in (action, check):
            if name not in registered:
                message = f"tool {name!r} is not registered in server.py"
                findings.append(Finding(gate, "TOOL_NOT_REGISTERED", message, "server.py", 1, 0))
        for name, nodes in sorted(registered.items()):
            if name not in (action, check) or len(nodes) > 1:
                message = f"tool {name!r} is registered {len(nodes)} time(s), not once as expected"
                at = nodes[0]
                findings.append(
                    Finding(gate, "TOOL_UNEXPECTED", message, "server.py", at.lineno, at.col_offset)
                )
            else:
                findings += _signature_findings("server.py", nodes[0], signature)
    for file, name in (("action_tool.py", action), ("check_tool.py", check)):
        tree = trees.get(file)
        if tree is None:
            continue
        fn = _function(tree, name)
        if fn is None:
            message = f"function {name!r} is not defined"
            findings.append(Finding(gate, "TOOL_FUNCTION_MISSING", message, file, 1, 0))
            continue
        findings += _signature_findings(file, fn, signature)
        if file == "check_tool.py":
            findings += _check_outputs(file, fn, outputs)
    return findings


# --- all three ------------------------------------------------------------------------------------


def run_ast_checks(
    files: Mapping[str, bytes],
    skeleton: RenderedSkeleton,
    template: LoadedTemplate,
    params: Mapping[str, Any],
) -> tuple[CheckResult, CheckResult, CheckResult]:
    """AST_SYNTAX, AST_SKELETON, AST_CONFORMANCE. If a file does not parse the other two are
    SKIPPED, since there is no tree to compare."""
    trees: dict[str, ast.Module] = {}
    syntax: list[Finding] = []
    for name in sorted(files):
        if not name.endswith(".py"):
            continue
        tree, finding = parse_file(name, files[name])
        if finding is not None:
            syntax.append(finding)
        elif tree is not None:
            trees[name] = tree
    syntax_result = _result(GateName.AST_SYNTAX, syntax)
    if syntax_result.status is GateStatus.FAILED:
        return (
            syntax_result,
            CheckResult(GateName.AST_SKELETON, GateStatus.SKIPPED),
            CheckResult(GateName.AST_CONFORMANCE, GateStatus.SKIPPED),
        )
    return (
        syntax_result,
        _result(GateName.AST_SKELETON, check_skeleton(files, skeleton, template)),
        _result(GateName.AST_CONFORMANCE, check_conformance(trees, params, template)),
    )
