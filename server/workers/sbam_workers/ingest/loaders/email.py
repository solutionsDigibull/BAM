"""E-mail, `.eml` (RFC 5322) and Outlook `.msg` (OLE compound file) (E4.6).

`doc.md` and `doc.json` carry the headers (From, To, Cc, Date, Subject) and the body. The
attachments are not stored here: the loader hands them back in `Parsed.attachments` (name, type,
bytes) and a later step registers each one as a child document, scanned and type-checked like
any upload. An attachment over 15 MB is left out with a warning. A nested `.eml` attached to a
mail is an attachment like any other; the depth rule (one level) is enforced where children are
created.

Nothing in the message is run, opened or fetched: no link is followed, no remote image loaded, no
script evaluated, and HTML is reduced to its text.
"""

import re
import struct
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage, MIMEPart
from email.parser import BytesParser, HeaderParser
from email.utils import format_datetime
from html.parser import HTMLParser
from typing import Any, cast

import olefile
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, BlockType, ParseReport

from sbam_workers.ingest.loaders.base import Attachment, Heartbeat, Parsed, ParseError

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # the upload limit, for each attachment
MAX_ATTACHMENTS = 100  # a mail with more is not a business mail; the rest are left out
_HEADERS = ("From", "To", "Cc", "Date", "Subject")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def load_eml(data: bytes, beat: Heartbeat) -> Parsed:
    try:
        message = BytesParser(policy=policy.default).parsebytes(data)
        headers = {
            name: _clean(str(message[name])) for name in _HEADERS if message[name] is not None
        }
        body_part = message.get_body(preferencelist=("plain", "html"))
        body = _body_text(body_part) if body_part is not None else ""
        attachments, warnings = _eml_attachments(message)
    except ParseError:
        raise
    except Exception:  # the stdlib parser raises many types on hostile input
        raise ParseError(ErrorCode.CORRUPT, "the file is not a readable e-mail") from None
    if not headers:  # a body with no From, To, Cc, Date or Subject is not a mail
        raise ParseError(ErrorCode.CORRUPT, "the file has no e-mail headers")
    beat()
    return _result(headers, body, attachments, warnings)


def _eml_attachments(message: EmailMessage) -> tuple[list[Attachment], list[str]]:
    found: list[Attachment] = []
    warnings: list[str] = []
    for part in message.iter_attachments():
        if len(found) >= MAX_ATTACHMENTS:
            warnings.append(f"more than {MAX_ATTACHMENTS} attachments: the rest were left out")
            break
        name = _clean(part.get_filename() or "") or f"attachment-{len(found) + 1}"
        content_type = part.get_content_type()
        payload = part.get_content() if part.get_content_maintype() == "message" else None
        if isinstance(payload, EmailMessage):  # a nested message: its own bytes
            raw = payload.as_bytes()
            name = name if "." in name else f"{name}.eml"
        else:
            raw = cast(bytes, part.get_payload(decode=True) or b"")
        found.append(Attachment(name=name, content_type=content_type, data=raw))
    kept = []
    for attachment in found:
        if len(attachment.data) > MAX_ATTACHMENT_BYTES:
            warnings.append(f"attachment {attachment.name!r} is over 15 MB and was left out")
        else:
            kept.append(attachment)
    return kept, warnings


