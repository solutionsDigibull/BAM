"""Langfuse traces over its REST ingestion API (E6.1, TD §17).

No SDK: one `trace-create` and one `generation-create` event per attempt, sent with `httpx`. Tracing
never decides the outcome of a call: a Langfuse that is down or slow only costs a warning and a
counter
(`llm_trace_export_failures_total`), the batch is kept in a small buffer and goes out with the next
call,
and `llm_calls` is written regardless. The trace id is made here, so every call has one even with
Langfuse unset or unreachable.

Redaction: prompts and answers hold client documents, so by default only their size and hash are
exported. `LANGFUSE_LOG_CONTENT=true` (development) sends the text.
"""

import hashlib
import logging
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from opentelemetry import metrics

from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.llm.trace")
BUFFER_MAX = 200  # events kept while Langfuse is unreachable; the oldest are dropped first
EXPORT_TIMEOUT_SECONDS = 3.0

_failures = metrics.get_meter("sbam_workers.llm").create_counter(
    "llm_trace_export_failures_total", description="Langfuse exports that failed (E6.1)"
)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def _redact(value: Any, *, log_content: bool) -> Any:
    if log_content:
        return value
    text = value if isinstance(value, str) else repr(value)
    return {
        "redacted": True,
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


@dataclass(frozen=True)
class Generation:
    """One model call attempt, as the trace sees it."""

    trace_id: str
    alias: str
    model: str
    prompt_id: str
    prompt_version: str
    tenant_id: str
    project_id: str
    attempt: int
    status: str
    started_at: datetime
    ended_at: datetime
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    input: Any = None
    output: Any = None
    error: str | None = None
    subject: Mapping[str, str] = field(default_factory=dict)  # e.g. the document version id


@dataclass
class LangfuseTracer:
    settings: WorkerSettings
    client: httpx.AsyncClient | None = None
    failures: int = 0  # exports that failed since start (also the counter above)
    _buffer: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=BUFFER_MAX))

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.langfuse_host and s.langfuse_public_key and s.langfuse_secret_key)

    def _events(self, g: Generation) -> list[dict[str, Any]]:
        keep = self.settings.langfuse_log_content
        now = datetime.now(UTC).isoformat()
        trace = {
            "id": str(uuid.uuid4()),
            "type": "trace-create",
            "timestamp": now,
            "body": {
                "id": g.trace_id,
                "name": f"{g.alias}/{g.prompt_id}",
                "tags": [
                    g.alias,
                    f"prompt:{g.prompt_id}@{g.prompt_version}",
                    f"tenant:{g.tenant_id}",
                ],
                "metadata": {"tenant_id": g.tenant_id, "project_id": g.project_id, **g.subject},
            },
        }
        generation = {
            "id": str(uuid.uuid4()),
            "type": "generation-create",
            "timestamp": now,
            "body": {
                "id": str(uuid.uuid4()),
                "traceId": g.trace_id,
                "name": f"attempt-{g.attempt}",
                "model": g.model,
                "startTime": g.started_at.isoformat(),
                "endTime": g.ended_at.isoformat(),
                "level": "DEFAULT" if g.status == "SUCCEEDED" else "WARNING",
                "statusMessage": g.error,
                "input": _redact(g.input, log_content=keep),
                "output": _redact(g.output, log_content=keep),
                "usage": {"input": g.tokens_in, "output": g.tokens_out, "unit": "TOKENS"},
                "metadata": {
                    "alias": g.alias,
                    "prompt_id": g.prompt_id,
                    "prompt_version": g.prompt_version,
                    "attempt": g.attempt,
                    "status": g.status,
                    "cost_usd": g.cost_usd,
                    **g.subject,
                },
            },
        }
        return [trace, generation]

    async def record(self, generation: Generation) -> None:
        """Send this attempt (and anything still buffered). Never raises."""
        if not self.enabled:
            return
        self._buffer.extend(self._events(generation))
        batch = list(self._buffer)
        try:
            await self._post(batch)
        except Exception as exc:  # tracing must never fail a call
            self.failures += 1
            _failures.add(1)
            log.warning(
                "langfuse export failed (%s); %d events buffered",
                type(exc).__name__,
                len(self._buffer),
            )
            return
        self._buffer.clear()

    async def _post(self, batch: list[dict[str, Any]]) -> None:
        s = self.settings
        assert s.langfuse_host and s.langfuse_secret_key  # noqa: S101 - guarded by `enabled`
        owns = self.client is None
        client = self.client or httpx.AsyncClient(timeout=EXPORT_TIMEOUT_SECONDS)
        try:
            response = await client.post(
                f"{str(s.langfuse_host).rstrip('/')}/api/public/ingestion",
                json={"batch": batch},
                auth=(s.langfuse_public_key or "", s.langfuse_secret_key.get_secret_value()),
                timeout=EXPORT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        finally:
            if owns:
                await client.aclose()
