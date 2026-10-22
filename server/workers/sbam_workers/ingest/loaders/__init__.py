"""The loaders of the parse step (E4.5, TD §5.2): one function per detected type.

A loader gets the whole file (at most 15 MB, the upload limit), reads it without running anything
in it (no macro, no formula, no link followed, no network) and returns a `Parsed`: the blocks and
tables of `doc.json`, the text of `doc.md` and the report. A file it cannot read raises
`ParseError` with the code the version is failed with.

`loader_for(detected_type)` is the registry. A type with no loader yet gives None: the parse step
leaves such a version in PARSING, as before, until its loader lands (PDF, DOCX and XLSX: E4.5 part
2; e-mail, structured, source code, archives: later issues).
"""

from sbam_workers.ingest.loaders.base import Heartbeat, Loader, Parsed, ParseError, no_heartbeat
from sbam_workers.ingest.loaders.tabular import load_csv
from sbam_workers.ingest.loaders.text import load_markdown, load_text

LOADERS: dict[str, Loader] = {
    "md": load_markdown,
    "txt": load_text,
    "csv": load_csv,
}


def loader_for(detected_type: str | None) -> Loader | None:
    return LOADERS.get(detected_type or "")


__all__ = [
    "LOADERS",
    "Heartbeat",
    "Loader",
    "ParseError",
    "Parsed",
    "loader_for",
    "no_heartbeat",
]
