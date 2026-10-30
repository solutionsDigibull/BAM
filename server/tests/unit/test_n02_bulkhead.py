"""N-02 (#430): a bulkhead caps the threads one dependency may hold, and fails fast past the cap."""

import threading

import pytest
from sbam_api.core.bulkhead import REGISTRY, Bulkhead, BulkheadFullError


def test_n02_t01_calls_up_to_the_limit_run_and_the_next_is_refused_at_once() -> None:
    bulkhead = Bulkhead("dep", limit=2, wait=0.05)
    with bulkhead.slot(), bulkhead.slot():
        assert bulkhead.in_use == 2 and bulkhead.saturated
        with pytest.raises(BulkheadFullError), bulkhead.slot():
            raise AssertionError("a third call must not get a slot")
    assert bulkhead.in_use == 0 and not bulkhead.saturated


def test_n02_t02_a_slot_is_given_back_when_the_call_raises() -> None:
    bulkhead = Bulkhead("dep", limit=1, wait=0.05)
    with pytest.raises(RuntimeError), bulkhead.slot():
        raise RuntimeError("the dependency failed")
    with bulkhead.slot():  # not leaked
        assert bulkhead.in_use == 1


def test_n02_t03_a_waiting_call_gets_the_slot_that_frees_within_the_wait() -> None:
    bulkhead = Bulkhead("dep", limit=1, wait=2.0)
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        with bulkhead.slot():
            held.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(5)
    threading.Timer(0.1, release.set).start()
    with bulkhead.slot():  # waits for the holder, which lets go after 0.1 s
        assert bulkhead.in_use == 1
    thread.join(5)


def test_n02_t04_the_request_path_dependencies_are_sized_under_the_thread_pool() -> None:
    assert {b.name for b in REGISTRY} == {"clamd", "keycloak_admin"}
    assert all(0 < b.limit < 40 and b.wait < 2 for b in REGISTRY)  # anyio's pool is 40 threads
