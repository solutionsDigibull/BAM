"""The export's one source (E6.4): Pandoc Markdown built from a version's `structured` SowDocument.

Never from the editor's TipTap: raw HTML, marks and images typed into the editor are not part of
`structured`, so they cannot reach a renderer. Every piece of SOW text is escaped (markup
characters backslash-escaped, so `<img src=...>`, `[link](...)` or `**` stay literal text), and the
renderers also read the Markdown with raw HTML and TeX turned off.

Layout: a cover (project, version, status, sha256, generated_at; a SUPERSEDED version says so),
then the nine sections as `## ` headings in template order, then the citations as numbered
footnotes, one per citation: the document's title and the location.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sbam_contracts.sow_api import SECTION_TITLES

# Pandoc's Markdown with every way to smuggle markup in turned off (used by the renderers), and
# without "smart" punctuation, so the text is rendered as written.
MD_FORMAT: Final = (
    "markdown-raw_html-raw_tex-raw_attribute-tex_math_dollars-tex_math_single_backslash-smart"
)
# Characters that can start markup anywhere (emphasis, links, images, HTML, code, tables, maths,
# attributes, entities, footnotes, autolinks), and markers that only matter at the start of a line.
_MARKUP = re.compile(r"([\\`*_\[\]<>#|!~^$&{}@])")
_LINE_START = re.compile(r"^([-+=:])")
_NUMBERED = re.compile(r"^(\d+)([.)])")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
ITEM_SECTIONS: Final = ("modules", "features")
SKIP: Final = frozenset({"citations", "id", "module_id", "name"})


@dataclass(frozen=True)
class ExportMeta:
    project: str
    version: int
    status: str
    sha256: str
    generated_at: datetime


def escape(text: str) -> str:
    """Literal text in Pandoc Markdown: markup escaped, line breaks kept as hard breaks."""
    lines = [_escape_line(_CONTROL.sub("", line).strip()) for line in text.split("\n")]
    return "\\\n".join(line for line in lines if line)


def _escape_line(line: str) -> str:
    line = _MARKUP.sub(r"\\\1", line)
    line = _LINE_START.sub(r"\\\1", line)
    return _NUMBERED.sub(r"\1\\\2", line)


def label(field: str) -> str:
    return field.replace("_", " ").capitalize()


def where(location: Mapping[str, Any]) -> str:
    keys = ("page", "line", "sheet", "cell")
    parts = [f"{k} {location[k]}" for k in keys if location.get(k) is not None]
    return ", ".join(parts) or "whole document"


class _Notes:
    def __init__(self, titles: Mapping[UUID, str]) -> None:
        self.titles = titles
        self.lines: list[str] = []

    def refs(self, citations: list[dict[str, Any]]) -> str:
        marks = []
        for c in citations:
            n = len(self.lines) + 1
            doc = UUID(str(c["document_version_id"]))
            title = self.titles.get(doc) or f"document {str(doc)[:8]}"
            self.lines.append(f"[^{n}]: {escape(title)}, {escape(where(c.get('location') or {}))}")
            marks.append(f"[^{n}]")
        return "Sources: " + " ".join(marks) if marks else ""


def _value(value: Any) -> list[str]:
    """Markdown lines for one field value (text, number, list, nested object, list of objects)."""
    if value is None:
        return ["None identified"]
    if isinstance(value, dict):
        return [f"- {label(k)}: {_inline(v)}" for k, v in value.items()]
    if isinstance(value, list):
        if not value:
            return ["None identified"]
        return [f"- {_inline(v)}" for v in value]
    return [escape(str(value))]


def _inline(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{label(k)}: {_inline(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(_inline(v) for v in value) or "none"
    return escape(str(value)).replace("\\\n", " ")


def _fields(data: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for field, value in data.items():
        if field in SKIP:
            continue
        out += [f"**{escape(label(field))}**", "", *_value(value), ""]
    return out


def build_markdown(
    structured: Mapping[str, Any], meta: ExportMeta, titles: Mapping[UUID, str]
) -> str:
    notes = _Notes(titles)
    lines = [
        "# Statement of Work",
        "",
        f"**Project:** {escape(meta.project)}\\",
        f"**SOW version:** v{meta.version}\\",
        f"**Status:** {escape(meta.status)}\\",
        f"**SHA-256:** `{meta.sha256}`\\",
        f"**Generated at:** {meta.generated_at.isoformat(timespec='seconds')}",
        "",
    ]
    if meta.status == "SUPERSEDED":
        lines += ["**SUPERSEDED**: a newer version of this SOW was approved.", ""]
    for number, (section, title) in enumerate(SECTION_TITLES.items(), start=1):
        lines += [f"## {number}. {title}", ""]
        value = structured.get(section)
        if section in ITEM_SECTIONS:
            for item in value or []:
                head = f"{escape(str(item.get('name', '')))} ({escape(str(item.get('id', '')))})"
                lines += [f"### {head}", ""]
                if section == "features":
                    lines += [f"*Module:* {escape(str(item.get('module_id', '')))}", ""]
                lines += _fields(item)
                lines += [notes.refs(item.get("citations") or []), ""]
        else:
            lines += _fields(value or {})
            lines += [notes.refs((value or {}).get("citations") or []), ""]
    lines += notes.lines
    return "\n".join(lines).rstrip() + "\n"


def citation_count(structured: Mapping[str, Any]) -> int:
    total = 0
    for section in SECTION_TITLES:
        value = structured.get(section)
        items = value if isinstance(value, list) else [value or {}]
        total += sum(len(i.get("citations") or []) for i in items)
    return total
