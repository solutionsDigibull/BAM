"""#676: Cognee's per-chunk date hints are replaced by a no-op, so no chunk runs `dateparser` on the
worker's event loop (which starved the activity heartbeat and timed every build attempt out).

Cognee's databases and Bifrost are not used. Only its chunk-task module is touched, and each test
puts back what it patched."""

import asyncio
import importlib
from types import ModuleType
from typing import Any

import cognee.modules.engine.operations.setup as cognee_setup
import pytest
from sbam_workers.knowledge import cognee_backend

CHUNK = cognee_backend.CHUNK_TASK_MODULE


def _real_hint_lines(text: str, base: Any) -> tuple[list[str], Any]:  # pragma: no cover
    raise AssertionError("the real hint_lines must not run")


def test_676_hint_lines_is_replaced_by_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module(CHUNK)
    monkeypatch.setattr(module, "hint_lines", _real_hint_lines)

    cognee_backend.disable_temporal_hints()

    assert module.hint_lines is cognee_backend._no_temporal_hints
    base = object()
    assert module.hint_lines("on 27 April 1947 the desk opened", base) == ([], base)


def test_676_dateparser_is_never_called_for_a_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    import cognee.modules.engine.utils.temporal_hints as hints

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("dateparser.search_dates must not run on a chunk")

    monkeypatch.setattr(hints, "search_dates", boom)
    module = importlib.import_module(CHUNK)
    monkeypatch.setattr(module, "hint_lines", hints.hint_lines)

    cognee_backend.disable_temporal_hints()

    assert module.hint_lines("later that spring, 12 May", None) == ([], None)


def test_676_a_missing_hint_lines_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda name: ModuleType(name))

    with pytest.raises(RuntimeError, match="no hint_lines to replace"):
        cognee_backend.disable_temporal_hints()


def test_676_setup_disables_the_hints_before_cognee_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    import cognee

    module = importlib.import_module(CHUNK)
    monkeypatch.setattr(module, "hint_lines", _real_hint_lines)
    monkeypatch.setattr(cognee_backend, "_setup_done", False)
    monkeypatch.setattr(cognee_backend, "_ready", None)
    monkeypatch.setattr(cognee.config, "set_llm_provider", lambda provider: None)
    seen: list[Any] = []

    async def setup() -> None:
        seen.append(module.hint_lines)  # what the chunk task would call from here on

    monkeypatch.setattr(cognee_setup, "setup", setup)

    asyncio.run(cognee_backend.CogneeLibrary()._setup())

    assert seen == [cognee_backend._no_temporal_hints]
