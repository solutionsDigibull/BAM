"""What every loader takes and returns."""

import io
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, ParseReport, Table

Heartbeat = Callable[[], None]


def no_heartbeat() -> None:
    """For callers outside an activity (tests, scripts)."""


class ParseError(Exception):
    """The file cannot be parsed. `code` is the version's `parse_code`: CORRUPT, ENCRYPTED or EMPTY
    (final: retrying cannot change the file), or PARSE_ERROR for a limit the file exceeds."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Attachment:
    """A file an e-mail carries (E4.6). The loader only finds it; the parse step does not store it.
    It becomes a child document with its own scan and type check (later issue)."""

    name: str
    content_type: str
    data: bytes


@dataclass
class Parsed:
    """What a loader returns. `report` is complete when the loader returns. `blocks` and `markdown`
    may be lazy (a big workbook is written out piece by piece, never held as one string or one list
    of block objects); the activity iterates them once, in order."""

    blocks: Iterable[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    markdown: str | Iterable[str] = ""
    report: ParseReport = field(default_factory=ParseReport)
    attachments: list[Attachment] = field(default_factory=list)  # e-mail only


Loader = Callable[[bytes, Heartbeat], Parsed]

# A loader beats its heart this often (rows, paragraphs): the activity's heartbeat timeout is 60 s.
BEAT_EVERY = 1000


def decode(data: bytes) -> str:
    """UTF-8 (a BOM is dropped) with `\n` line ends. Anything else is CORRUPT: type detection only
    looked at the first 8 KB, so a file can still be bad further in."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseError(ErrorCode.CORRUPT, "the file is not valid UTF-8") from None
    if "\x00" in text:
        raise ParseError(ErrorCode.CORRUPT, "the file contains a NUL byte")
    return text.replace("\r\n", "\n").replace("\r", "\n")


# A ZIP container (DOCX, XLSX) that unpacks to more than this, or into more files than this, is
# refused before any part of it is read: the memory cap of the worker is 1 GiB.
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000


def check_zip(data: bytes) -> None:
    """CORRUPT for a file that is not a readable ZIP; PARSE_ERROR for a container that is too big
    once unpacked (a zip bomb). Only the directory is read, nothing is decompressed."""
    try:
        entries = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ParseError(ErrorCode.CORRUPT, "the file is not a readable container") from None
    if len(entries) > MAX_ZIP_ENTRIES:
        raise ParseError(ErrorCode.PARSE_ERROR, "the container holds too many files")
    if sum(e.file_size for e in entries) > MAX_UNPACKED_BYTES:
        raise ParseError(ErrorCode.PARSE_ERROR, "the container is too large once unpacked")


def gfm_table(header: list[str], rows: Iterable[list[str]]) -> Iterable[str]:
    """A table as GitHub-flavoured Markdown, one line at a time."""

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(_cell(c) for c in cells) + " |\n"

    yield line(header)
    yield "| " + " | ".join("---" for _ in header) + " |\n"
    for row in rows:
        yield line(row)


def _cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", r"\|").replace("\n", " ")
