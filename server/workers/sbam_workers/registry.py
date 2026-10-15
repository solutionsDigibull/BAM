"""Workflow/activity registry: modules register themselves, so `bootstrap.py` is edited once.

    @activity_for(TaskQueue.INGEST)
    @activity.defn(name="scan_file")
    async def scan_file(inp: ScanInput) -> ScanResult: ...

`load_all()` imports every module under `sbam_workers` so their decorators run; `collect(queue)`
returns what a worker on that queue must serve.

A module that needs something set up in Temporal when its worker starts (a Schedule, E4.9)
registers a startup hook: `@startup_for(TaskQueue.INGEST)` on `async def f(client: Client) -> None`.
A failing hook is logged and never stops the worker; it runs again on the next start, so it must be
idempotent.
"""

import importlib
import logging
import pkgutil
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, TypeVar

from sbam_contracts.runs import TaskQueue
from temporalio.client import Client

import sbam_workers

T = TypeVar("T")

_WORKFLOWS: dict[TaskQueue, list[type]] = {q: [] for q in TaskQueue}
_ACTIVITIES: dict[TaskQueue, list[Callable[..., Any]]] = {q: [] for q in TaskQueue}
_STARTUP: dict[TaskQueue, list[Callable[[Client], Awaitable[None]]]] = {q: [] for q in TaskQueue}
log = logging.getLogger("sbam_workers.registry")
_SKIP = {"bootstrap", "registry", "settings", "queues", "telemetry"}


def _queues(queues: tuple[TaskQueue, ...]) -> tuple[TaskQueue, ...]:
    if not queues:
        raise ValueError("register on at least one task queue")
    return queues


def _key(obj: Any) -> tuple[str, str]:
    return obj.__module__, obj.__qualname__


def workflow_for(*queues: TaskQueue) -> Callable[[type[T]], type[T]]:
    queues = _queues(queues)

    def register(cls: type[T]) -> type[T]:
        for q in queues:
            # Temporal's sandbox re-imports workflow modules, which re-runs this decorator with a
            # copy of the class: keep the first registration of each (module, name).
            if all(_key(c) != _key(cls) for c in _WORKFLOWS[q]):
                _WORKFLOWS[q].append(cls)
        return cls

    return register


def activity_for(*queues: TaskQueue) -> Callable[[Callable[..., T]], Callable[..., T]]:
    queues = _queues(queues)

    def register(fn: Callable[..., T]) -> Callable[..., T]:
        for q in queues:
            if all(_key(f) != _key(fn) for f in _ACTIVITIES[q]):
                _ACTIVITIES[q].append(fn)
        return fn

    return register


def startup_for(
    *queues: TaskQueue,
) -> Callable[[Callable[[Client], Awaitable[None]]], Callable[[Client], Awaitable[None]]]:
    queues = _queues(queues)

    def register(fn: Callable[[Client], Awaitable[None]]) -> Callable[[Client], Awaitable[None]]:
        for q in queues:
            if all(_key(f) != _key(fn) for f in _STARTUP[q]):
                _STARTUP[q].append(fn)
        return fn

    return register


async def run_startup(client: Client, queues: Iterable[TaskQueue]) -> None:
    """Run the startup hooks of the served queues (once each, even when on several queues)."""
    done: set[tuple[str, str]] = set()
    for queue in queues:
        for hook in _STARTUP[queue]:
            if _key(hook) in done:
                continue
            done.add(_key(hook))
            try:
                await hook(client)
            except Exception as exc:  # e.g. Temporal not ready yet: the next start retries
                log.warning(
                    "startup hook %s failed",
                    hook.__qualname__,
                    extra={"exc_type": type(exc).__name__},
                )


def load_all() -> None:
    """Import every `sbam_workers` module (recursively) so their decorators register."""
    for info in pkgutil.walk_packages(sbam_workers.__path__, "sbam_workers."):
        if info.name.rsplit(".", 1)[-1] not in _SKIP:
            importlib.import_module(info.name)


def collect(queue: TaskQueue) -> tuple[list[type], list[Callable[..., Any]]]:
    return list(_WORKFLOWS[queue]), list(_ACTIVITIES[queue])