class _Text(HTMLParser):
    """An HTML body as text: no script or style, nothing fetched."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag in ("p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _body_text(part: MIMEPart) -> str:
    text = str(part.get_content())
    if part.get_content_subtype() == "html":
        reader = _Text()
        reader.feed(text)
        reader.close()
        text = "".join(reader.parts)
    return re.sub(r"\n{3,}", "\n\n", _CONTROL.sub("", text.replace("\r\n", "\n"))).strip()


def load_msg(data: bytes, beat: Heartbeat) -> Parsed:
    try:
        with olefile.OleFileIO(data) as ole:
            headers, body, attachments, warnings = _read_msg(ole, beat)
    except ParseError:
        raise
    except Exception:  # olefile raises OSError, struct and index errors on bad files
        raise ParseError(ErrorCode.CORRUPT, "the file is not a readable Outlook message") from None
    return _result(headers, body, attachments, warnings)


def _read_msg(
    ole: "olefile.OleFileIO[Any]", beat: Heartbeat
) -> tuple[dict[str, str], str, list[Attachment], list[str]]:
    def raw_text(prefix: str, tag: str) -> str:
        for suffix, encoding in (("001F", "utf-16-le"), ("001E", "cp1252")):
            path = f"{prefix}__substg1.0_{tag}{suffix}"
            if ole.exists(path):
                return ole.openstream(path).read().decode(encoding, "replace").strip("\x00")
        return ""

    def text(prefix: str, tag: str) -> str:
        return _clean(raw_text(prefix, tag))  # one line, no control characters

    if not ole.exists("__properties_version1.0"):
        raise ParseError(ErrorCode.CORRUPT, "the file is not an Outlook message")
    sender, address = text("", "0C1A"), text("", "0C1F")
    recipients = []
    for storage in _storages(ole, "__recip_version1.0_"):
        address_of = text(f"{storage}/", "39FE") or text(f"{storage}/", "3003")
        recipients.append(_mailbox(text(f"{storage}/", "3001"), address_of))
    sent = HeaderParser().parsestr(raw_text("", "007D")).get("Date", "") or _submit_time(ole)
    headers = {
        "From": _mailbox(sender, address),
        "To": ", ".join(r for r in recipients if r) or text("", "0E04"),
        "Date": _clean(str(sent)),
        "Subject": text("", "0037"),
    }
    headers = {k: v for k, v in headers.items() if v}
    body = _CONTROL.sub("", raw_text("", "1000").replace("\r\n", "\n")).strip()
    warnings: list[str] = []
    attachments: list[Attachment] = []
    for folder in _storages(ole, "__attach_version1.0_"):
        beat()
        if len(attachments) >= MAX_ATTACHMENTS:
            warnings.append(f"more than {MAX_ATTACHMENTS} attachments: the rest were left out")
            break
        name = (
            text(f"{folder}/", "3707") or text(f"{folder}/", "3704") or text(f"{folder}/", "3001")
        )
        data_path = f"{folder}/__substg1.0_37010102"
        label = name or folder
        if not ole.exists(data_path):  # an embedded message or an OLE object: no file bytes
            warnings.append(f"attachment {label!r} has no file content and was left out")
            continue
        if ole.get_size(data_path) > MAX_ATTACHMENT_BYTES:
            warnings.append(f"attachment {label!r} is over 15 MB and was left out")
            continue
        attachments.append(
            Attachment(
                name=name or f"attachment-{len(attachments) + 1}",
                content_type=text(f"{folder}/", "370E") or "application/octet-stream",
                data=ole.openstream(data_path).read(),
            )
        )
    return headers, body, attachments, warnings


def _submit_time(ole: "olefile.OleFileIO[Any]") -> str:
    """PR_CLIENT_SUBMIT_TIME (0x0039, a FILETIME) of the message as an RFC 5322 date, or ''."""
    data = ole.openstream("__properties_version1.0").read()
    for offset in range(32, len(data) - 15, 16):  # the top-level stream has a 32-byte header
        kind, tag = struct.unpack_from("<HH", data, offset)
        if (tag, kind) == (0x0039, 0x0040):
            ticks = struct.unpack_from("<Q", data, offset + 8)[0]
            try:
                moment = datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
            except OverflowError:
                return ""
            return format_datetime(moment)
    return ""


def _storages(ole: "olefile.OleFileIO[Any]", prefix: str) -> list[str]:
    return sorted(
        {e[0] for e in ole.listdir(storages=True, streams=False) if e[0].startswith(prefix)}
    )


def _mailbox(name: str, address: str) -> str:
    if name and address and name != address:
        return f"{name} <{address}>"
    return address or name


def _clean(value: str) -> str:
    return _CONTROL.sub("", value).replace("\n", " ").strip()


def _lines(
    headers: dict[str, str], body: str, attachments: list[Attachment]
) -> Iterator[tuple[str, BlockType]]:
    if "Subject" in headers:
        yield f"# {headers['Subject']}", BlockType.HEADING
    for name in _HEADERS:
        if name in headers:
            yield f"- **{name}:** {headers[name]}", BlockType.LIST_ITEM
    if body:
        yield "", BlockType.PARAGRAPH
        for paragraph in re.split(r"\n\s*\n", body):
            yield paragraph, BlockType.PARAGRAPH
            yield "", BlockType.PARAGRAPH
    if attachments:
        yield "## Attachments", BlockType.HEADING
        for a in attachments:
            yield f"- {a.name} ({a.content_type}, {len(a.data)} bytes)", BlockType.LIST_ITEM


def _result(
    headers: dict[str, str], body: str, attachments: list[Attachment], warnings: list[str]
) -> Parsed:
    if not headers and not body:
        raise ParseError(ErrorCode.CORRUPT, "the file has no e-mail headers or body")
    blocks: list[Block] = []
    out: list[str] = []
    for text, kind in _lines(headers, body, attachments):
        if not text:
            out.append("")
            continue
        number = len(out) + 1
        if kind == BlockType.HEADING:
            level = 1 if text.startswith("# ") else 2
            blocks.append(Block(type=kind, text=text.lstrip("# "), level=level, line=number))
        elif kind == BlockType.LIST_ITEM:
            blocks.append(Block(type=kind, text=text[2:], line=number))
        else:
            blocks.append(Block(type=kind, text=text, line=number))
        out.extend(text.split("\n"))
    report = ParseReport(warnings=warnings)
    return Parsed(
        blocks=blocks,
        markdown="\n".join(out).strip("\n") + "\n",
        report=report,
        attachments=attachments,
    )
