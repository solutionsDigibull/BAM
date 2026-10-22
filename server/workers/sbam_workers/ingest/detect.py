"""Type detection (E4.4, TD §5.2): what a CLEAN file really is, compared with what its name says.

Pure functions, no I/O: `detect(name, head, zip_names)` takes the file name, the first
`HEAD_BYTES` of the file and, for ZIP containers, a callable that lists the archive's entries. It
returns `Detected` (the Filetype row and loader the file goes to) or `Rejected` (an `ErrorCode`).

Rules, in this order:
  1. A binary executable (Windows PE, ELF, Mach-O) is always `TYPE_MISMATCH`, whatever it is named.
  2. An extension without an MVP-enabled Filetype row is `UNSUPPORTED_TYPE` (the allowlist is the
     Filetype configuration of E3.7, never a list kept here).
  3. The content wins over the extension. A file whose signature is not the one the extension
     promises is `TYPE_MISMATCH`; it is never "fixed up" to the type it looks like.
  4. A text format (the extension decides among md, txt, csv, json, yaml, py, ...) must be UTF-8
     (a BOM is fine) with no NUL byte in the part we see: otherwise `INVALID_ENCODING`.
  5. `.docx` and `.xlsx` are both ZIP, so the archive's own entries decide (`word/` vs `xl/`).

No dependency: the MVP has about 20 extensions and a dozen signatures, so a table is smaller and
easier to audit than libmagic (which Windows laptops do not have) or puremagic.

Only the head is inspected, so a file that goes bad after 8 KB passes here and is the loader's to
reject (E4.5). Nothing here logs file names or content.
"""

import codecs
import gzip
import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sbam_api.filetypes.config import Filetype, enabled_rows
from sbam_api.ingest.rules import extension
from sbam_contracts.api import ErrorCode

HEAD_BYTES: Final = 8192

# Containers and images: the signature the extension promises.
_PDF = "pdf"
_ZIP = "zip"
_GZIP = "gzip"
_OLE = "ole"  # Outlook .msg
_PNG, _JPEG, _TIFF = "png", "jpeg", "tiff"
_TEXT = "text"

_EXPECTED: Final = {
    "pdf": _PDF,
    "docx": _ZIP,
    "xlsx": _ZIP,
    "zip": _ZIP,
    "tar.gz": _GZIP,
    "msg": _OLE,
    "png": _PNG,
    "jpg": _JPEG,
    "jpeg": _JPEG,
    "tiff": _TIFF,
}
# Everything else the allowlist accepts is a text format: extension decides among them.

_MACHO: Final = (
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",  # universal binary (also a Java class file: either way not a document)
)
# Media that is a known Future type (TD §5.2): recognised only to give a precise answer.
_FUTURE_MAGIC: Final = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"RIFF", b"OggS", b"fLaC")


@dataclass(frozen=True)
class Detected:
    detected_type: str  # the extension of the type, e.g. "pdf", "docx", "tar.gz", "md"
    row_label: str  # the Filetype row it maps to, e.g. "Filetype #21"
    loader: str  # the loader id of that row (filetypes.config.LOADERS)


@dataclass(frozen=True)
class Rejected:
    code: ErrorCode  # TYPE_MISMATCH, UNSUPPORTED_TYPE or INVALID_ENCODING


ZipNames = Callable[[], set[str]]


def is_executable(head: bytes) -> bool:
    if head.startswith(b"\x7fELF") or head.startswith(_MACHO):
        return True
    if head.startswith(b"MZ"):  # PE: a pointer at 0x3C to "PE\0\0", or the DOS stub's message
        pointer = int.from_bytes(head[0x3C:0x40], "little") if len(head) >= 0x40 else 0
        return head[pointer : pointer + 4] == b"PE\x00\x00" or b"DOS mode" in head[:512]
    return False


def kind_of(head: bytes) -> str | None:
    """The container or image signature at the start of the file, or None.

    The signatures that sit at offset 0 are tried first. A PDF may have a few bytes before its
    `%PDF-` marker, so it is the loosest test and comes last: an Outlook message or a ZIP that
    carries a PDF attachment is not a PDF.
    """
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _ZIP
    if head.startswith(b"\x1f\x8b"):
        return _GZIP
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _OLE
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return _PNG
    if head.startswith(b"\xff\xd8\xff"):
        return _JPEG
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return _TIFF
    if b"%PDF-" in head[:1024]:
        return _PDF
    return None


def is_future_media(head: bytes) -> bool:
    return head.startswith(_FUTURE_MAGIC) or head[4:8] == b"ftyp"


def is_text(head: bytes) -> bool:
    """UTF-8 (BOM allowed), no NUL, in the part we have (a cut multi-byte character is fine)."""
    if b"\x00" in head:
        return False
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        decoder.decode(head, final=False)
    except UnicodeDecodeError:
        return False
    return True


def _is_tar_gz(head: bytes) -> bool:
    try:
        block = gzip.GzipFile(fileobj=io.BytesIO(head)).read(512)
    except (OSError, EOFError):
        return False
    return len(block) >= 262 and block[257:262] == b"ustar"


def _zip_matches(ext: str, names: set[str]) -> bool:
    if ext == "zip":
        return True
    if "[Content_Types].xml" not in names:
        return False
    if ext == "docx":
        return "word/document.xml" in names and not any(n.startswith("xl/") for n in names)
    return "xl/workbook.xml" in names and not any(n.startswith("word/") for n in names)  # xlsx


def detect(
    name: str,
    head: bytes,
    zip_names: ZipNames | None = None,
    filetypes: tuple[Filetype, ...] | None = None,
) -> Detected | Rejected:
    ext = extension(name)
    if is_executable(head):
        return Rejected(ErrorCode.TYPE_MISMATCH)
    row = enabled_rows(filetypes).get(ext)
    if row is None or row.config.loader is None:
        return Rejected(ErrorCode.UNSUPPORTED_TYPE)

    expected = _EXPECTED.get(ext, _TEXT)
    seen = kind_of(head)
    if expected == _TEXT:
        if seen is not None or is_future_media(head):
            return Rejected(ErrorCode.TYPE_MISMATCH)  # a PDF, an archive, ... named like text
        if not is_text(head):
            return Rejected(ErrorCode.INVALID_ENCODING)
    else:
        if seen != expected:
            return Rejected(ErrorCode.TYPE_MISMATCH)
        if expected == _GZIP and not _is_tar_gz(head):
            return Rejected(ErrorCode.TYPE_MISMATCH)  # a gzip file, but not a tar archive
        if expected == _ZIP:
            try:
                names = zip_names() if zip_names else set()
            except Exception:  # a truncated or malformed archive
                return Rejected(ErrorCode.TYPE_MISMATCH)
            if not _zip_matches(ext, names):
                return Rejected(ErrorCode.TYPE_MISMATCH)  # a .docx renamed .xlsx, or not OOXML
    return Detected(detected_type=ext, row_label=row.row_label, loader=row.config.loader)
