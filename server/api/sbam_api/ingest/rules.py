"""Upload limits and the extension allowlist (TD §5.2).

`ALLOWED_EXTENSIONS` comes from the Filetype configuration (E3.7): the extensions of the types with
an MVP loader (`sbam_api.filetypes.config`).
"""

from datetime import timedelta
from typing import Final

from sbam_api.filetypes.config import allowlist

ALLOWED_EXTENSIONS: Final = allowlist()

STAGING: Final = "uploads"  # tusd's -upload-dir under STORAGE_ROOT (compose/compose.yaml)

# A started upload keeps its slot this long, so a large file can resume after its token expired.
UPLOAD_HOLD: Final = timedelta(hours=24)
# An abandoned upload's row is kept this long after its hold ended, then deleted (E4.12).
ABANDONED_ROW_RETENTION: Final = timedelta(days=7)
# A staged file this much older than its hold, with no reservation, is reported as an orphan.
ORPHAN_GRACE: Final = timedelta(days=1)


def extension(name: str) -> str:
    """Lower-case extension without the dot; `.tar.gz` counts as one. Empty when there is none."""
    lowered = name.lower()
    if lowered.endswith(".tar.gz"):
        return "tar.gz"
    return lowered.rpartition(".")[2] if "." in lowered.strip(".") else ""


def is_allowed(name: str) -> bool:
    return extension(name) in ALLOWED_EXTENSIONS


def is_downloadable(state: str, scan_status: str | None) -> bool:
    """Whether an FDE may be given the original file. A quarantined (infected, REJECTED) or
    not-yet-clean file never is; the download endpoint answers 404 for it (E4.3-T07)."""
    return scan_status == "CLEAN" and state not in {"REJECTED", "DELETED", "PURGED"}
