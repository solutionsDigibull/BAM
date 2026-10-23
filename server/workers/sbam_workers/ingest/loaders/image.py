"""Images: PNG, JPEG and TIFF (E4.8). There is no text layer, so every frame is a page for OCR.

The loader only opens the file to check it is a readable image and to count its frames (a TIFF may
have many). The text comes from the vision model in the OCR step, which keeps the version PARSING
until then (`ParseReport.ocr_needed_pages` lists the frames). More than 200 frames is refused.
"""

from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import MAX_OCR_PAGES, ParseReport

from sbam_workers.ingest.loaders.base import Heartbeat, Parsed, ParseError


def load_image(data: bytes, beat: Heartbeat) -> Parsed:
    # Imported here: `rasterize` imports `loaders.base`, which loads this package (a cycle).
    from sbam_workers.ingest.rasterize import ImageSource

    source = ImageSource(data)  # CORRUPT when it is not a readable image
    try:
        frames = source.pages
    finally:
        source.close()
    if frames > MAX_OCR_PAGES:
        raise ParseError(ErrorCode.PARSE_ERROR, f"the image has more than {MAX_OCR_PAGES} frames")
    report = ParseReport(pages=frames, ocr_needed_pages=list(range(1, frames + 1)))
    return Parsed(blocks=[], markdown="", report=report)
