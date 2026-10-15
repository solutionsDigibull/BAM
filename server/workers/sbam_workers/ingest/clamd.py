"""A small clamd client: INSTREAM scans and VERSION over TCP (E4.3, TD §4 T3).

No dependency: the protocol is a few bytes. `zINSTREAM\\0`, then the file in chunks, each with a
4-byte big-endian length, then a zero length; clamd answers `stream: OK` or
`stream: <signature> FOUND`. clamd is on the internal Docker network only.

Anything that stops a verdict (clamd down, dropped connection, an ERROR reply, an unreadable
VERSION) is a `ClamdError`: the activity retries it and, after the last attempt, the version is
`SCAN_FAILED`. A scan is never skipped and never passes by default.
"""

import socket
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

CHUNK = 64 * 1024
_END = b"\0\0\0\0"


class ClamdError(Exception):
    """No verdict could be obtained. Retryable."""


class SignatureStaleError(ClamdError):
    """clamd's signatures are older than allowed (or their date is unknown). Retryable: freshclam
    may catch up; the signature-age alert tells an operator if it does not."""


@dataclass(frozen=True)
class ClamVersion:
    raw: str  # e.g. `ClamAV 1.5.4/27800/Tue Oct 14 07:25:00 2026`
    signature_date: datetime | None  # UTC; None when clamd's reply had no readable date


@dataclass(frozen=True)
class ScanVerdict:
    infected: bool
    signature: str | None = None  # the signature name when infected, e.g. `Win.Test.EICAR_HDB-1`


def parse_version(raw: str) -> ClamVersion:
    parts = raw.strip().split("/")
    date: datetime | None = None
    if len(parts) >= 3:
        try:
            date = datetime.strptime(parts[2].strip(), "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
        except ValueError:
            date = None
    return ClamVersion(raw=raw.strip(), signature_date=date)


def signature_age(version: ClamVersion, now: datetime) -> timedelta | None:
    return None if version.signature_date is None else now - version.signature_date


def require_fresh(version: ClamVersion, now: datetime, max_age: timedelta) -> None:
    """Raise `SignatureStaleError` unless the signatures are at most `max_age` old."""
    age = signature_age(version, now)
    if age is None:
        raise SignatureStaleError("clamd did not report a signature date")
    if age > max_age:
        raise SignatureStaleError(f"signatures are {age} old; the limit is {max_age}")


class ClamdClient:
    def __init__(self, host: str, port: int, timeout: float = 60.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def _connect(self) -> socket.socket:
        try:
            return socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise ClamdError(f"cannot reach clamd: {type(exc).__name__}") from None

    def version(self) -> ClamVersion:
        with self._connect() as sock:
            try:
                sock.sendall(b"zVERSION\0")
                reply = _read_reply(sock)
            except OSError as exc:
                raise ClamdError(f"VERSION failed: {type(exc).__name__}") from None
        if not reply:
            raise ClamdError("empty VERSION reply")
        return parse_version(reply)

    def scan(self, stream: BinaryIO) -> ScanVerdict:
        with self._connect() as sock:
            try:
                sock.sendall(b"zINSTREAM\0")
                while block := stream.read(CHUNK):
                    sock.sendall(struct.pack("!I", len(block)) + block)
                sock.sendall(_END)
            except OSError:
                # clamd closes early when it refuses the stream (size limit); its reason is in the
                # reply, if there is one. Otherwise it is a dropped connection.
                reply = _try_read(sock)
                raise ClamdError(f"INSTREAM interrupted: {reply or 'connection dropped'}") from None
            try:
                reply = _read_reply(sock)
            except OSError as exc:
                raise ClamdError(f"no verdict: {type(exc).__name__}") from None
        return _verdict(reply)


def _read_reply(sock: socket.socket) -> str:
    data = b""
    while not data.endswith(b"\0") and (block := sock.recv(4096)):
        data += block
    return data.rstrip(b"\0\n").decode("utf-8", "replace")


def _try_read(sock: socket.socket) -> str:
    try:
        return _read_reply(sock)
    except OSError:
        return ""


def _verdict(reply: str) -> ScanVerdict:
    text = reply.removeprefix("stream:").strip()
    if text == "OK":
        return ScanVerdict(infected=False)
    if text.endswith("FOUND"):
        return ScanVerdict(infected=True, signature=text.removesuffix("FOUND").strip() or None)
    raise ClamdError(f"unexpected clamd reply: {text[:80] or 'empty'}")
