"""Structured log fields: the ones workers pass as `extra=` reach the line, secrets do not."""

import json
import logging

from sbam_api.core.logging import JsonFormatter


def line(**extra: object) -> dict[str, object]:
    record = logging.LogRecord("t", logging.WARNING, __file__, 1, "msg", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    out: dict[str, object] = json.loads(JsonFormatter().format(record))
    return out


def test_worker_fields_are_logged() -> None:
    out = line(project_id="p1", sow_version_id="v1", type="LLM_REJECTED", entities=313, chunks=156)
    assert out["project_id"] == "p1" and out["sow_version_id"] == "v1"
    assert out["type"] == "LLM_REJECTED" and out["entities"] == 313 and out["chunks"] == 156


def test_unknown_fields_are_still_dropped() -> None:
    assert "document_text" not in line(document_text="the customer's price list")


def test_a_secret_in_a_logged_field_is_redacted() -> None:
    out = line(cause="Bifrost refused (400) Authorization: Bearer sk-live-123")
    assert "sk-live-123" not in json.dumps(out)
