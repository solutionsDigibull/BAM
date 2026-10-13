"""Workflow/activity registry: modules register themselves, so `bootstrap.py` is edited once.

    @activity_for(TaskQueue.INGEST)
    @activity.defn(name="scan_file")
    async def scan_file(inp: ScanInput) -> ScanResult: ...

`load_all()` imports every module under `sbam_workers` so their decorators run; `collect(queue)`
returns what a worker on that queue must serve.
"""

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any, TypeVar

from sbam_contracts.runs import TaskQueue

import sbam_workers

T = TypeVar("T")

_WORKFLOWS: dict[TaskQueue, list[type]] = {q: [] for q in TaskQueue}
_ACTIVITIES: dict[TaskQueue, list[Callable[..., Any]]] = {q: [] for q in TaskQueue}
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


def load_all() -> None:
    """Import every `sbam_workers` module (recursively) so their decorators register."""
    for info in pkgutil.walk_packages(sbam_workers.__path__, "sbam_workers."):
        if info.name.rsplit(".", 1)[-1] not in _SKIP:
            importlib.import_module(info.name)


def collect(queue: TaskQueue) -> tuple[list[type], list[Callable[..., Any]]]:
    return list(_WORKFLOWS[queue]), list(_ACTIVITIES[queue])
