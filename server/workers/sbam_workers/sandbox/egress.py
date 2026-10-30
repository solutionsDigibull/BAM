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
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Final

from pydantic import ValidationError
from sbam_contracts.sandbox import EgressEvent, InfraReason

from sbam_workers.sandbox.backend import SandboxInfraError

log = logging.getLogger("sbam_workers.sandbox")
MAX_EVENTS: Final = 1000


def read_events(path: Path, vm_id: str) -> list[EgressEvent]:
    needle = json.dumps(vm_id)  # as it appears in a line: quoted and escaped
    events: list[EgressEvent] = []
    skipped = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as lines:
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
    except OSError:
        raise SandboxInfraError(
            InfraReason.EGRESS_LOG_UNAVAILABLE, "the egress evidence file cannot be read"
        ) from None
    if skipped:
        log.warning("egress evidence lines skipped", extra={"vm_id": vm_id, "skipped": skipped})
    return events


class JsonlEgressReader:
    def __init__(self, path: str, settle_seconds: float = 0.0) -> None:
        self.path = Path(path)
        self.settle_seconds = settle_seconds  # the collector's flush interval, if it has one

    async def __call__(self, vm_id: str) -> list[EgressEvent]:
        if self.settle_seconds:
            await asyncio.sleep(self.settle_seconds)
        return await asyncio.to_thread(read_events, self.path, vm_id)
