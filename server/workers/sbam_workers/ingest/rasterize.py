"""Page images for the vision model (E4.8, TD §4 T5): a PDF page or an image frame as one PNG.

Every image that leaves for the model is a PNG of at most 3000 px on the longest side and 5 MB,
rendered at 200 DPI (a PDF) or scaled down to fit (an image). Nothing in the file is run: pdfium
draws the page, Pillow decodes the image, and the page's own scripts and links are never touched.
An image is also upright (the EXIF orientation is applied), RGB, and refused above 100 megapixels
(a decompression bomb), so a rotated phone photo reads the right way up and a crafted file cannot
fill the worker's memory.

`open_source(data, detected_type)` gives a `PageSource`: how many pages it has and `render(n)`
(1-based). One source is used by one thread at a time (pdfium is not thread-safe).
"""

import io
from typing import Final, Protocol

import pypdfium2 as pdfium  # type: ignore[import-untyped]  # ships no type information
from PIL import Image, ImageOps
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import OCR_DPI, OCR_MAX_IMAGE_BYTES, OCR_MAX_SIDE_PX

from sbam_workers.ingest.loaders.base import ParseError

MAX_PIXELS: Final = 100_000_000
SHRINK: Final = 0.8  # each step when a PNG is still over the byte limit
MAX_SHRINKS: Final = 8
IMAGE_TYPES: Final = frozenset({"png", "jpg", "jpeg", "tiff"})

Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow raises DecompressionBombError at twice this


class PageUnreadable(Exception):  # noqa: N818 - the page cannot become a usable image
    """The page image is over the byte limit even after the biggest shrink, or the frame cannot be
    read: the page is unreadable and the document goes on."""


class PageSource(Protocol):
    @property
    def pages(self) -> int: ...

    def render(self, number: int) -> bytes:
        """The page (1-based) as a PNG within the size limits, or `PageUnreadable`."""

    def close(self) -> None: ...


def open_source(data: bytes, detected_type: str) -> PageSource:
    """A PDF or an image file. Anything it cannot open is a `ParseError` (CORRUPT)."""
    if detected_type == "pdf":
        return PdfSource(data)
    if detected_type in IMAGE_TYPES:
        return ImageSource(data)
    raise ParseError(ErrorCode.PARSE_ERROR, f"no page images for type {detected_type!r}")


def fit(image: Image.Image) -> bytes:
    """RGB, at most OCR_MAX_SIDE_PX on the longest side, PNG of at most OCR_MAX_IMAGE_BYTES."""
    rgb = image.convert("RGB")
    longest = max(rgb.size)
    if longest > OCR_MAX_SIDE_PX:
        factor = OCR_MAX_SIDE_PX / longest
        rgb = rgb.resize((max(1, round(rgb.width * factor)), max(1, round(rgb.height * factor))))
    for _ in range(MAX_SHRINKS + 1):
        out = io.BytesIO()
        rgb.save(out, format="PNG", optimize=True)
        if out.tell() <= OCR_MAX_IMAGE_BYTES:
            return out.getvalue()
        rgb = rgb.resize((max(1, round(rgb.width * SHRINK)), max(1, round(rgb.height * SHRINK))))
    raise PageUnreadable("the page image is over the size limit")


class PdfSource:
    def __init__(self, data: bytes) -> None:
        try:
            self._pdf = pdfium.PdfDocument(data)
            self.pages = len(self._pdf)
        except Exception:  # pdfium raises PdfiumError, and others for bad input
            raise ParseError(ErrorCode.CORRUPT, "the PDF cannot be drawn") from None

    def render(self, number: int) -> bytes:
        page = self._pdf[number - 1]
        try:
            width, height = page.get_size()
            scale = OCR_DPI / 72
            longest = max(width, height) * scale
            if longest > OCR_MAX_SIDE_PX:
                scale *= OCR_MAX_SIDE_PX / longest
            return fit(page.render(scale=scale).to_pil())
        finally:
            page.close()

    def close(self) -> None:
        self._pdf.close()


class ImageSource:
    """An image file: one page per frame (a multi-page TIFF has several)."""

    def __init__(self, data: bytes) -> None:
        try:
            self._image = Image.open(io.BytesIO(data))
            self._image.load()  # decodes the first frame: a truncated file fails here
            self.pages = int(getattr(self._image, "n_frames", 1))
        except Image.DecompressionBombError:
            raise ParseError(ErrorCode.PARSE_ERROR, "the image is too large") from None
        except Exception:  # Pillow raises UnidentifiedImageError, OSError, SyntaxError, ...
            raise ParseError(ErrorCode.CORRUPT, "the file is not a readable image") from None

    def render(self, number: int) -> bytes:
        try:
            self._image.seek(number - 1)
            frame = ImageOps.exif_transpose(self._image.copy())
            return fit(frame)
        except (EOFError, OSError, Image.DecompressionBombError):
            raise PageUnreadable("the frame cannot be read") from None

    def close(self) -> None:
        self._image.close()
