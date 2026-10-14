"""Upload limits and the extension allowlist (TD §5.2).

ponytail: `ALLOWED_EXTENSIONS` is the TD §5.2 MVP set, written out. #60 (E3.7) derives it from the
Filetype configuration (`mvp_enabled` loaders); replace this constant with that then.
"""

from datetime import timedelta
from typing import Final

ALLOWED_EXTENSIONS: Final = frozenset(
    {
        "md",
        "txt",
        "eml",
        "msg",
        "json",
        "xml",
        "yaml",
        "yml",
        "py",
        "sql",
        "js",
        "go",
        "cpp",
        "csv",
        "xlsx",
        "docx",
        "pdf",
        "zip",
        "tar.gz",
        "png",
        "jpg",
        "jpeg",
        "tiff",
    }
)

# A started upload keeps its slot this long, so a large file can resume after its token expired.
UPLOAD_HOLD: Final = timedelta(hours=24)


def extension(name: str) -> str:
    """Lower-case extension without the dot; `.tar.gz` counts as one. Empty when there is none."""
    lowered = name.lower()
    if lowered.endswith(".tar.gz"):
        return "tar.gz"
    return lowered.rpartition(".")[2] if "." in lowered.strip(".") else ""


def is_allowed(name: str) -> bool:
    return extension(name) in ALLOWED_EXTENSIONS
