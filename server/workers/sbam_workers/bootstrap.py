"""Worker entrypoint: `python -m sbam_workers.bootstrap --queues sbam-ingest[,sbam-llm,...]` (E1.7).

* `--queues` (default `$SBAM_QUEUES`) is a non-empty subset of the six task queues; anything else
  exits with code 2 and lists the valid queues (E1.7-T02). One process may serve several queues.
* Workflows and activities come from the registry (`@workflow_for` / `@activity_for`); feature
  modules register themselves, so this file is not edited to add one.
* Touches the heartbeat file every few seconds; the image healthcheck reads it.
* Preflight checks of the served queues (`@preflight_for`) run before Temporal is reached; a failure
  exits with code 3 and `health FAILED <code>`, so nothing is polled (E5.1-T04).
* `--reconcile-every SECONDS` (default `$SBAM_RECONCILE_EVERY`, 0 = off) runs the mirror reconciler.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError
from sbam_api.core.logging import configure_logging
from sbam_api.temporal.client import connect
from sbam_contracts.runs import TaskQueue
from temporalio.client import Client
from temporalio.worker import Worker

from sbam_workers import mirror, registry, telemetry
from sbam_workers.queues import QueueError, parse_queues
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.bootstrap")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sbam-worker", description="SBAM Temporal worker")
    parser.add_argument(
        "--queues",
        default=os.environ.get("SBAM_QUEUES"),
        help="comma-separated task queues, e.g. sbam-ingest,sbam-llm (default: $SBAM_QUEUES)",
    )
    parser.add_argument(
        "--reconcile-every",
        type=float,
        default=float(os.environ.get("SBAM_RECONCILE_EVERY", "0")),
        help="seconds between run-mirror reconciler passes; 0 = off",
    )
    return parser


# Deploy drain (E16.4-T07): on SIGTERM a worker stops polling and gives running activities this long
# to finish before cancelling them (a cancelled one is retried by the new worker; activities are
# idempotent). compose.deploy.yaml's stop_grace_period must be longer.
DRAIN = timedelta(seconds=120)


def build_workers(
    client: Client, queues: Sequence[TaskQueue], executor: ThreadPoolExecutor
) -> list[Worker]:
    """One Temporal worker per queue, serving whatever the registry holds for that queue."""
    registry.load_all()
    workers = []
    for queue in queues:
        workflows, activities = registry.collect(queue)
        workers.append(
            Worker(
                client,
                task_queue=queue.value,
                workflows=workflows,
                activities=activities,
                activity_executor=executor,  # sync activities (DB) run on threads
                graceful_shutdown_timeout=DRAIN,
            )
        )
    return workers


async def _heartbeat(path: Path, every: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        path.touch()
        await asyncio.sleep(every)


async def _reconcile_loop(client: Client, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            fixed = await mirror.reconcile(client)
            if fixed:
                log.info("reconciler repaired %d run mirror row(s)", fixed)
        except Exception as exc:  # a failed pass must never stop the worker
            log.warning("reconciler pass failed", extra={"exc_type": type(exc).__name__})


async def serve(
    settings: WorkerSettings,
    queues: Sequence[TaskQueue],
    reconcile_every: float = 0,
    stop: asyncio.Event | None = None,
) -> None:
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # Windows: no signal handlers on the loop
            loop.add_signal_handler(sig, stop.set)

    client = await connect(
        settings.temporal_host, settings.temporal_namespace, telemetry.tracer(settings)
    )
    with ThreadPoolExecutor(max_workers=16, thread_name_prefix="activity") as executor:
        async with contextlib.AsyncExitStack() as stack:
            for worker in build_workers(client, queues, executor):
                await stack.enter_async_context(worker)
            log.info("worker serving %s", ", ".join(q.value for q in queues))
            await registry.run_startup(client, queues)  # e.g. the PurgeDeleted schedule (E4.9)
            tasks = [
                asyncio.create_task(
                    _heartbeat(Path(settings.heartbeat_file), settings.heartbeat_seconds)
                )
            ]
            if reconcile_every > 0:
                tasks.append(asyncio.create_task(_reconcile_loop(client, reconcile_every)))
            try:
                await stop.wait()
            finally:
                for task in tasks:
                    task.cancel()
    log.info("worker stopped")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        queues = parse_queues(args.queues)
    except QueueError as exc:
        print(f"sbam-worker: {exc}", file=sys.stderr)
        return 2
    try:
        settings = WorkerSettings()  # type: ignore[call-arg]
    except ValidationError as exc:
        names = sorted({str(e["loc"][0]).upper() for e in exc.errors() if e["loc"]})
        print(f"sbam-worker: missing or invalid settings: {', '.join(names)}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    registry.load_all()
    try:
        registry.run_preflight(queues)
    except registry.PreflightError as exc:  # never polls; no heartbeat, so the healthcheck fails
        print(f"sbam-worker: health FAILED {exc}", file=sys.stderr)
        return 3
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(settings, queues, args.reconcile_every))
    return 0


if __name__ == "__main__":
    sys.exit(main())
