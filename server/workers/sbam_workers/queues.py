"""`--queues` parsing: a comma list that must be a non-empty subset of the six task queues."""

from sbam_contracts.runs import TaskQueue

VALID = ", ".join(q.value for q in TaskQueue)


class QueueError(ValueError):
    """Bad `--queues` value. The message always lists the valid queues (E1.7-T02)."""


def parse_queues(text: str | None) -> list[TaskQueue]:
    """`"sbam-llm, sbam-build"` -> [LLM, BUILD]. Order kept, duplicates dropped."""
    names = [n.strip() for n in (text or "").split(",") if n.strip()]
    if not names:
        raise QueueError(f"no task queue given; valid queues: {VALID}")
    unknown = [n for n in names if n not in {q.value for q in TaskQueue}]
    if unknown:
        raise QueueError(f"unknown task queue(s) {', '.join(unknown)}; valid queues: {VALID}")
    return list(dict.fromkeys(TaskQueue(n) for n in names))
