"""Archives, `.zip` and `.tar.gz` (E4.7): the members become child documents.

The loader reads the archive in memory and never writes a member to disk, so no member path can
land anywhere: a path is only a name that is kept or refused. It returns the accepted members in
`Parsed.attachments` (name, type, bytes and `path` inside the archive); the registration step makes
each one a child document that is scanned, type-checked and parsed on its own.

Limits (TD §5.2), counted on the bytes really read, not on what the headers say:
  * one member at most 15 MB, all members together at most 100 MB, and no more than 100 times the
    size of the archive, with 1 MB always allowed (a zip bomb stops here: ARCHIVE_LIMIT for the
    whole archive, after at most that many bytes);
  * at most 20 members are taken; the rest are listed as rejected (ARCHIVE_LIMIT);
  * at most 10,000 entries are looked at.
A member is refused by name, with its code, and the others go on: an absolute path, `..`, a symlink,
hardlink, device or FIFO (PATH_UNSAFE); an archive inside the archive (NESTED_ARCHIVE, one level
only); an encrypted zip member (ENCRYPTED); an extension that is not allowed (UNSUPPORTED_TYPE).
An archive with no member left is EMPTY; one that cannot be read is CORRUPT.
"""

import gzip
import io
import mimetypes
import re
import tarfile
import zipfile
import zlib
from collections.abc import Iterator
from typing import IO, BinaryIO

from sbam_api.ingest.rules import extension, is_allowed
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import (
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_RATIO,
    MAX_ARCHIVE_UNPACKED_BYTES,
    MAX_FILE_BYTES,
    ArchiveReport,
    Block,
    BlockType,
    ParseReport,
    RejectedMember,
)

from sbam_workers.ingest.loaders.base import Attachment, Heartbeat, Parsed, ParseError

MAX_ENTRIES = 10_000  # entries of an archive that are looked at
RATIO_FLOOR = 1024 * 1024  # the ratio rule starts above this: a tar pads even one small file
CHUNK = 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:")
_ARCHIVE_EXTENSIONS = frozenset({"zip", "tar.gz", "tgz", "gz", "tar", "7z", "rar", "bz2", "xz"})
_ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"7z\xbc\xaf", b"Rar!")


class Budget:
    """The bytes read from one archive, against what it may expand to."""

    def __init__(self, compressed: int) -> None:
        ratio = max(MAX_ARCHIVE_RATIO * compressed, RATIO_FLOOR)  # tar pads a tiny archive
        self.limit = min(MAX_ARCHIVE_UNPACKED_BYTES, ratio)
        self.used = 0

    def take(self, count: int) -> None:
        self.used += count
        if self.used > self.limit:
            raise ParseError(ErrorCode.ARCHIVE_LIMIT, "the archive expands beyond its limits")


class Members:
    """What is found while the members go by: the ones taken and the ones refused."""

    def __init__(self) -> None:
        self.total = 0
        self.taken: list[Attachment] = []
        self.rejected: list[RejectedMember] = []

    def refuse(self, path: str, code: ErrorCode) -> None:
        self.rejected.append(RejectedMember(path=shown(path), code=code))

    def room(self) -> bool:
        return len(self.taken) < MAX_ARCHIVE_MEMBERS


def shown(path: str) -> str:
    """A member path as it is reported: printable, valid UTF-8, at most 1024 characters."""
    text = path.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    return (_CONTROL.sub("?", text)[:1024]) or "?"


def clean_path(raw: str) -> str | None:
    """The member's path with `/` separators and no empty or `.` part, or None when it is not safe:
    absolute, a drive letter, a `..` part, a control character or too long."""
    text = raw.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    if _CONTROL.search(text):
        return None
    slashed = text.replace("\\", "/")
    if slashed.startswith("/") or _DRIVE.match(slashed):
        return None
    parts = [part for part in slashed.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return None
    path = "/".join(parts)
    return path if len(path) <= 1024 else None


def refusal(path: str) -> ErrorCode | None:
    """Why a member with this (clean) path is not taken, judged by its name alone."""
    ext = extension(path)
    if ext in _ARCHIVE_EXTENSIONS:
        return ErrorCode.NESTED_ARCHIVE
    if not is_allowed(path):
        return ErrorCode.UNSUPPORTED_TYPE
    return None


def read_member(source: IO[bytes], budget: Budget, beat: Heartbeat) -> bytes | None:
    """The member's bytes, or None when it is over 15 MB (the rest of it is not read). Every byte
    read counts against the archive's budget."""
    out = io.BytesIO()
    while chunk := source.read(CHUNK):
        budget.take(len(chunk))
        if out.tell() + len(chunk) > MAX_FILE_BYTES:
            return None
        out.write(chunk)
        beat()
    return out.getvalue()


def keep(members: Members, path: str, data: bytes) -> None:
    if data[:4].startswith(_ARCHIVE_MAGIC) or data[:2] == b"\x1f\x8b":
        members.refuse(path, ErrorCode.NESTED_ARCHIVE)  # an archive under another name
        return
    name = path.rpartition("/")[2]
    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
    members.taken.append(Attachment(name=name, content_type=kind, data=data, path=path))


def load_zip(data: bytes, beat: Heartbeat) -> Parsed:
    budget, members = Budget(len(data)), Members()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ParseError(ErrorCode.ARCHIVE_LIMIT, "the archive has too many entries")
        if sum(info.file_size for info in infos) > budget.limit:
            raise ParseError(ErrorCode.ARCHIVE_LIMIT, "the archive expands beyond its limits")
        for info in infos:
            if info.is_dir():
                continue
            members.total += 1
            _zip_member(archive, info, members, budget, beat)
    except ParseError:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError, RuntimeError):
        raise ParseError(ErrorCode.CORRUPT, "the file is not a readable zip archive") from None
    return result(members)


