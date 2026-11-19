"""The Staging egress proxy (#765, ADR 0036 decision 3): the only way out of a Staging container.

Containers sit on an internal Docker network and reach the outside only through this proxy
(`HTTP_PROXY`/`HTTPS_PROXY`). Each deployment gets a random token, sent as the proxy user name, and
a
policy file `<token>.json` (`{"deployment": ..., "hosts": [...]}`, the hosts the certified manifest
declares). A request whose token has no policy file, or whose host the policy does not allow, is
answered 403 and logged (`sbam.staging.egress`, deployment and host only); nothing is dialled.
Ports are limited to 80 and 443. The proxy supports `CONNECT` (HTTPS) and absolute-URI HTTP.

Run it on a host or container that is on the Staging network AND has outside access:
`python -m sbam_workers.staging.egress_proxy --listen 0.0.0.0:3128 --policy-dir /var/sbam/egress`.
"""

import argparse
import asyncio
import base64
import binascii
import json
import logging
import re
from collections.abc import Collection
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from sbam_api.staging.egress import EgressPolicy

log = logging.getLogger("sbam.staging.egress")

TOKEN: Final = re.compile(r"^[0-9a-f]{32}$")
MAX_HEADER: Final = 16 * 1024
DEFAULT_PORTS: Final = (80, 443)


class PolicyDir:
    """The per-deployment allowlists, as files the runtime writes and the proxy reads."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, token: str, deployment: str, hosts: Collection[str]) -> None:
        if TOKEN.match(token) is None:
            raise ValueError("token must be 32 lower-case hex characters")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{token}.json"
        target.write_text(json.dumps({"deployment": deployment, "hosts": sorted(hosts)}))

    def remove(self, token: str) -> None:
        if TOKEN.match(token) is not None:
            (self.root / f"{token}.json").unlink(missing_ok=True)

    def read(self, token: str) -> tuple[str, EgressPolicy] | None:
        if TOKEN.match(token) is None:
            return None
        try:
            data = json.loads((self.root / f"{token}.json").read_text())
        except (OSError, ValueError):
            return None
        return str(data.get("deployment", "unknown")), EgressPolicy(data.get("hosts", []))


def _token_of(headers: dict[str, str]) -> str:
    raw = headers.get("proxy-authorization", "")
    if not raw.lower().startswith("basic "):
        return ""
    try:
        return base64.b64decode(raw[6:].strip(), validate=True).decode().split(":", 1)[0]
    except (binascii.Error, UnicodeDecodeError):
        return ""


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


class EgressProxy:
    def __init__(self, policies: PolicyDir, allowed_ports: Collection[int] = DEFAULT_PORTS) -> None:
        self.policies = policies
        self.allowed_ports = frozenset(allowed_ports)

    @staticmethod
    async def _deny(writer: asyncio.StreamWriter, why: str) -> None:
        body = why.encode()
        writer.write(
            b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        if len(head) > MAX_HEADER:
            await self._deny(writer, "header too large")
            return
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        headers = {
            k.strip().lower(): v.strip()
            for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)
        }
        if len(parts) != 3:
            await self._deny(writer, "bad request")
            return
        method, target, version = parts
        policy = self.policies.read(_token_of(headers))
        if policy is None:
            log.warning("staging egress blocked: unknown deployment token")
            await self._deny(writer, "no egress policy for this caller")
            return
        deployment, rules = policy
        if method == "CONNECT":
            host, _, port_text = target.rpartition(":")
            port = int(port_text) if port_text.isdigit() else 0
        else:
            url = urlsplit(target)
            host, port = url.hostname or "", url.port or 80
        decision = rules.check(host, deployment=deployment)
        if not decision.allowed or port not in self.allowed_ports:
            if decision.allowed:
                log.warning(
                    "staging egress blocked: deployment=%s host=%s port=%d", deployment, host, port
                )
            await self._deny(writer, f"{decision.host or host} is not allowed")
            return
        try:
            up_reader, up_writer = await asyncio.open_connection(decision.host, port)
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
        else:
            path = urlsplit(target)
            origin = (path.path or "/") + (f"?{path.query}" if path.query else "")
            kept = [ln for ln in lines[1:] if ln and not ln.lower().startswith("proxy-")]
            up_writer.write(
                (f"{method} {origin} {version}\r\n" + "\r\n".join(kept) + "\r\n\r\n").encode(
                    "latin-1"
                )
            )
            await up_writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def serve(listen: str, policies: PolicyDir) -> None:
    host, _, port = listen.rpartition(":")
    proxy = EgressProxy(policies)
    server = await asyncio.start_server(proxy.handle, host or "0.0.0.0", int(port))  # noqa: S104
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="SBAM Staging egress proxy")
    parser.add_argument("--listen", default="0.0.0.0:3128")
    parser.add_argument("--policy-dir", required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(args.listen, PolicyDir(args.policy_dir)))


if __name__ == "__main__":
    main()
