"""LLM bodies for a rendered skeleton (TD §12.2, E11.2): `sbam-codegen` -> `CodeBodies` -> slots.

Pipeline: skeleton + render params -> prompt context -> `LlmClient.run` -> pre-check -> slot fill.

* The model writes only the four `CodeBodies` fields. The output type forbids extra keys, so a
  reply that tries to set a platform field (status, template, imports) is re-asked, never kept.
* The context is the render params (the manifest's code-relevant part, one Sub-Skill of one
  project), the rendered skeleton and the import allowlist, each in a `<data>` block. ISATVON
  prose is data: nothing in it can widen the allowlist, which comes from the template's
  `base_imports` and the registry-resolved libraries only.
* The pre-check is a fast fail before assembly, not the authority: the AST gate (TD §12.5) runs
  on the assembled files afterwards. Rejected bodies go to the correction loop (GateFailed).
* `state_queries` is data: the assembler writes `QUERIES = {name: "SELECT ... %s"}` itself, so no
  Python from the model reaches state.py.
"""

import ast
import json
import re
import textwrap
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from sbam_workers.build.codegen.render import RenderedSkeleton, params_json, str_literal
from sbam_workers.build.codegen.store import LoadedTemplate
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.store import LlmScope

ALIAS: Final = "sbam-codegen"
PROMPT_ID: Final = "codegen-bodies"
MAX_BODY_CHARS: Final = 20000
STATE_QUERIES_FIELD: Final = "state_queries"
SIBLING_MODULES: Final = frozenset({"models", "state", "state_handoff"})  # `from . import ...`
# Never importable, whatever the manifest declares (TD §12.5 AST_IMPORTS).
FORBIDDEN_MODULES: Final = frozenset({"pickle", "marshal", "ctypes", "importlib", "builtins"})
DECLARABLE_MODULES: Final = frozenset({"subprocess"})  # forbidden unless a resolved library
RETRY_MODULES: Final = frozenset({"tenacity", "backoff", "retrying"})
FORBIDDEN_NAMES: Final = frozenset({"eval", "exec", "compile", "__import__"})
FORBIDDEN_ATTRS: Final = frozenset(
    {"system", "popen", "__globals__", "__builtins__", "__subclasses__", "__code__"}
)
_MODULE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
# SQL-shaped text: `SELECT ... FROM <table> WHERE/JOIN/...` (or FROM at the end, a table to be
# appended), INSERT INTO, UPDATE ... SET, DELETE FROM. Prose such as "select one from the list"
# does not match.
_SQL: Final = re.compile(
    r"\bselect\b.+?\bfrom\b\s*(?:[A-Za-z_][\w.]*\s*(?:\b(?:where|join|order|group|limit)\b|$)|$)"
    r"|\binsert\s+into\b|\bupdate\s+\w+\s+set\b|\bdelete\s+from\b",
    re.I | re.S,
)
_MARKER: Final = re.compile(r"^(?P<indent>[ \t]*)# (?P<kind>>>>|<<<) LLM_BODY:(?P<slot>\w+)$", re.M)

Body = Annotated[str, Field(max_length=MAX_BODY_CHARS)]
QueryName = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]*$", max_length=63)]


