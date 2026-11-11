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

When the collector runs on another host, `HttpEgressReader` asks its read-only endpoint instead
(`GET /egress/<vm_id>`, bearer token, a JSONL body of the same events). The rule is the same: only a
200 is evidence. A 404 (never saw the VM), 409 (saw it part-way), 503, 401, a timeout, a refused
connection or a body that is not events is `EGRESS_LOG_UNAVAILABLE`. A failure is never turned into
"nothing was attempted": an empty 200 is the only way to say that.
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


def _unavailable(why: str) -> SandboxInfraError:
    return SandboxInfraError(InfraReason.EGRESS_LOG_UNAVAILABLE, why)


class HttpEgressReader:
    """The collector's read-only HTTP endpoint (see the module docstring)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
        settle_seconds: float = 0.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.settle_seconds = settle_seconds
        self._client = client

    async def __call__(self, vm_id: str) -> list[EgressEvent]:
        if not vm_id.isalnum():  # an id goes into a URL path: never anything but letters and digits
            raise _unavailable("the sandbox id is not an id")
        if self.settle_seconds:
            await asyncio.sleep(self.settle_seconds)
        url = f"{self.base_url}/egress/{vm_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            if self._client is not None:
                response = await self._client.get(url, headers=headers, timeout=self.timeout)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, headers=headers, timeout=self.timeout)
        except httpx.HTTPError:
            raise _unavailable("the egress collector cannot be reached") from None
        if response.status_code != 200:
            raise _unavailable(f"the egress collector answered {response.status_code}")
        events: list[EgressEvent] = []
        for line in response.text.splitlines():
            if not line.strip():
                continue
            try:
                event = EgressEvent.model_validate_json(line)
            except (ValidationError, ValueError):
                # The collector promises valid events. A line that is not one means we cannot tell
                # what happened: no evidence (unlike the file, nothing is skipped here).
                raise _unavailable(
                    "the egress collector sent a line that is not an event"
                ) from None
            if event.vm_id != vm_id:
                raise _unavailable("the egress collector sent an event of another sandbox")
            events.append(event)
            if len(events) >= MAX_EVENTS:
                break
        return events
