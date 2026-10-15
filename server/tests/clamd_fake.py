"""A fake clamd for tests (E4.3): speaks VERSION and INSTREAM over a real TCP socket on 127.0.0.1.

It calls a file infected when it starts with the EICAR test string, like real ClamAV (a prefix hides
it: the signature matches the 68-byte test file from offset 0). The string
comes from `fixtures.eicar_bytes()`, which assembles it at run time, so no file in the repo holds it
(E0.8-T04) and Windows Defender stays quiet.
"""

import socket
import struct
import threading
from datetime import UTC, datetime, timedelta
from types import TracebackType

from fixtures import eicar_bytes


def version_string(age: timedelta = timedelta(hours=2), db: int = 27800) -> str:
    stamp = (datetime.now(UTC) - age).strftime("%a %b %d %H:%M:%S %Y")
    return f"ClamAV 1.5.4/{db}/{stamp}"


class FakeClamd:
    """Use as a context manager; `host` and `port` say where it listens.

    `behaviour` is how INSTREAM ends: `normal`, `drop` (close mid-stream), `garbage` (a reply that
    is not a verdict) or `limit` (the size-limit ERROR a real clamd sends). `version` is the VERSION
    reply.
    """

    def __init__(
        self,
        version: str | None = None,
        behaviour: str = "normal",
        version_after_reload: str | None = None,
    ) -> None:
        self.version = version if version is not None else version_string()
        self.version_after_reload = version_after_reload  # what VERSION says once RELOAD is sent
        self.reloads = 0
        self.behaviour = behaviour
        self.scans = 0
        self.sizes: list[int] = []
        self.host = "127.0.0.1"
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, 0))
        self._server.listen(8)
        self.port: int = self._server.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "FakeClamd":
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._running = False
        try:
            self._server.shutdown(socket.SHUT_RDWR)  # wakes the thread blocked in accept() on Linux
        except OSError:
            pass  # Windows refuses to shut down a listening socket; close() is enough there
        self._server.close()

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            command = b""
            while not command.endswith(b"\0"):
                block = conn.recv(1)
                if not block:
                    return
                command += block
            if command == b"zVERSION\0":
                conn.sendall(self.version.encode() + b"\0")
            elif command == b"zRELOAD\0":
                self.reloads += 1
                if self.version_after_reload is not None:
                    self.version = self.version_after_reload
                conn.sendall(b"RELOADING\0")
            elif command == b"zINSTREAM\0":
                self._instream(conn)

    def _instream(self, conn: socket.socket) -> None:
        body = b""
        while True:
            header = _recv_exact(conn, 4)
            if header is None:
                return
            (length,) = struct.unpack("!I", header)
            if length == 0:
                break
            chunk = _recv_exact(conn, length)
            if chunk is None:
                return
            body += chunk
            if self.behaviour == "drop":
                return  # closes the connection with the upload half done
        self.scans += 1
        self.sizes.append(len(body))
        if self.behaviour == "garbage":
            conn.sendall(b"stream: something odd\0")
        elif self.behaviour == "limit":
            conn.sendall(b"INSTREAM size limit exceeded. ERROR\0")
        elif body.startswith(eicar_bytes()):  # like the real signature: the file starts with it
            conn.sendall(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
        else:
            conn.sendall(b"stream: OK\0")


def _recv_exact(conn: socket.socket, size: int) -> bytes | None:
    data = b""
    while len(data) < size:
        block = conn.recv(size - len(data))
        if not block:
            return None
        data += block
    return data