class CodeBodies(BaseModel):
    """What `sbam-codegen` may write: slot bodies and helpers, nothing else (TD §12.2)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action_body: Body = Field(description="Statements of the action tool body (no def line).")
    check_body: Body = Field(description="Statements of the check tool body (no def line).")
    state_queries: dict[QueryName, Body] = Field(
        max_length=1000,
        description="One parameterised SELECT per fresh-state name, key bound as the only %s.",
    )
    helpers: Body = Field(description="Module-level helper functions; may be empty.")


class BodyFindingCode(StrEnum):
    """Stable pre-check codes. Add new ones here; never rename or reuse one."""

    BODY_SYNTAX = "BODY_SYNTAX"  # a body does not parse, or the assembled file does not
    MANUAL_LOOP = "MANUAL_LOOP"  # `while` or a retry decorator/library: iteration is declarative
    FORBIDDEN_CALL = "FORBIDDEN_CALL"  # eval/exec/compile/__import__, os.system, dunder escapes
    FORBIDDEN_IMPORT = "FORBIDDEN_IMPORT"  # pickle, marshal, ctypes, undeclared subprocess, ...
    IMPORT_NOT_ALLOWED = "IMPORT_NOT_ALLOWED"  # not in base imports + resolved libraries
    SQL_UNSAFE = "SQL_UNSAFE"  # f-string / concatenated / formatted SQL, or a handoff write
    STATE_QUERY_INVALID = "STATE_QUERY_INVALID"  # not one SELECT with one %s per fresh-state name
    SHADOWS_SKELETON = "SHADOWS_SKELETON"  # a helper rebinds a name the skeleton defines


@dataclass(frozen=True)
class BodyFinding:
    code: BodyFindingCode
    field: str  # CodeBodies field (or `file:<name>` for an assembled file)
    message: str
    line: int | None = None  # 1-based, within the body

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "field": self.field,
            "line": self.line,
            "message": self.message,
        }


@dataclass(frozen=True)
class GeneratedBodies:
    bodies: CodeBodies
    findings: tuple[BodyFinding, ...]  # empty when the pre-check passed
    files: Mapping[str, str]  # assembled files (slots filled); empty when rejected
    allowlist: tuple[str, ...]
    llm_call_id: Any
    trace_id: str
    attempts: int  # model calls; 0 on a replay-cache hit
    cached: bool


# --- allowlist and context -----------------------------------------------------------------------


def import_allowlist(base_imports: Iterable[str], modules: Iterable[str]) -> tuple[str, ...]:
    """Template base imports + resolved libraries' top-level modules (E9.3), sorted and unique."""
    names = {*base_imports, *modules}
    bad = sorted(n for n in names if not _MODULE.match(n))
    if bad:
        raise ValueError(f"not module names: {bad}")
    if not names:
        raise ValueError("the import allowlist must not be empty")
    return tuple(sorted(names))


def request_context(
    params: Mapping[str, Any], skeleton: RenderedSkeleton, allowlist: Iterable[str]
) -> dict[str, str]:
    """The `<data>` blocks of the request: params, allowlist, every rendered file. Nothing else."""
    context = {
        "manifest": params_json(params),
        "allowed_imports": json.dumps(list(allowlist)),
    }
    for name, text in skeleton.files.items():
        context[f"skeleton.{name}"] = text
    return context


# --- pre-check -----------------------------------------------------------------------------------


def _parse(field: str, source: str, wrap: bool) -> tuple[ast.Module | None, BodyFinding | None]:
    text = textwrap.dedent(source)
    if wrap:
        text = "def _slot():\n" + textwrap.indent(text, "    ") + "\n    pass\n"
    try:
        return ast.parse(text), None
    except SyntaxError as exc:
        line = (exc.lineno or 1) - (1 if wrap else 0)
        return None, BodyFinding(
            BodyFindingCode.BODY_SYNTAX, field, f"does not parse: {exc.msg}", max(line, 1)
        )


def _name(node: ast.expr) -> str:
    """Dotted name of a Name/Attribute/Call chain, '' for anything else."""
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _sql_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "x"
            for v in node.values
        )
    return None


def _is_sql(node: ast.expr) -> bool:
    text = _sql_text(node)
    return text is not None and _SQL.search(text) is not None


