"""A small reader for Outlook .msg files (OLE / CFB, MS-CFB, MS-OXMSG), for the fixture tests.

It reads only what the fixture needs: CFB version 3 or 4, the FAT and mini FAT, the directory tree
and the `__substg1.0_<tag>` property streams. It is written separately from the fixture writer in
`scripts/generate_test_fixtures.py`, so the two check each other. The file was also opened once with
the real `olefile` and `extract-msg` (see issue #202).
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path

FREESECT, ENDOFCHAIN = 0xFFFFFFFF, 0xFFFFFFFE
NOSTREAM = 0xFFFFFFFF
SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class NotAMsgFileError(ValueError):
    pass


@dataclass
class Attachment:
    filename: str
    data: bytes
    mime: str | None


@dataclass
class OutlookMessage:
    streams: dict[str, bytes]  # "a/b/name" -> bytes, every stream in the file
    subject: str = ""
    sender_name: str = ""
    sender_email: str = ""
    body: str = ""
    message_class: str = ""
    recipients: list[tuple[str, str]] = field(default_factory=list)  # (display name, address)
    attachments: list[Attachment] = field(default_factory=list)

    def texts(self) -> list[str]:
        """Every text property (UTF-16 `001F`), for the fixture pack's address and phone scans."""
        return [
            data.decode("utf-16-le", errors="replace")
            for name, data in self.streams.items()
            if name.rsplit("/", 1)[-1].startswith("__substg1.0_") and name.endswith("001F")
        ]


def read_msg(source: Path | bytes) -> OutlookMessage:
    data = source.read_bytes() if isinstance(source, Path) else source
    streams = _read_cfb(data)
    message = OutlookMessage(streams=streams)

    def text(prefix: str, prop: int) -> str:
        raw = streams.get(f"{prefix}__substg1.0_{prop:04X}001F")
        return raw.decode("utf-16-le") if raw is not None else ""

    message.message_class = text("", 0x001A)
    message.subject = text("", 0x0037)
    message.sender_name = text("", 0x0C1A)
    message.sender_email = text("", 0x0C1F)
    message.body = text("", 0x1000)
    for folder in sorted({name.split("/")[0] for name in streams if "/" in name}):
        if folder.startswith("__recip_version1.0_"):
            message.recipients.append((text(folder + "/", 0x3001), text(folder + "/", 0x3003)))
        elif folder.startswith("__attach_version1.0_"):
            prefix = folder + "/"
            mime = text(prefix, 0x370E) or None
            message.attachments.append(
                Attachment(
                    filename=text(prefix, 0x3707),
                    data=streams.get(f"{prefix}__substg1.0_37010102", b""),
                    mime=mime,
                )
            )
    return message


def _read_cfb(data: bytes) -> dict[str, bytes]:
    if data[:8] != SIGNATURE:
        raise NotAMsgFileError("not a compound file (bad signature)")
    sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
    mini_size = 1 << struct.unpack_from("<H", data, 32)[0]
    n_fat, dir_first = struct.unpack_from("<II", data, 44)
    cutoff, minifat_first, n_minifat = struct.unpack_from("<III", data, 56)
    difat = [v for v in struct.unpack_from("<109I", data, 76) if v != FREESECT][:n_fat]

    def sector(index: int) -> bytes:
        start = (index + 1) * sector_size
        return data[start : start + sector_size]

    fat: list[int] = []
    for index in difat:
        fat += struct.unpack(f"<{sector_size // 4}I", sector(index))

    def chain(first: int) -> list[int]:
        found, seen = [], set()
        while first not in (ENDOFCHAIN, FREESECT):
            if first in seen:
                raise NotAMsgFileError("a sector chain loops")
            seen.add(first)
            found.append(first)
            first = fat[first]
        return found

    def read_chain(first: int) -> bytes:
        return b"".join(sector(i) for i in chain(first))

    directory = read_chain(dir_first)
    entries = []
    for offset in range(0, len(directory), 128):
        raw = directory[offset : offset + 128]
        name_length = struct.unpack_from("<H", raw, 64)[0]
        entries.append(
            {
                "name": raw[: max(name_length - 2, 0)].decode("utf-16-le"),
                "kind": raw[66],
                "left": struct.unpack_from("<I", raw, 68)[0],
                "right": struct.unpack_from("<I", raw, 72)[0],
                "child": struct.unpack_from("<I", raw, 76)[0],
                "start": struct.unpack_from("<I", raw, 116)[0],
                "size": struct.unpack_from("<Q", raw, 120)[0],
            }
        )
    mini_stream = read_chain(entries[0]["start"])[: entries[0]["size"]]
    mini_fat: list[int] = []
    for index in chain(minifat_first) if n_minifat else []:
        mini_fat += struct.unpack(f"<{sector_size // 4}I", sector(index))

    def stream_bytes(entry: dict[str, int]) -> bytes:
        size = entry["size"]
        if size == 0:
            return b""
        if size < cutoff:
            out, index = b"", entry["start"]
            while index not in (ENDOFCHAIN, FREESECT):
                out += mini_stream[index * mini_size : (index + 1) * mini_size]
                index = mini_fat[index]
            return out[:size]
        return read_chain(entry["start"])[:size]

    streams: dict[str, bytes] = {}

    def siblings(first: int) -> list[int]:
        if first == NOSTREAM:
            return []
        entry = entries[first]
        return [*siblings(entry["left"]), first, *siblings(entry["right"])]

    def walk(parent: int, prefix: str) -> None:
        for index in siblings(entries[parent]["child"]):
            entry = entries[index]
            path = prefix + entry["name"]
            if entry["kind"] == 2:
                streams[path] = stream_bytes(entry)
            elif entry["kind"] == 1:
                walk(index, path + "/")

    walk(0, "")
    return streams
