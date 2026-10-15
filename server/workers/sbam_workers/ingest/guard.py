"""The scan guard (E4.3-T06): nothing reads, parses, OCRs or sends an upload to an LLM until its
scan verdict is CLEAN. Every parse, OCR and loader activity calls `require_clean` first.
"""

from sbam_api.ingest.models import DocumentVersion
from sbam_contracts.documents import ScanStatus
from temporalio.exceptions import ApplicationError


def not_scanned() -> ApplicationError:
    return ApplicationError(
        "The document version has not been scanned clean",
        type="NotScanned",
        non_retryable=True,  # retrying cannot make an unscanned file clean
    )


def require_clean(version: DocumentVersion) -> None:
    """Raise `NotScanned` (non-retryable) unless the version's scan status is CLEAN."""
    if version.scan_status != ScanStatus.CLEAN.value:
        raise not_scanned()