class _Checker(ast.NodeVisitor):
    def __init__(self, field: str, allowlist: frozenset[str], offset: int, handoff: str) -> None:
        self.field = field
        self.allowlist = allowlist
        self.offset = offset
        # UPDATE / DELETE FROM the handoff table, however spaced or cased.
        self.handoff_write = (
            re.compile(rf"\b(?:update|delete\s+from)\s+{re.escape(handoff)}\b", re.I)
            if handoff
            else None
        )
        self.findings: list[BodyFinding] = []

    def add(self, code: BodyFindingCode, node: ast.AST, message: str) -> None:
        line = max(getattr(node, "lineno", 1) - self.offset, 1)
        self.findings.append(BodyFinding(code, self.field, message, line))

    def module(self, node: ast.AST, name: str) -> None:
        top = name.split(".")[0]
        if top in RETRY_MODULES:
            self.add(BodyFindingCode.MANUAL_LOOP, node, f"retry library {top!r}")
        elif top in FORBIDDEN_MODULES or (top in DECLARABLE_MODULES and top not in self.allowlist):
            self.add(BodyFindingCode.FORBIDDEN_IMPORT, node, f"import of {top!r}")
        elif top not in self.allowlist:
            self.add(BodyFindingCode.IMPORT_NOT_ALLOWED, node, f"{top!r} is not an allowed import")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.module(node, alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0:
            self.module(node, node.module or "")
            return
        names = [node.module] if node.module else [a.name for a in node.names]
        for name in names:
            if node.level != 1 or name.split(".")[0] not in SIBLING_MODULES:
                self.add(
                    BodyFindingCode.IMPORT_NOT_ALLOWED,
                    node,
                    f"relative import of {name!r}; only {sorted(SIBLING_MODULES)}",
                )

    def visit_While(self, node: ast.While) -> None:
        self.add(
            BodyFindingCode.MANUAL_LOOP, node, "while loop: iteration is declarative (V bounds)"
        )
        self.generic_visit(node)

    def _decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            if "retry" in _name(decorator).lower() or "backoff" in _name(decorator).lower():
                self.add(BodyFindingCode.MANUAL_LOOP, decorator, "retry decorator")
        self.generic_visit(node)

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _decorators  # noqa: N815

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES:
            self.add(BodyFindingCode.FORBIDDEN_CALL, node, f"{node.id} is forbidden")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRS or node.attr in FORBIDDEN_NAMES:
            self.add(BodyFindingCode.FORBIDDEN_CALL, node, f".{node.attr} is forbidden")
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        if len(node.values) > 1 and _is_sql(node):
            self.add(BodyFindingCode.SQL_UNSAFE, node, "SQL built with an f-string")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Add | ast.Mod) and (_is_sql(node.left) or _is_sql(node.right)):
            self.add(BodyFindingCode.SQL_UNSAFE, node, "SQL built by concatenation or % formatting")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format" and _is_sql(func.value):
            self.add(BodyFindingCode.SQL_UNSAFE, node, "SQL built with str.format")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and self.handoff_write is not None:
            if self.handoff_write.search(node.value):
                self.add(BodyFindingCode.SQL_UNSAFE, node, "the handoff table is append-only")


def _bound_names(tree: ast.Module) -> dict[str, int]:
    """Names a module binds at top level, with their line."""
    names: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.setdefault(node.name, node.lineno)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                names.setdefault((alias.asname or alias.name).split(".")[0], node.lineno)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        names.setdefault(sub.id, node.lineno)
    return names


def _check_state_queries(
    queries: Mapping[str, str], fresh_state: Iterable[str]
) -> list[BodyFinding]:
    field = STATE_QUERIES_FIELD
    expected = list(fresh_state)
    findings: list[BodyFinding] = []
    for name in sorted(set(queries) - set(expected)):
        findings.append(
            BodyFinding(BodyFindingCode.STATE_QUERY_INVALID, field, f"{name!r} is not fresh state")
        )
    for name in [n for n in expected if n not in queries]:
        findings.append(
            BodyFinding(BodyFindingCode.STATE_QUERY_INVALID, field, f"no query for {name!r}")
        )
    for name, sql in sorted(queries.items()):
        text = sql.strip()
        if not text.upper().startswith("SELECT") or text.count("%s") != 1 or ";" in text:
            findings.append(
                BodyFinding(
                    BodyFindingCode.STATE_QUERY_INVALID,
                    field,
                    f"{name!r} must be one SELECT with exactly one %s and no ';'",
                )
            )
        elif text.replace("%s", "").count("%") or "{" in text:
            findings.append(
                BodyFinding(BodyFindingCode.SQL_UNSAFE, field, f"{name!r} has a format placeholder")
            )
    return findings


def precheck(
    bodies: CodeBodies,
    allowlist: Iterable[str],
    params: Mapping[str, Any],
    skeleton: RenderedSkeleton,
) -> list[BodyFinding]:
    """Every finding for the bodies, in field order. Empty means: assemble."""
    allowed = frozenset(allowlist)
    handoff = str(params.get("handoff_table", ""))
    findings: list[BodyFinding] = []
    for field, wrap in (("helpers", False), ("action_body", True), ("check_body", True)):
        tree, error = _parse(field, getattr(bodies, field), wrap)
        if error is not None:
            findings.append(error)
            continue
        if tree is None:  # unreachable: _parse returns one of the two
            continue
        checker = _Checker(field, allowed, 1 if wrap else 0, handoff)
        checker.visit(tree)
        findings.extend(checker.findings)
        if field == "helpers":
            findings.extend(_shadowing(tree, skeleton))
    findings.extend(_check_state_queries(bodies.state_queries, params.get("fresh_state", [])))
    return findings


