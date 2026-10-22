"""What every loader takes and returns."""

from collections.abc import Callable
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


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    markdown: str = ""
    report: ParseReport = field(default_factory=ParseReport)


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
