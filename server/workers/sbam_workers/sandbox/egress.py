"""Egress evidence for a certifying run (E13.2, ADR 0023, decision 8).

CubeSandbox's audit log is a host-local JSONL file of HTTP and HTTPS requests that carries the VM's
*IP*, not its id; connections it drops at L3/L4 are not in it at all. The host collector
(`compose/sandbox-host`) joins that log with the VM's address and with what it captured on the VM's
tap device, and writes one `EgressEvent` per line to the file this reader is given. The reader only
reads that file:

  * a missing or unreadable file is `EGRESS_LOG_UNAVAILABLE`: with no evidence a run cannot certify;
  * a line that is not a valid event is skipped and counted, never trusted;
  * only lines that name this VM are parsed, so a file of millions of lines costs one substring
    scan.

When the collector runs on another machine than the worker, it serves the same lines over HTTP
instead (`HttpEgressReader`, `SANDBOX_EGRESS_URL`): `GET <url>/egress/<vm_id>` with a bearer token
answers the VM's lines. Only a 200 is evidence (an empty 200: the VM was watched from creation and
attempted nothing). 404 (never seen), 409 (seen only in part), 503 (collector down), 401, any other
status, a timeout or a transport error are `EGRESS_LOG_UNAVAILABLE`: a failure never becomes an
empty list. The reader waits `settle_seconds` first, because an unanswered SYN becomes an event only
after the collector's timeout.
"""

import asyncio
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Final
from urllib.parse import quote

import httpx
from pydantic import ValidationError
from sbam_contracts.sandbox import EgressEvent, InfraReason

from sbam_workers.sandbox.backend import SandboxInfraError

log = logging.getLogger("sbam_workers.sandbox")
MAX_EVENTS: Final = 1000


def parse_events(lines: Iterable[str], vm_id: str) -> list[EgressEvent]:
    """This VM's events among `lines`: an invalid line is skipped and counted, never trusted."""
    needle = json.dumps(vm_id)  # as it appears in a line: quoted and escaped
    events: list[EgressEvent] = []
    skipped = 0
    for line in lines:
        if needle not in line:
            continue
        try:
            event = EgressEvent.model_validate_json(line)
        except (ValidationError, ValueError):
            skipped += 1
            continue
        if event.vm_id == vm_id:  # the substring may sit in another field
            events.append(event)
            if len(events) >= MAX_EVENTS:
                break
    if skipped:
        log.warning("egress evidence lines skipped", extra={"vm_id": vm_id, "skipped": skipped})
    return events


def read_events(path: Path, vm_id: str) -> list[EgressEvent]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as lines:
            return parse_events(lines, vm_id)
    except OSError:
        raise SandboxInfraError(
            InfraReason.EGRESS_LOG_UNAVAILABLE, "the egress evidence file cannot be read"
        ) from None


class JsonlEgressReader:
    def __init__(self, path: str, settle_seconds: float = 0.0) -> None:
        self.path = Path(path)
        self.settle_seconds = settle_seconds  # the collector's flush interval, if it has one

    async def __call__(self, vm_id: str) -> list[EgressEvent]:
        if self.settle_seconds:
            await asyncio.sleep(self.settle_seconds)
        return await asyncio.to_thread(read_events, self.path, vm_id)


HTTP_TIMEOUT_S: Final = 10.0
HTTP_SETTLE_S: Final = 0.5  # the collector turns an unanswered SYN into an event after 300 ms


class HttpEgressReader:
    """The collector's lines over HTTP (see the module docstring)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        settle_seconds: float = HTTP_SETTLE_S,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settle_seconds = settle_seconds
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=HTTP_TIMEOUT_S,
        )

    async def __call__(self, vm_id: str) -> list[EgressEvent]:
        if self.settle_seconds:
            await asyncio.sleep(self.settle_seconds)
        try:
            response = await self._client.get(f"/egress/{quote(vm_id, safe='')}")
        except httpx.HTTPError:  # timeouts and transport errors included
            raise SandboxInfraError(
                InfraReason.EGRESS_LOG_UNAVAILABLE, "the egress evidence service is unreachable"
            ) from None
        if response.status_code != 200:
            raise SandboxInfraError(
                InfraReason.EGRESS_LOG_UNAVAILABLE,
                f"the egress evidence service answered {response.status_code}",
            )
        return parse_events(response.text.splitlines(), vm_id)