def _shadowing(helpers: ast.Module, skeleton: RenderedSkeleton) -> list[BodyFinding]:
    reserved: set[str] = set()
    for text in skeleton.files.values():
        if "LLM_BODY:helpers" in text:
            reserved |= set(_bound_names(ast.parse(text)))
    return [
        BodyFinding(BodyFindingCode.SHADOWS_SKELETON, "helpers", f"rebinds {name!r}", line)
        for name, line in sorted(_bound_names(helpers).items(), key=lambda item: item[1])
        if name in reserved
    ]


# --- assembly ------------------------------------------------------------------------------------


def queries_literal(queries: Mapping[str, str], fresh_state: Iterable[str]) -> str:
    """`QUERIES = {...}` in fresh-state order, every value a repr-escaped string literal."""
    names = [n for n in fresh_state if n in queries]
    if not names:
        return "QUERIES = {}"
    lines = [f"    {str_literal(n)}: {str_literal(queries[n].strip())}," for n in names]
    return "QUERIES = {\n" + "\n".join(lines) + "\n}"


def fill_slots(source: str, contents: Mapping[str, str]) -> str:
    """Replace what is between each slot's markers with `contents[slot]`, at the marker's indent.

    The markers stay, so the AST gate can find the regions again (AST_SKELETON).
    """
    out: list[str] = []
    lines = source.split("\n")
    open_slot: str | None = None
    for line in lines:
        match = _MARKER.match(line)
        if match and match["kind"] == "<<<":
            if match["slot"] != open_slot:
                raise ValueError(f"unbalanced LLM_BODY marker {match['slot']!r}")
            open_slot = None
        elif open_slot is not None:
            continue  # the unfilled region (or a previous body) is dropped
        out.append(line)
        if match and match["kind"] == ">>>":
            open_slot = match["slot"]
            body = textwrap.dedent(contents.get(open_slot, "")).strip("\n")
            if body:
                out.extend(textwrap.indent(body, match["indent"]).split("\n"))
    if open_slot is not None:
        raise ValueError(f"LLM_BODY:{open_slot} is never closed")
    return "\n".join(out)


def assemble(
    template: LoadedTemplate,
    skeleton: RenderedSkeleton,
    bodies: CodeBodies,
    fresh_state: Iterable[str],
) -> tuple[dict[str, str], list[BodyFinding]]:
    """Fill every slot declared in template.yaml; each filled file must still parse.

    Black formatting and bundle storage are E11.3; this is the splice they start from.
    """
    values = bodies.model_dump()
    values[STATE_QUERIES_FIELD] = queries_literal(bodies.state_queries, fresh_state)
    files: dict[str, str] = {}
    findings: list[BodyFinding] = []
    for entry in template.files:
        text = skeleton.files[entry.output]
        if entry.slots:
            text = fill_slots(text, {slot: values[field] for slot, field in entry.slots.items()})
            try:
                ast.parse(text)
            except SyntaxError as exc:
                findings.append(
                    BodyFinding(
                        BodyFindingCode.BODY_SYNTAX,
                        f"file:{entry.output}",
                        f"assembled file does not parse: {exc.msg}",
                        exc.lineno,
                    )
                )
        files[entry.output] = text
    return files, findings


# --- the call ------------------------------------------------------------------------------------


async def generate_bodies(
    client: LlmClient,
    scope: LlmScope,
    template: LoadedTemplate,
    params: Mapping[str, Any],
    skeleton: RenderedSkeleton,
    modules: Iterable[str],
    *,
    prompt_version: str | None = None,
) -> GeneratedBodies:
    """Ask `sbam-codegen` for the bodies, pre-check them and fill the slots.

    Raises what `LlmClient.run` raises (SchemaInvalidAfterRetries, LlmTransportError, ...);
    rejected bodies are returned with their findings and no files, for the correction loop.
    """
    allowlist = import_allowlist(template.base_imports, modules)
    result = await client.run(
        ALIAS,
        PROMPT_ID,
        CodeBodies,
        request_context(params, skeleton, allowlist),
        scope,
        prompt_version=prompt_version,
        metadata={"template": f"{template.name}/{template.version}"},
    )
    findings = precheck(result.output, allowlist, params, skeleton)
    files: Mapping[str, str] = {}
    if not findings:
        assembled, findings = assemble(
            template, skeleton, result.output, params.get("fresh_state", [])
        )
        files = {} if findings else assembled
    return GeneratedBodies(
        bodies=result.output,
        findings=tuple(findings),
        files=files,
        allowlist=allowlist,
        llm_call_id=result.llm_call_id,
        trace_id=result.trace_id,
        attempts=result.attempts,
        cached=result.cached,
    )
