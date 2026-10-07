"""JSON-lines logging with request context and secret redaction."""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)  # set by auth, #48
project_id_var: ContextVar[str | None] = ContextVar("project_id", default=None)

_EXTRA_FIELDS = ("method", "route", "status", "duration_ms", "exc_type", "exc_at", "check")
_SECRET_VALUE = re.compile(
    r"(?i)\b(authorization|password|passwd|secret|token|api[_-]?key)\b(\s*[=:]\s*)(\"[^\"]*\"|\S+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")


def redact(text: str) -> str:
    text = _BEARER.sub("Bearer [REDACTED]", text)
    return _SECRET_VALUE.sub(r"\1\2[REDACTED]", text)


class JsonFormatter(logging.Formatter):
    """One JSON object per line. json.dumps escapes newlines, so a value can't forge extra lines.

    Tracebacks are never written (exc_info is ignored): they can carry secrets from exception
    messages. Callers log `exc_type` / `exc_at` instead; full traces go to OTEL (#35).
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "correlation_id": correlation_id_var.get(),
            "tenant_id": tenant_id_var.get(),
            "project_id": project_id_var.get(),
        }
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Our middleware writes the access line (with correlation id); uvicorn's would duplicate it.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
