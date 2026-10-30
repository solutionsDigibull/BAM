"""N-02 (#430): a hung clamd must not take the API with it.

A listener that accepts and never answers stands in for a hung clamd (the audit's PoC used 45
parallel imports against one). While real scans hold every clamd slot, a further import is 503
SCAN_UNAVAILABLE at once, without touching clamd, and another user's `GET /me` stays fast.

The requests themselves are sent one at a time: the API tests share one in-memory SQLite connection,
which cannot take concurrent requests. What the bulkhead protects is the thread each hung call would
hold, and that is what the held slots stand for.
"""

import contextlib
import io
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import test_projects
from sbam_api.core.bulkhead import CLAMD
from sbam_api.scan.clamd import ClamdClient, ClamdError
from test_auth import problem
from test_masters_api import engine_with_masters
from test_masters_import import ImportHarness, workbook


@contextlib.contextmanager
def hung_clamd() -> Iterator[tuple[str, int]]:
    """Accepts connections and never replies: what a wedged clamd looks like."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(64)
    held: list[socket.socket] = []
    running = True

    def accept() -> None:
        while running:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            held.append(conn)

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield "127.0.0.1", server.getsockname()[1]
    finally:
        running = False
        server.close()
        for conn in held:
            conn.close()


def hung_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str, port: int, timeout: float
) -> ImportHarness:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_masters)
    h = ImportHarness(tmp_path)
    state = h.client.app.state  # type: ignore[attr-defined]
    state.settings = state.settings.model_copy(
        update={"clamav_host": host, "clamav_port": port, "clamav_timeout": timeout}
    )
    return h


def test_n02_t05_with_clamd_hung_imports_fail_fast_and_other_requests_stay_quick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holders: list[threading.Thread] = []
    with hung_clamd() as (host, port):
        h = hung_harness(tmp_path, monkeypatch, host, port, timeout=30.0)
        token = h.admin_a()
        assert h.call("GET", "/me", token).status_code == 200  # signed in before the hang

        def stuck_scan() -> None:
            # A real call into the hung clamd, as an import would make. It holds its slot until the
            # listener closes at the end of the test (a long timeout, so it outlasts every import).
            with contextlib.suppress(ClamdError), CLAMD.slot():
                ClamdClient(host, port, 60.0).scan(io.BytesIO(b"x"))

        holders.extend(threading.Thread(target=stuck_scan) for _ in range(CLAMD.limit))
        for thread in holders:
            thread.start()
        deadline = time.monotonic() + 3
        while not CLAMD.saturated and time.monotonic() < deadline:
            time.sleep(0.01)
        assert CLAMD.saturated  # clamd holds every slot it may

        upload = workbook()
        for _ in range(12):  # the PoC's imports: each fails fast instead of waiting on clamd
            before = time.monotonic()
            problem(h.upload(upload, token=token), 503, "SCAN_UNAVAILABLE")
            assert time.monotonic() - before < 10  # a scan would take 30 s
        durations = []
        for _ in range(10):  # and another user's request is not held up behind them
            before = time.monotonic()
            assert h.call("GET", "/me", token).status_code == 200
            durations.append(time.monotonic() - before)
        assert sorted(durations)[-1] < 1.0
    for thread in holders:  # the listener is closed: the stuck scans fail and give their slots back
        thread.join(10)
    assert CLAMD.in_use == 0  # every slot came back


def test_n02_t06_the_readiness_body_says_which_dependency_is_saturated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with hung_clamd() as (host, port):
        h = hung_harness(tmp_path, monkeypatch, host, port, timeout=30.0)
        with contextlib.ExitStack() as stack:
            for _ in range(CLAMD.limit):
                stack.enter_context(CLAMD.slot())
            before = time.monotonic()
            problem(h.upload(workbook(), token=h.admin_a()), 503, "SCAN_UNAVAILABLE")
            assert time.monotonic() - before < 10  # no scan was even tried (it would take 30 s)
            assert h.client.get("/health/ready").json()["bulkheads"]["clamd"] == "saturated"
    assert h.client.get("/health/ready").json()["bulkheads"]["clamd"] == "ok"
