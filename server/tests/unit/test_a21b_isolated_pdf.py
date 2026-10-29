"""A-21b: a PDF is parsed in a resource-limited child process (#492)."""

import subprocess
import sys
import threading
import time
from typing import Any

import pytest
from pdfs import make_pdf
from sbam_contracts.api import ErrorCode
from sbam_workers.ingest import isolation
from sbam_workers.ingest.loaders import loader_for
from sbam_workers.ingest.loaders.base import ParseError, no_heartbeat
from sbam_workers.ingest.loaders.pdf import load_pdf
from test_a21_pdf_bombs import _pdf


def _fake_child(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """Replace the child's command with `python -c code`; its argv[1] is the result path."""
    real = subprocess.Popen

    def popen(argv: list[str], **kwargs: Any) -> "subprocess.Popen[bytes]":
        return real([sys.executable, "-c", code, argv[-1]], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)


def _parse(data: bytes = b"%PDF", **kwargs: Any) -> Any:
    return isolation.run_isolated("pdf", data, no_heartbeat, **kwargs)


def test_a21b_t01_isolated_result_equals_in_process_result() -> None:
    data = make_pdf(["first page of text, long enough to count", "", "third page of text, too"])
    inline, isolated = load_pdf(data, no_heartbeat), loader_for("pdf")(data, no_heartbeat)  # type: ignore[misc]
    assert [b.model_dump() for b in isolated.blocks] == [b.model_dump() for b in inline.blocks]
    assert isolated.markdown == inline.markdown and isolated.report == inline.report
    assert isolated.report.ocr_needed_pages == [2]


def test_a21b_t02_the_pdf_loader_is_the_isolated_one() -> None:
    assert loader_for("pdf").__name__ == "load_pdf_isolated"  # type: ignore[union-attr]


def test_a21b_t03_array_bomb_is_a_parse_error_not_a_crash() -> None:
    started = time.monotonic()
    with pytest.raises(ParseError) as err:
        _parse(_pdf("/Foo [" + "0 " * 7_000_000 + "]"))
    assert err.value.code == ErrorCode.CORRUPT
    assert time.monotonic() - started < isolation.WALL_SECONDS


def test_a21b_t04_content_stream_bomb_ends_inside_the_limits() -> None:
    data = _pdf(content=b"BT /F1 1 Tf " + b"0 0 Td (a) Tj " * 500_000 + b"ET")
    started = time.monotonic()
    parsed = _parse(data)
    assert parsed.report.warnings == ["page 1 could not be read"]
    assert time.monotonic() - started < isolation.WALL_SECONDS


def test_a21b_t05_wall_clock_deadline_kills_the_child() -> None:
    started = time.monotonic()
    with pytest.raises(ParseError) as err:
        _parse(_pdf(), wall_seconds=0.05)  # the interpreter alone takes longer than this
    assert err.value.code == ErrorCode.TIMEOUT
    assert time.monotonic() - started < 10


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", ErrorCode.PARSE_ERROR),
        ("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)", ErrorCode.PARSE_ERROR),
        ("import os, signal; os.kill(os.getpid(), signal.SIGXCPU)", ErrorCode.TIMEOUT),
        ("raise SystemExit(1)", ErrorCode.PARSE_ERROR),
        ("import sys; open(sys.argv[1], 'w').write('not json')", ErrorCode.PARSE_ERROR),
        ("import sys; open(sys.argv[1], 'w').write('{\"parsed\": {}}')", ErrorCode.PARSE_ERROR),
        (
            "import sys, json; open(sys.argv[1], 'w').write(json.dumps("
            "{'error': {'code': 'ENCRYPTED', 'message': 'locked'}}))",
            ErrorCode.ENCRYPTED,
        ),
        (
            "import sys, json; open(sys.argv[1], 'w').write(json.dumps("
            "{'error': {'code': 'NOT_A_CODE', 'message': 'x'}}))",
            ErrorCode.PARSE_ERROR,
        ),
    ],
)
def test_a21b_t06_every_way_the_child_can_end_is_a_parse_error(
    monkeypatch: pytest.MonkeyPatch, code: str, expected: ErrorCode
) -> None:
    _fake_child(monkeypatch, code)
    with pytest.raises(ParseError) as err:
        _parse()
    assert err.value.code == expected


def test_a21b_t07_the_child_never_sees_the_workers_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://sbam:hunter2@db/sbam")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    _fake_child(
        monkeypatch,
        "import os, sys, json; open(sys.argv[1], 'w').write(json.dumps("
        "{'error': {'code': 'PARSE_ERROR', 'message': ','.join(sorted(os.environ))}}))",
    )
    with pytest.raises(ParseError) as err:
        _parse()
    assert "DATABASE_URL" not in str(err.value) and "OPENROUTER" not in str(err.value)
    assert "PATH" in str(err.value)


def test_a21b_t08_a_tenant_holds_one_slot_and_other_tenants_are_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolation, "_global_slots", threading.BoundedSemaphore(2))
    release_first, second_in, other_in = threading.Event(), threading.Event(), threading.Event()

    def hold() -> None:
        with isolation.parse_slot("tenant-a", no_heartbeat):
            release_first.wait(5)

    def same_tenant() -> None:
        with isolation.parse_slot("tenant-a", no_heartbeat):
            second_in.set()

    def other_tenant() -> None:
        with isolation.parse_slot("tenant-b", no_heartbeat):
            other_in.set()

    first = threading.Thread(target=hold)
    first.start()
    time.sleep(0.2)
    waiting = threading.Thread(target=same_tenant)
    waiting.start()
    other = threading.Thread(target=other_tenant)
    other.start()
    assert other_in.wait(2)  # tenant B is served while tenant A holds its slot
    assert not second_in.wait(0.5)  # tenant A's second file waits
    release_first.set()
    assert second_in.wait(10)  # and goes on as soon as the first is done
    for thread in (first, waiting, other):
        thread.join(5)


def test_a21b_t09_a_failed_parse_releases_its_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_child(monkeypatch, "raise SystemExit(1)")
    token = isolation.current_tenant.set("tenant-z")
    try:
        for _ in range(3):  # would deadlock on the third if a slot leaked
            with pytest.raises(ParseError):
                _parse()
    finally:
        isolation.current_tenant.reset(token)
    assert "tenant-z" not in isolation._tenants_busy
