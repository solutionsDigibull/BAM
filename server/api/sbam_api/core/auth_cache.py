"""A short cache of what the database says about a signed-in session (audit A-12).

Every authenticated request asks the database two things: which projects the user is assigned to
and which portal roles SBAM still grants (A-04). Both answers are cached per `(user, session)` for
`ttl` seconds when `AUTH_STATE_CACHE_SECONDS` is above 0. It is off by default because it trades
freshness: A-04 and E3.x promise that a role taken away or a project unassigned is gone on the
*next* request, and a cache keeps the old answer for up to `ttl` seconds, in every API replica that
cached it. Turn it on only where a few seconds of staleness is acceptable.

What it never does:
  * cache a failure (a deactivated user, a suspended tenant, an unknown tenant raise before `put`);
  * outlive a new login: the Keycloak `sid` is part of the key, so a new session misses;
  * keep a session the caller says is gone: `invalidate(sub)` drops every entry of a user, which is
    what a logout, a deactivation or a role change should call.
"""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

MAX_ENTRIES = 10_000


@dataclass(frozen=True)
class AuthState:
    assigned_project_ids: frozenset[uuid.UUID]
    granted_roles: frozenset[str] | None


class AuthStateCache:
    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], tuple[float, AuthState]] = {}

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, sub: str, sid: str | None) -> AuthState | None:
        if not self.enabled or sid is None:
            return None
        with self._lock:
            entry = self._entries.get((sub, sid))
            if entry is None:
                return None
            expires, state = entry
            if self._clock() >= expires:
                del self._entries[(sub, sid)]
                return None
            return state

    def put(self, sub: str, sid: str | None, state: AuthState) -> None:
        if not self.enabled or sid is None:
            return
        with self._lock:
            if len(self._entries) >= MAX_ENTRIES:
                self._entries.clear()  # a bound, not a policy: the next requests refill it
            self._entries[(sub, sid)] = (self._clock() + self.ttl, state)

    def invalidate(self, sub: str) -> None:
        with self._lock:
            for key in [k for k in self._entries if k[0] == sub]:
                del self._entries[key]
