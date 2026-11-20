"""`sbam_build_idle_seconds`: how long the quietest running Sub-Skill build has gone without a stage
change (#926). 0 while no build runs. The stuck-stage alert reads it (compose/grafana).

Exported over OTLP only while OTEL_EXPORTER_OTLP_ENDPOINT is set (the `obs` profile). The callback
runs one short indexed query on its own tiny pool, swallows every error (a gauge must never break
the API) and reports the last good reading, or 0, when the database cannot be reached. It carries
no id and no tenant: one number.
"""

import logging
import threading
from collections.abc import Callable, Iterable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from sbam_api.core.settings import Settings
from sbam_api.db.session import make_engine

log = logging.getLogger("sbam_api.build_metrics")
_started = False
_engine: Engine | None = None
_lock = threading.Lock()
_last = 0.0


def read(session: Session) -> float:
    # imported here: the temporal package imports the auth module, which imports `core`
    from sbam_api.temporal.repository import longest_idle_build_seconds

    return longest_idle_build_seconds(session)


def _observe(settings: Settings) -> Callable[[CallbackOptions], Iterable[Observation]]:
    def observe(_: CallbackOptions) -> Iterable[Observation]:
        global _engine, _last
        try:
            with _lock:
                if _engine is None:
                    _engine = make_engine(settings.database_url, pool_size=1, max_overflow=0)
                engine = _engine
            with Session(engine) as session:
                _last = read(session)
        except Exception:  # a gauge never breaks the API; the last reading stands
            log.warning("build idle gauge could not read the database", exc_info=True)
        yield Observation(_last)

    return observe


def start(settings: Settings) -> None:
    """Register the gauge once per process; it exports through the provider that
    `bulkhead_metrics.start` (called first) installed, and is a no-op without an endpoint."""
    global _started
    if _started or not settings.otel_exporter_otlp_endpoint:
        return
    _started = True
    metrics.get_meter("sbam_api.build").create_observable_gauge(
        "sbam_build_idle_seconds",
        callbacks=[_observe(settings)],
        description="Seconds the quietest running Sub-Skill build has gone without a stage change",
    )
