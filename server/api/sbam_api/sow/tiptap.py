"""`SowDocument` <-> the editor's TipTap JSON (E6.3, ADR 0019; shape in `sbam_contracts.sow_api`).

Both directions are driven by the `SowDocument` fields, so a field added to the contract appears
in the editor without code here. `from_tiptap` is strict about the template (nine sections, in
order, with their titles, known fields only) and lenient about formatting: marks are dropped,
empty paragraphs are ignored, any block's text is read. Its errors carry a dotted path
(`sections.scope`, `features.0.bound.max_attempts`), and the result is then validated as a
`SowDocument`, whose errors are reported with the same kind of path.
"""

import json
import types
from collections.abc import Iterator
from typing import Annotated, Any, Final, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError
from sbam_contracts.api import FieldError
from sbam_contracts.sow import AnyCitation, SowDocument
from sbam_contracts.sow_api import SECTION_TITLES

SECTION: Final = "sowSection"
FIELD: Final = "sowField"
ITEM: Final = "sowItem"
RECORD: Final = "sowRecord"
CITATIONS: Final = "sowCitations"
CITATION: Final = "citation"
OKF: Final = "okf"
FDE: Final = "fde"  # a citation the FDE wrote themselves (no okf_ref_id)
ITEM_SECTIONS: Final = {"modules": ("id",), "features": ("id", "module_id")}
LISTS: Final = ("bulletList", "orderedList")

Node = dict[str, Any]


class TipTapInvalid(Exception):  # noqa: N818
    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__(f"{len(errors)} problems")
        self.errors = errors


# --- field kinds ----------------------------------------------------------------------------------


def _unwrap(annotation: Any) -> tuple[Any, bool]:
    """The type under Annotated / Optional, and whether None is allowed."""
    optional = False
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, types.UnionType):
            args = [a for a in get_args(annotation) if a is not type(None)]
            optional = optional or len(args) < len(get_args(annotation))
            annotation = args[0]
        else:
            return annotation, optional


def _kind(annotation: Any) -> tuple[str, Any, bool]:
    """('text' | 'int' | 'texts' | 'object' | 'records', model or None, optional)."""
    inner, optional = _unwrap(annotation)
    if get_origin(inner) is list:
        item, _ = _unwrap(get_args(inner)[0])
        if isinstance(item, type) and issubclass(item, BaseModel):
            return "records", item, optional
        return "texts", None, optional
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return "object", inner, optional
    if inner is int:
        return "int", None, optional
    return "text", None, optional


def _fields(model: type[BaseModel], skip: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        name: info.annotation
        for name, info in model.model_fields.items()
        if name != "citations" and name not in skip
    }


# --- SowDocument -> TipTap ------------------------------------------------------------------------


def _text(text: str) -> list[Node]:
    return [{"type": "text", "text": text}] if text else []


def _paragraphs(value: str) -> list[Node]:
    return [{"type": "paragraph", "content": _text(line)} for line in value.split("\n")]


def _heading(level: int, text: str) -> Node:
    return {"type": "heading", "attrs": {"level": level}, "content": _text(text)}


def _value(annotation: Any, value: Any) -> list[Node]:
    kind, model, _ = _kind(annotation)
    if value is None:
        return []
    if kind == "texts":
        items = [{"type": "listItem", "content": _paragraphs(str(v))} for v in value]
        return [{"type": "bulletList", "content": items}] if items else []
    if kind == "object":
        return _field_nodes(model, value)
    if kind == "records":
        return [{"type": RECORD, "content": _field_nodes(model, v)} for v in value]
    return _paragraphs(str(value))


def _field_nodes(
    model: type[BaseModel], data: dict[str, Any], skip: tuple[str, ...] = ()
) -> list[Node]:
    return [
        {"type": FIELD, "attrs": {"field": name}, "content": _value(annotation, data.get(name))}
        for name, annotation in _fields(model, skip).items()
    ]


def _citation_attrs(c: dict[str, Any]) -> dict[str, Any]:
    if c.get("kind") == FDE:
        attrs: dict[str, Any] = {"kind": FDE, "author": c["author"], "date": str(c["date"])}
        if c.get("note") is not None:
            attrs["note"] = c["note"]
        return attrs
    return {
        "okf_ref_id": c["okf_ref_id"],
        "document_version_id": str(c["document_version_id"]),
        "location": dict(c["location"]),
    }


def _citations(cites: list[dict[str, Any]]) -> Node:
    return {
        "type": CITATIONS,
        "content": [{"type": CITATION, "attrs": _citation_attrs(c)} for c in cites],
    }


def _section_model(section: str) -> type[BaseModel]:
    _, model, _ = _kind(SowDocument.model_fields[section].annotation)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):  # pragma: no cover
        raise TypeError(f"section {section} is not a model")
    return model


