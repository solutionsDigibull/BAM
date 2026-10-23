"""Source code (E4.6): Python is read with `ast`, the other languages as text.

Nothing in an uploaded file is ever imported, compiled to a code object or run: `ast.parse` only
builds a tree. `doc.md` is the source in a fenced block (with a list of Python symbols above it) and
`doc.json` has one paragraph per run of lines, as for plain text, plus a heading block for every
Python class, function and method with its 1-based source line (`class Line`, `function f`,
`method Line.__init__`). A Python file that does not parse is read as text, with a warning.
"""

import ast
import re
from collections.abc import Callable

from sbam_contracts.documents import Block, BlockType, ParseReport

from sbam_workers.ingest.loaders.base import Heartbeat, Parsed, decode
from sbam_workers.ingest.loaders.text import load_text

Symbol = tuple[str, str, int]  # kind, name, line


def load_python(data: bytes, beat: Heartbeat) -> Parsed:
    base = load_text(data, beat)  # UTF-8, not empty; the paragraphs of the source
    source = decode(data)
    report = ParseReport()
    try:
        tree = ast.parse(source)  # parses only: no import, no exec, no eval of the file
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        report.warnings.append("the file is not valid Python: it was read as plain text")
        return _result(base, source, "python", [], report)
    symbols = sorted(_symbols(tree), key=lambda s: s[2])
    beat()
    return _result(base, source, "python", symbols, report)


def _symbols(tree: ast.AST) -> list[Symbol]:
    found: list[Symbol] = []

    def walk(node: ast.AST, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                found.append(("class", _dotted(owner, child.name), child.lineno))
                walk(child, child.name if owner is None else f"{owner}.{child.name}")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                kind = "function" if owner is None else "method"
                found.append((kind, _dotted(owner, child.name), child.lineno))
                walk(child, None if owner is None else f"{owner}.{child.name}")
            else:
                walk(child, owner)

    walk(tree, None)
    return found


def _dotted(owner: str | None, name: str) -> str:
    return name if owner is None else f"{owner}.{name}"


def _text_loader(language: str) -> Callable[[bytes, Heartbeat], Parsed]:
    def load(data: bytes, beat: Heartbeat) -> Parsed:
        base = load_text(data, beat)
        return _result(base, decode(data), language, [], ParseReport())

    return load


load_sql = _text_loader("sql")
load_javascript = _text_loader("javascript")
load_go = _text_loader("go")
load_cpp = _text_loader("cpp")


def _result(
    base: Parsed, source: str, language: str, symbols: list[Symbol], report: ParseReport
) -> Parsed:
    heads = [
        Block(type=BlockType.HEADING, text=f"{kind} {name}", level=2, line=line)
        for kind, name, line in symbols
    ]
    blocks = sorted(
        [*heads, *base.blocks], key=lambda b: (b.line or 0, b.type != BlockType.HEADING)
    )
    fence = "````" if re.search(r"^```", source, re.MULTILINE) else "```"
    listing = "".join(f"- {kind} `{name}` (line {line})\n" for kind, name, line in symbols)
    markdown = (
        f"{listing}\n" if listing else ""
    ) + f"{fence}{language}\n{source.rstrip(chr(10))}\n{fence}\n"
    return Parsed(blocks=blocks, markdown=markdown, report=report)
