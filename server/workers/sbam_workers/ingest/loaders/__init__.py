"""The loaders of the parse step (E4.5, TD §5.2): one function per detected type.

A loader gets the whole file (at most 15 MB, the upload limit), reads it without running anything
in it (no macro, no formula, no link followed, no network) and returns a `Parsed`: the blocks and
tables of `doc.json`, the text of `doc.md` and the report. A file it cannot read raises
`ParseError` with the code the version is failed with.

`loader_for(detected_type)` is the registry. A type with no loader yet gives None: the parse step
leaves such a version in PARSING, as before, until its loader lands (archives: a later issue).
"""

from sbam_workers.ingest.loaders.base import (
    Attachment,
    Heartbeat,
    Loader,
    Parsed,
    ParseError,
    no_heartbeat,
)
from sbam_workers.ingest.loaders.code import (
    load_cpp,
    load_go,
    load_javascript,
    load_python,
    load_sql,
)
from sbam_workers.ingest.loaders.email import load_eml, load_msg
from sbam_workers.ingest.loaders.image import load_image
from sbam_workers.ingest.loaders.pdf import load_pdf
from sbam_workers.ingest.loaders.structured import load_json, load_xml, load_yaml
from sbam_workers.ingest.loaders.tabular import load_csv
from sbam_workers.ingest.loaders.text import load_markdown, load_text
from sbam_workers.ingest.loaders.word import load_docx
from sbam_workers.ingest.loaders.xlsx import load_xlsx

LOADERS: dict[str, Loader] = {
    "md": load_markdown,
    "txt": load_text,
    "csv": load_csv,
    "pdf": load_pdf,
    "docx": load_docx,
    "xlsx": load_xlsx,
    "png": load_image,  # images have no text layer: every frame goes to OCR (E4.8)
    "jpg": load_image,
    "jpeg": load_image,
    "tiff": load_image,
    "eml": load_eml,
    "msg": load_msg,
    "json": load_json,
    "xml": load_xml,
    "yaml": load_yaml,
    "yml": load_yaml,
    "py": load_python,
    "sql": load_sql,
    "js": load_javascript,
    "go": load_go,
    "cpp": load_cpp,
}


def loader_for(detected_type: str | None) -> Loader | None:
    return LOADERS.get(detected_type or "")


__all__ = [
    "LOADERS",
    "Attachment",
    "Heartbeat",
    "Loader",
    "ParseError",
    "Parsed",
    "loader_for",
    "no_heartbeat",
]
