"""JSON, XML and YAML, including OpenAPI, Swagger and FastMCP specs (E4.6): a key-path index.

Every value of the document is one line of `doc.md` and one paragraph block, `path: value`. The
path is dotted (`paths./quotes.post.summary`, `tools[0].name`, `bom.line[1].@cpn`), and the block's
`line` is its line in `doc.md`, so a key path can be found again in `doc.json`. An OpenAPI or
Swagger file is read as the plain structure it is; its operations show up as `paths.<url>.<method>`.

The file is data, never code:
  * JSON is parsed strictly (no NaN or Infinity) and a depth over 100 is refused;
  * XML goes through defusedxml with DTDs and entities forbidden (XXE and entity bombs);
  * YAML goes through `safe_load` (no object construction) and the walk over the result counts every
    expanded node, so an alias bomb (billion laughs) stops at the budget as CORRUPT.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

import yaml
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError as XmlParseError
from defusedxml.ElementTree import fromstring
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, BlockType, ParseReport

from sbam_workers.ingest.loaders.base import BEAT_EVERY, Heartbeat, Parsed, ParseError, decode

MAX_DEPTH = 100
MAX_NODES = 1_000_000  # values and containers visited, aliases expanded: a YAML bomb is far above
MAX_VALUE_CHARS = 2_000  # a longer value is cut in the index; `doc.md` is an index, not a copy

_PLAIN_KEY = re.compile(r"^[A-Za-z0-9_\-]+$")  # `201` and `post` are plain keys; `a b` is quoted


def load_json(data: bytes, beat: Heartbeat) -> Parsed:
    text = decode(data)

    def refuse(constant: str) -> Any:
        raise ValueError(f"{constant} is not valid JSON")

    try:
        value = json.loads(text, parse_constant=refuse)
    except RecursionError:
        raise ParseError(ErrorCode.PARSE_ERROR, "the JSON is nested too deeply") from None
    except ValueError:
        raise ParseError(ErrorCode.CORRUPT, "the file is not valid JSON") from None
    return _index(value, beat)


def load_yaml(data: bytes, beat: Heartbeat) -> Parsed:
    text = decode(data)
    try:
        value = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError, ValueError):
        raise ParseError(ErrorCode.CORRUPT, "the file is not valid YAML") from None
    return _index(value, beat)


def load_xml(data: bytes, beat: Heartbeat) -> Parsed:
    try:
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, XmlParseError, ValueError, RecursionError):
        raise ParseError(ErrorCode.CORRUPT, "the file is not valid XML") from None
    return _index({root.tag: _xml_value(root, 1)}, beat)


def _xml_value(element: Any, depth: int) -> Any:
    """An element as plain data: attributes `@name`, text `#text`, repeated children as lists."""
    if depth > MAX_DEPTH:
        raise ParseError(ErrorCode.PARSE_ERROR, "the XML is nested too deeply")
    out: dict[str, Any] = {f"@{k}": v for k, v in element.attrib.items()}
    text = (element.text or "").strip()
    children: dict[str, list[Any]] = {}
    for child in element:
        children.setdefault(str(child.tag), []).append(_xml_value(child, depth + 1))
    for tag, items in children.items():
        out[tag] = items[0] if len(items) == 1 else items
    if text:
        if not out:
            return text
        out["#text"] = text
    return out if out else ""


def _index(value: Any, beat: Heartbeat) -> Parsed:
    blocks: list[Block] = []
    lines: list[str] = []
    if value is None:  # an empty YAML file, or JSON `null`
        raise ParseError(ErrorCode.EMPTY, "the file has no values")
    for count, (path, leaf) in enumerate(_leaves(value), start=1):
        if count % BEAT_EVERY == 0:
            beat()
        line = f"{path}: {leaf}"
        lines.append(line)
        blocks.append(Block(type=BlockType.PARAGRAPH, text=line, line=len(lines)))
    if not blocks:
        raise ParseError(ErrorCode.EMPTY, "the file has no values")
    return Parsed(blocks=blocks, markdown="\n".join(lines) + "\n", report=ParseReport())


def _leaves(value: Any) -> Iterator[tuple[str, str]]:
    """Every scalar with its key path, depth first. Iterative, so depth cannot exhaust the stack;
    the node budget counts a shared (aliased) node every time it is reached."""
    nodes = 0
    stack: list[tuple[str, Any, int]] = [("", value, 1)]
    while stack:
        path, item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise ParseError(ErrorCode.CORRUPT, "the file expands to too many values")
        if depth > MAX_DEPTH:
            raise ParseError(ErrorCode.PARSE_ERROR, "the file is nested too deeply")
        if isinstance(item, dict):
            for key, child in reversed(list(item.items())):
                stack.append((_join(path, str(key)), child, depth + 1))
        elif isinstance(item, list):
            for index in range(len(item) - 1, -1, -1):
                stack.append((f"{path}[{index}]", item[index], depth + 1))
        else:
            yield path or "$", _scalar(item)


def _join(path: str, key: str) -> str:
    key = key if _PLAIN_KEY.match(key) or key.startswith(("/", "@", "#")) else json.dumps(key)
    return f"{path}.{key}" if path else key


def _scalar(item: Any) -> str:
    if item is None:
        return "null"
    if isinstance(item, bool):
        return "true" if item else "false"
    text = str(item)
    if "\n" in text or "\r" in text:
        text = json.dumps(text, ensure_ascii=False)  # one value, one line
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "..."