def to_tiptap(structured: dict[str, Any]) -> Node:
    """The editor document of a `SowDocument` (as `model_dump(mode="json")`)."""
    doc = SowDocument.model_validate(structured).model_dump(mode="json")
    sections: list[Node] = []
    for section, title in SECTION_TITLES.items():
        model = _section_model(section)
        body: list[Node] = [_heading(2, title)]
        if section in ITEM_SECTIONS:
            keys = ITEM_SECTIONS[section]
            for item in doc[section]:
                body.append(
                    {
                        "type": ITEM,
                        "attrs": {k: item[k] for k in keys},
                        "content": [
                            _heading(3, item["name"]),
                            *_field_nodes(model, item, (*keys, "name")),
                            _citations(item["citations"]),
                        ],
                    }
                )
        else:
            body += [*_field_nodes(model, doc[section]), _citations(doc[section]["citations"])]
        sections.append({"type": SECTION, "attrs": {"section_id": section}, "content": body})
    return {"type": "doc", "content": sections}


# --- TipTap -> SowDocument ------------------------------------------------------------------------


class _Reader:
    def __init__(self) -> None:
        self.errors: list[FieldError] = []

    def fail(self, path: str, message: str) -> None:
        self.errors.append(FieldError(path=path, message=message))

    @staticmethod
    def children(node: Any) -> list[Node]:
        content = node.get("content") if isinstance(node, dict) else None
        return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []

    @staticmethod
    def attrs(node: Node) -> dict[str, Any]:
        attrs = node.get("attrs")
        return attrs if isinstance(attrs, dict) else {}

    def text(self, node: Node) -> str:
        """The node's text: inline text joined, hard breaks as line breaks, blocks one per line."""
        kind = node.get("type")
        if kind == "text":
            return str(node.get("text") or "")
        if kind == "hardBreak":
            return "\n"
        parts = [self.text(c) for c in self.children(node)]
        if kind in ("paragraph", "heading"):
            return "".join(parts)
        return "\n".join(p for p in parts if p)

    def lines(self, nodes: list[Node]) -> str:
        return "\n".join(t for t in (self.text(n) for n in nodes) if t.strip())

    def value(self, annotation: Any, nodes: list[Node], path: str) -> Any:
        kind, model, optional = _kind(annotation)
        if kind == "texts":
            out: list[str] = []
            for block in nodes:
                if block.get("type") in LISTS:
                    out += [t for t in (self.text(i).strip() for i in self.children(block)) if t]
                elif self.text(block).strip():
                    out.append(self.text(block).strip())
            return out
        if kind == "object":
            return self.fields(model, nodes, path)
        if kind == "records":
            records = [n for n in nodes if n.get("type") == RECORD]
            return [
                self.fields(model, self.children(r), f"{path}.{i}") for i, r in enumerate(records)
            ]
        text = self.lines(nodes)
        if not text and optional:
            return None
        if kind == "int":
            try:
                return int(text.strip())
            except ValueError:
                self.fail(path, "must be a whole number")
                return None
        return text

    def fields(
        self, model: type[BaseModel], nodes: list[Node], path: str, skip: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        known = _fields(model, skip)
        out: dict[str, Any] = {}
        for node in nodes:
            if node.get("type") != FIELD:
                continue
            name = self.attrs(node).get("field")
            where = f"{path}.{name}" if isinstance(name, str) else path
            if name not in known:
                self.fail(where, "is not a field of this section")
            elif name in out:
                self.fail(where, "appears twice")
            else:
                out[name] = self.value(known[name], self.children(node), where)
        return out

    def citations(self, nodes: list[Node], path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for block in (n for n in nodes if n.get("type") == CITATIONS):
            for node in self.children(block):
                if node.get("type") == CITATION:
                    a = self.attrs(node)
                    if a.get("kind") == FDE:
                        fde = {k: a.get(k) for k in ("kind", "author", "date", "note")}
                        out.append({k: v for k, v in fde.items() if v is not None})
                        continue
                    out.append(
                        {
                            "okf_ref_id": a.get("okf_ref_id"),
                            "document_version_id": a.get("document_version_id"),
                            "location": a.get("location"),
                        }
                    )
        if not out:
            self.fail(f"{path}.citations", "needs at least one citation")
        return out

    def heading(self, nodes: list[Node], level: int) -> tuple[str | None, list[Node]]:
        if nodes and nodes[0].get("type") == "heading":
            return self.text(nodes[0]).strip(), nodes[1:]
        return None, nodes

    def section(self, section: str, node: Node) -> Any:
        nodes = self.children(node)
        title, nodes = self.heading(nodes, 2)
        if title != SECTION_TITLES[section]:
            self.fail(f"sections.{section}.heading", f"must stay '{SECTION_TITLES[section]}'")
        model = _section_model(section)
        if section not in ITEM_SECTIONS:
            data = self.fields(model, nodes, section)
            data["citations"] = self.citations(nodes, section)
            return data
        keys = ITEM_SECTIONS[section]
        items = []
        for i, item in enumerate(n for n in nodes if n.get("type") == ITEM):
            path, inner = f"{section}.{i}", self.children(item)
            name, inner = self.heading(inner, 3)
            data = {k: self.attrs(item).get(k) for k in keys}
            data["name"] = name or ""
            data |= self.fields(model, inner, path, (*keys, "name"))
            data["citations"] = self.citations(inner, path)
            items.append(data)
        return items

    def document(self, doc: Any) -> dict[str, Any]:
        if not isinstance(doc, dict) or doc.get("type") != "doc":
            self.fail("content_tiptap", "must be a TipTap document")
            return {}
        found: dict[str, Node] = {}
        order: list[str] = []
        for node in self.children(doc):
            if node.get("type") != SECTION:
                if self.text(node).strip():
                    self.fail("sections", "text outside the SOW sections")
                continue
            section = self.attrs(node).get("section_id")
            if section not in SECTION_TITLES:
                self.fail("sections", f"unknown section {str(section)[:40]!r}")
            elif section in found:
                self.fail(f"sections.{section}", "appears twice")
            else:
                found[section] = node
                order.append(section)
        for section in SECTION_TITLES:
            if section not in found:
                self.fail(f"sections.{section}", "is missing (template sections cannot be removed)")
        if not self.errors and tuple(order) != tuple(SECTION_TITLES):
            self.fail("sections", "must stay in the template order")
        return {s: self.section(s, found[s]) for s in SECTION_TITLES if s in found}


def _loc(loc: tuple[int | str, ...]) -> str:
    """Dotted path of a validation error, without the citation union's `okf` / `fde` tag."""
    parts = [
        str(part)
        for i, part in enumerate(loc)
        if not (part in (OKF, FDE) and i and isinstance(loc[i - 1], int))
    ]
    return ".".join(parts) or "content_tiptap"


def from_tiptap(doc: Any) -> SowDocument:
    """The `SowDocument` an editor document holds. Raises `TipTapInvalid` with every problem."""
    reader = _Reader()
    data = reader.document(doc)
    if reader.errors:
        raise TipTapInvalid(reader.errors)
    try:
        return SowDocument.model_validate(data)
    except ValidationError as exc:
        raise TipTapInvalid(
            [FieldError(path=_loc(e["loc"]), message=e["type"]) for e in exc.errors()]
        ) from None


def _statement(owner: Node) -> str:
    """What an owner (a section or an item) says, without its citations, whitespace folded."""
    reader = _Reader()
    kept = [c for c in reader.children(owner) if c.get("type") != CITATIONS]
    return " ".join(reader.text({"type": "doc", "content": kept}).split())


def _owner_key(section: str, owner: Node) -> str:
    """Names one statement: its section, its item ids and its text. A citation's stamp is only
    valid for the statement it was written on, so editing the text or moving the citation to
    another statement voids it."""
    attrs = _Reader.attrs(owner) if owner.get("type") == ITEM else {}
    ids = [attrs.get(k) for k in ITEM_SECTIONS.get(section, ())]
    return json.dumps([section, ids, _statement(owner)], ensure_ascii=False)


def _fde_citations(content: Any) -> Iterator[tuple[str, Node]]:
    """(statement key, citation node) for every FDE-authored citation in an editor document."""
    reader = _Reader()
    for node in reader.children(content):
        section = reader.attrs(node).get("section_id")
        if node.get("type") != SECTION or not isinstance(section, str):
            continue
        owners = [n for n in reader.children(node) if n.get("type") == ITEM] or [node]
        for owner in owners:
            key = _owner_key(section, owner)
            for block in (n for n in reader.children(owner) if n.get("type") == CITATIONS):
                for cite in reader.children(block):
                    if cite.get("type") == CITATION and reader.attrs(cite).get("kind") == FDE:
                        yield key, cite


def fde_stamps(content: Any) -> set[tuple[str, Any, Any, Any]]:
    """(statement, author, date, note) of every FDE-authored citation a saved document holds."""
    return {
        (key, a.get("author"), a.get("date"), a.get("note"))
        for key, cite in _fde_citations(content)
        for a in [_Reader.attrs(cite)]
    }


def stamp_fde(content: Any, seen: set[tuple[str, Any, Any, Any]], author: str, today: str) -> None:
    """Sign every FDE-authored citation as `author`, today, unless the saved version already holds
    exactly this citation on exactly this statement.

    An editor cannot write someone else's name or an older date, and cannot copy a colleague's
    stamp (visible on any read) onto a statement of their own: the stamp is keyed to the statement
    it was made on, so anywhere else, or after the text changed, it is replaced.
    """
    for key, cite in _fde_citations(content):
        attrs = cite.setdefault("attrs", {})
        if (key, attrs.get("author"), attrs.get("date"), attrs.get("note")) not in seen:
            attrs["author"], attrs["date"] = author, today


def citations_of(doc: SowDocument) -> list[tuple[str, AnyCitation]]:
    """Every citation with the path of what it supports (`scope`, `features.0`, ...)."""
    out: list[tuple[str, AnyCitation]] = []
    for section in SECTION_TITLES:
        value = getattr(doc, section)
        if isinstance(value, list):
            out += [(f"{section}.{i}", c) for i, item in enumerate(value) for c in item.citations]
        else:
            out += [(section, c) for c in value.citations]
    return out
