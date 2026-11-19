"""A bulkhead per blocking dependency (audit N-02).

Sync handlers run in a fixed thread pool. clamd, Keycloak's admin API and the like can hang for many
seconds, and enough parallel calls to a hung one would take every thread, so an unrelated
`GET /me` waits behind them while `/health/live` still answers 200. A `Bulkhead` caps how many
threads one dependency may hold. A call that cannot get a slot within `wait` seconds fails fast with
`BulkheadFullError`; the caller turns it into its own 503 (`SCAN_UNAVAILABLE`, `IDP_UNAVAILABLE`).
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sbam_api.core.settings import Settings


class BulkheadFullError(Exception):
    """Every slot of the dependency is busy and none freed up in time. Retryable by the client."""


class Bulkhead:
    def __init__(self, name: str, limit: int, wait: float = 0.5) -> None:
        self.name, self.limit, self.wait = name, limit, wait
        self._slots = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self._in_use = 0

    def resize(self, limit: int, wait: float) -> None:
        """New limit and wait. Replaces the semaphore, so call it at startup before traffic: a slot
        held across the call would release into the new one."""
        self.limit, self.wait = limit, wait
        self._slots = threading.BoundedSemaphore(limit)

    @contextmanager
    def slot(self) -> Iterator[None]:
        if not self._slots.acquire(timeout=self.wait):
            raise BulkheadFullError(self.name)
        with self._lock:
            self._in_use += 1
        try:
            yield
        finally:
            with self._lock:
                self._in_use -= 1
            self._slots.release()

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def saturated(self) -> bool:
        return self._in_use >= self.limit


# The request-path dependencies. Sized well under the 40-thread pool, so one hung dependency
# can never take it all.
CLAMD = Bulkhead("clamd", limit=8)
KEYCLOAK_ADMIN = Bulkhead("keycloak_admin", limit=8)
KEYCLOAK_JWKS = Bulkhead("keycloak_jwks", limit=8)  # the JWKS fetch on a cold cache or unknown kid
REGISTRY: tuple[Bulkhead, ...] = (CLAMD, KEYCLOAK_ADMIN, KEYCLOAK_JWKS)


def configure(settings: Settings) -> None:
    """Apply the sizing settings to the registered bulkheads (called once at startup)."""
    wait = settings.bulkhead_wait_seconds
    CLAMD.resize(settings.bulkhead_clamd_limit, wait)
    KEYCLOAK_ADMIN.resize(settings.bulkhead_keycloak_admin_limit, wait)
    KEYCLOAK_JWKS.resize(settings.bulkhead_keycloak_jwks_limit, wait)
