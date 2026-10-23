"""PDF with a text layer (E4.5): one paragraph block per page, with its page number.

Read with pypdf, which runs nothing in the file: no JavaScript, no launch action, no link followed.
A page with no text (a scan) is not an error: its number goes into `ocr_needed_pages` for the OCR
step (E4.8), and a PDF with no text on any page is parsed with no blocks for the same reason.
A password-protected PDF is ENCRYPTED, and no password is ever asked for or tried (a PDF that
opens with an empty password is not protected from reading, and is read). More than 2000 pages is
refused. `doc.md` marks each page with `<!-- page N -->`.
"""

import io
import logging
import re

from pypdf import PdfReader
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import MAX_PDF_PAGES, Block, BlockType, ParseReport

from sbam_workers.ingest.loaders.base import Heartbeat, Parsed, ParseError

log = logging.getLogger("sbam_workers.ingest.loaders.pdf")
BEAT_PAGES = 10
_BLANKS = re.compile(r"\n{3,}")


def load_pdf(data: bytes, beat: Heartbeat) -> Parsed:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ParseError(ErrorCode.ENCRYPTED, "the PDF is password-protected")
        pages = list(reader.pages) if len(reader.pages) <= MAX_PDF_PAGES else []
        count = len(reader.pages)
    except ParseError:
        raise
    except Exception as exc:  # pypdf raises many types for a file that is not a PDF it can read
        log.info("unreadable PDF", extra={"exc_type": type(exc).__name__})
        raise ParseError(ErrorCode.CORRUPT, "the file is not a readable PDF") from None
    if count == 0:
        raise ParseError(ErrorCode.EMPTY, "the PDF has no pages")
    if count > MAX_PDF_PAGES:
        raise ParseError(ErrorCode.PARSE_ERROR, f"the PDF has more than {MAX_PDF_PAGES} pages")

    blocks: list[Block] = []
    markdown: list[str] = []
    ocr_needed: list[int] = []
    warnings: list[str] = []
    for number, page in enumerate(pages, start=1):
        if number % BEAT_PAGES == 0:
            beat()
        try:
            text = _clean(page.extract_text() or "")
        except Exception as exc:  # one bad page does not fail the document
            log.info("unreadable PDF page", extra={"exc_type": type(exc).__name__})
            text = ""
            warnings.append(f"page {number} could not be read")
        if not text:
            ocr_needed.append(number)
            continue
        blocks.append(Block(type=BlockType.PARAGRAPH, text=text, page=number))
        markdown.append(f"<!-- page {number} -->\n\n{text}\n")
    report = ParseReport(pages=count, warnings=warnings, ocr_needed_pages=ocr_needed)
    return Parsed(blocks=blocks, markdown="\n".join(markdown), report=report)


def _clean(text: str) -> str:
    """No NUL or other control characters (JSON and Markdown must carry the text), no long runs of
    blank lines."""
    kept = "".join(c for c in text if c in "\n\t" or c.isprintable())
    return _BLANKS.sub("\n\n", kept.replace("\r\n", "\n").replace("\r", "\n")).strip()
