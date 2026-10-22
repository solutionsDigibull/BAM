"""Which SubSkillBuild children to start next (E10.3). Pure and deterministic: workflow code.

At most `limit` builds run at once; a Sub-Skill starts only after every upstream it depends on is
CERTIFIED. Ties are broken by the sorted skill id, so a replay starts children in the same order.
A failed upstream (NEEDS_FDE_REVIEW) blocks only its dependents; the rest keep building.
"""

from collections.abc import Iterable, Mapping
from uuid import UUID


class BuildScheduler:
    def __init__(
        self,
        targets: Iterable[UUID],
        depends_on: Mapping[UUID, Iterable[UUID]],
        limit: int,
        certified: Iterable[UUID],
    ) -> None:
        self.limit = limit
        self.queued = sorted(set(targets), key=str)
        self.deps = {s: set(depends_on.get(s, ())) for s in self.queued}
        self.certified = set(certified)
        self.running: list[UUID] = []
        self.failed: list[UUID] = []
        self.blocked: list[UUID] = []

    def start_ready(self) -> list[UUID]:
        """Move the Sub-Skills that can start now to running and return them, in start order."""
        self._block_unreachable()
        starts = [s for s in self.queued if self.deps[s] <= self.certified]
        starts = starts[: max(self.limit - len(self.running), 0)]
        self.queued = [s for s in self.queued if s not in starts]
        self.running += starts
        return starts

    def finished(self, sub_skill_id: UUID, certified: bool) -> None:
        self.running.remove(sub_skill_id)
        if certified:
            self.certified.add(sub_skill_id)
        else:
            self.failed.append(sub_skill_id)

    def needs_review(self) -> list[UUID]:
        """Everything not certified: failed, blocked, and anything never started (a cancel)."""
        return self.failed + self.blocked + self.queued

    def _block_unreachable(self) -> None:
        """Block every queued Sub-Skill with an upstream that won't be certified in this round:
        failed, blocked, or neither certified nor part of this round. Repeats until stable so a
        whole chain of dependents is blocked at once."""
        while True:
            pending = set(self.queued) | set(self.running)
            stuck = [
                s
                for s in self.queued
                if any(u not in self.certified and u not in pending for u in self.deps[s])
            ]
            if not stuck:
                return
            self.blocked += stuck
            self.queued = [s for s in self.queued if s not in stuck]