def _zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    members: Members,
    budget: Budget,
    beat: Heartbeat,
) -> None:
    path = clean_path(info.filename)
    if path is None:
        return members.refuse(info.filename, ErrorCode.PATH_UNSAFE)
    mode = info.external_attr >> 16
    if mode and (mode & 0o170000) not in (0, 0o100000):  # not a regular file: link, device, FIFO
        return members.refuse(path, ErrorCode.PATH_UNSAFE)
    if info.flag_bits & 0x1:
        return members.refuse(path, ErrorCode.ENCRYPTED)
    if (code := refusal(path)) is not None:
        return members.refuse(path, code)
    if info.file_size > MAX_FILE_BYTES or not members.room():
        return members.refuse(path, ErrorCode.ARCHIVE_LIMIT)
    with archive.open(info) as source:
        content = read_member(source, budget, beat)
    if content is None:
        return members.refuse(path, ErrorCode.ARCHIVE_LIMIT)
    keep(members, path, content)


class Counting:
    """A read-only stream that counts what is read from it against the budget. `tarfile` reads a
    gzip stream sequentially through it, so skipped members are counted too."""

    def __init__(self, source: BinaryIO, budget: Budget) -> None:
        self.source, self.budget = source, budget

    def read(self, size: int = -1) -> bytes:
        chunk = self.source.read(CHUNK if size < 0 else size)
        self.budget.take(len(chunk))
        return chunk


def load_targz(data: bytes, beat: Heartbeat) -> Parsed:
    budget, members = Budget(len(data)), Members()
    try:
        stream = Counting(gzip.GzipFile(fileobj=io.BytesIO(data)), budget)  # type: ignore[arg-type]
        with tarfile.open(fileobj=stream, mode="r|") as archive:  # type: ignore[call-overload]
            for entries, entry in enumerate(archive, start=1):
                if entries > MAX_ENTRIES:
                    raise ParseError(ErrorCode.ARCHIVE_LIMIT, "the archive has too many entries")
                if entry.isdir():
                    continue
                members.total += 1
                _tar_member(archive, entry, members, budget, beat)
    except ParseError:
        raise
    except (tarfile.TarError, zlib.error, EOFError, OSError, ValueError):
        raise ParseError(ErrorCode.CORRUPT, "the file is not a readable tar.gz archive") from None
    return result(members)


def _tar_member(
    archive: tarfile.TarFile,
    entry: tarfile.TarInfo,
    members: Members,
    budget: Budget,
    beat: Heartbeat,
) -> None:
    path = clean_path(entry.name)
    if path is None or not entry.isreg():  # a link, device or FIFO is never taken
        return members.refuse(path or entry.name, ErrorCode.PATH_UNSAFE)
    if (code := refusal(path)) is not None:
        return members.refuse(path, code)
    if entry.size > MAX_FILE_BYTES or not members.room():
        return members.refuse(path, ErrorCode.ARCHIVE_LIMIT)
    source = archive.extractfile(entry)
    if source is None:
        return members.refuse(path, ErrorCode.PATH_UNSAFE)
    content = read_member(source, budget, beat)
    if content is None:
        return members.refuse(path, ErrorCode.ARCHIVE_LIMIT)
    keep(members, path, content)


def result(members: Members) -> Parsed:
    if not members.taken:
        raise ParseError(
            ErrorCode.EMPTY, f"no member can be taken ({len(members.rejected)} rejected)"
        )
    report = ArchiveReport(
        members_total=members.total, accepted=len(members.taken), rejected=members.rejected
    )
    lines = list(listing(members))
    blocks: list[Block] = []
    for number, (text, kind) in enumerate(lines, start=1):
        if text:
            plain = text[2:] if kind == BlockType.LIST_ITEM else text.lstrip("# ")
            blocks.append(
                Block(
                    type=kind,
                    text=plain,
                    line=number,
                    level=(
                        (2 if text.startswith("## ") else 1) if kind == BlockType.HEADING else None
                    ),
                )
            )
    markdown = "\n".join(text for text, _ in lines) + "\n"
    return Parsed(
        blocks=blocks,
        markdown=markdown,
        report=ParseReport(archive=report),
        attachments=members.taken,
    )


def listing(members: Members) -> Iterator[tuple[str, BlockType]]:
    yield "# Archive", BlockType.HEADING
    for member in members.taken:
        yield f"- {member.path} ({len(member.data)} bytes)", BlockType.LIST_ITEM
    if members.rejected:
        yield "", BlockType.PARAGRAPH
        yield "## Not taken", BlockType.HEADING
        for item in members.rejected:
            yield f"- {item.path}: {item.code.value}", BlockType.LIST_ITEM
