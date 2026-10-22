"""Markdown and plain text (E4.5): headings, list items and paragraphs, each with its line.

`line` is the 1-based line of the first line of the block. Markdown headings (`#` to `######`) and
list items (`-`, `*`, `+`, `1.`) get their own blocks and a fenced code block is one paragraph;
plain text has only paragraphs (separated by blank lines). `doc.md` is the text with its line ends
normalised; the tables of a Markdown file stay text inside their paragraph.
"""

import re

from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, BlockType, ParseReport

from sbam_workers.ingest.loaders.base import BEAT_EVERY, Heartbeat, Parsed, ParseError, decode

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+(.*\S)[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")


def load_markdown(data: bytes, beat: Heartbeat) -> Parsed:
    return _load(data, beat, markdown=True)


def load_text(data: bytes, beat: Heartbeat) -> Parsed:
    return _load(data, beat, markdown=False)


def _load(data: bytes, beat: Heartbeat, *, markdown: bool) -> Parsed:
    text = decode(data)
    if not text.strip():
        raise ParseError(ErrorCode.EMPTY, "the file has no text")
    blocks: list[Block] = []
    paragraph: list[str] = []
    start = 0
    fence: str | None = None

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(Block(type=BlockType.PARAGRAPH, text="\n".join(paragraph), line=start))
            paragraph = []

    for number, line in enumerate(text.split("\n"), start=1):
        if number % BEAT_EVERY == 0:
            beat()
        if markdown and fence is not None:  # inside a code fence: everything is paragraph text
            paragraph.append(line)
            if line.strip().startswith(fence):
                fence = None
                flush()
            continue
        if not line.strip():
            flush()
            continue
        if markdown and (opener := _FENCE.match(line)):
            flush()
            fence, start = opener.group(1), number
            paragraph.append(line)
            continue
        if markdown and (heading := _HEADING.match(line)):
            flush()
            blocks.append(
                Block(
                    type=BlockType.HEADING,
                    text=heading.group(2),
                    level=len(heading.group(1)),
                    line=number,
                )
            )
            continue
        if markdown and (item := _ITEM.match(line)):
            flush()
            blocks.append(Block(type=BlockType.LIST_ITEM, text=item.group(1), line=number))
            continue
        if not paragraph:
            start = number
        paragraph.append(line)
    flush()
    if not blocks:  # nothing but blank space and empty fences
        raise ParseError(ErrorCode.EMPTY, "the file has no text")
    out = text if markdown else "\n\n".join(b.text for b in blocks)
    return Parsed(blocks=blocks, markdown=out.rstrip("\n") + "\n", report=ParseReport())
