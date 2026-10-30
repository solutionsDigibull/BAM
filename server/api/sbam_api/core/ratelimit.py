"""Per-user request budgets and an audit-write throttle (A-08, #434).

Two sliding-window budgets, checked after the token is verified (a forged token can never spend
someone else's budget): one for every write (POST, PUT, PATCH, DELETE) and a much smaller one
for the routes that start LLM work (`LLM_ROUTE`). Past a budget the answer is 429 `RATE_LIMITED`
with `Retry-After`. Reads are not limited here (the knowledge graph has its own limiter).

State is in this process, so with N API replicas the real limit would be N times the setting. When
`SBAM_API_REPLICAS` is above 1 the same budgets are also kept in PostgreSQL (`SharedBudget`, an
unlogged fixed-window counter per user and minute, no Redis): the configured number is then the
limit for the whole cluster. If that table cannot be reached the request is judged by the
in-process window alone (a budget must not take the API down). A limit of 0 turns that budget off.

`AuditThrottle` is the same idea for `access_denied` rows: a caller who keeps probing foreign ids
gets the first `per_minute` rows per minute written, and the rest only logged (the structured log
line is always written), so denials cannot be turned into an audit-table flood.
"""

import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Final

from sbam_contracts.api import ErrorCode
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.conventions import ApiError
from sbam_api.db.rate_windows import count_hit, sweep

log = logging.getLogger("sbam_api.ratelimit")

WINDOW_SECONDS: Final = 60.0
SWEEP_EVERY: Final = 256  # a shared hit in this many also sweeps: no scheduler, no hot-path cost
MAX_KEYS: Final = 50_000  # one deque per caller: bound the table, drop the idlest when full
WRITE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Routes that start LLM work (a regenerate, a rebuild, a retry that re-reads a page by the model, a
# new pipeline run): each costs money, so they get their own, smaller budget.
LLM_ROUTE: Final = re.compile(
    r"/(sow/regenerate|pipeline/start|knowledge/rebuild|documents/[^/]+/retry"
    r"|(macro|sub)-skills/[^/]+/(regenerate|retry))$"
)


class SlidingWindow:
    """At most `limit` hits per `window` seconds per key."""

    def __init__(self, limit: int, window: float = WINDOW_SECONDS) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float) -> float:
        """Record a hit. Returns 0 when allowed, else the seconds until the next hit would be."""
        if self.limit <= 0:
            return 0.0
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= MAX_KEYS:
                    self._evict(now)
                hits = self._hits[key] = deque()
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(hits[0] + self.window - now, 0.001)
            hits.append(now)
            return 0.0

    def _evict(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]:
            del self._hits[key]
        while len(self._hits) >= MAX_KEYS:  # still full of live callers: drop the oldest-inserted
            del self._hits[next(iter(self._hits))]


class SharedBudget:
    """The same two budgets counted in PostgreSQL, so N replicas share one limit.

    A fixed window per minute (a burst across a minute boundary can reach twice the limit, which
    the in-process sliding window, applied first, trims to its own limit per replica). `hit`
    returns 0 when allowed, else the seconds until the window ends.
    """

    def __init__(
        self,
        writes_per_minute: int,
        llm_per_minute: int,
        sessions: Callable[[], sessionmaker[Session]],
    ) -> None:
        self.limits = {"w": writes_per_minute, "l": llm_per_minute}
        self.sessions = sessions
        self._calls = 0
        self._last_warning = 0.0

    def hit(self, kind: str, user_id: str) -> float:
        limit = self.limits[kind]
        if limit <= 0:
            return 0.0
        self._calls += 1  # a racy count only moves when the sweep runs
        try:
            with self.sessions()() as session:
                hits, left = count_hit(session, f"{kind}:{user_id}")
                if self._calls % SWEEP_EVERY == 0:
                    sweep(session)
                session.commit()
        except SQLAlchemyError:
            now = time.monotonic()
            if now - self._last_warning > 60:  # one line a minute, not one per request
                self._last_warning = now
                log.warning("shared rate-limit table unavailable: judging by this process alone")
            return 0.0
        return float(left) if hits > limit else 0.0


class RateLimiter:
    def __init__(
        self,
        writes_per_minute: int,
        llm_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        shared: SharedBudget | None = None,
    ) -> None:
        self.writes = SlidingWindow(writes_per_minute)
        self.llm = SlidingWindow(llm_per_minute)
        self.clock = clock
        self.shared = shared

    def check(self, user_id: str, method: str, path: str) -> None:
        if method not in WRITE_METHODS:
            return
        now = self.clock()
        llm = bool(LLM_ROUTE.search(path))
        wait = self.writes.hit(user_id, now)
        if not wait and llm:
            wait = self.llm.hit(user_id, now)
        if not wait and self.shared is not None:  # after the local windows: they are free
            wait = self.shared.hit("w", user_id) or (self.shared.hit("l", user_id) if llm else 0.0)
        if wait:
            seconds = str(math.ceil(wait))
            raise ApiError(
                429,
                ErrorCode.RATE_LIMITED,
                "Too many requests",
                f"Too many requests. Try again in {seconds} s.",
                headers={"Retry-After": seconds},
            )


class AuditThrottle:
    """Allow the first `per_minute` audit rows per (actor, minute); drop the rest."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.time) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self._counts: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    def allow(self, actor: str) -> bool:
        if self.per_minute <= 0:
            return True
        minute = int(self.clock() // 60)
        with self._lock:
            if len(self._counts) >= MAX_KEYS:
                self._counts = {k: v for k, v in self._counts.items() if k[1] == minute}
                if len(self._counts) >= MAX_KEYS:
                    return False  # a flood of distinct actors in one minute: stop writing
            count = self._counts.get((actor, minute), 0)
            if count >= self.per_minute:
                return False
            self._counts[(actor, minute)] = count + 1
            return True
