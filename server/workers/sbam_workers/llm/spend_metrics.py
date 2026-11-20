"""`sbam_llm_cost_usd_total{alias}`: model spend as the platform recorded it (#926).

One counter increment per recorded call attempt, with the same figure that goes into
`llm_calls.cost_usd` (Bifrost's, else the configured price; #853 makes Cognee's right). The
cost-over-threshold alert and the LLM dashboard read it. Exported over OTLP only while
OTEL_EXPORTER_OTLP_ENDPOINT is set (the `obs` profile); without it the counter is a no-op. It
carries the alias only: no tenant, project or build id.
"""

from decimal import Decimal

from opentelemetry import metrics
from opentelemetry.metrics import Counter

_counter: Counter | None = None


def record(alias: str, cost_usd: Decimal | float | int) -> None:
    global _counter
    amount = float(cost_usd)
    if amount <= 0:
        return
    if _counter is None:
        _counter = metrics.get_meter("sbam_workers.llm").create_counter(
            "sbam_llm_cost_usd", description="Model spend recorded in llm_calls, in USD"
        )
    _counter.add(amount, {"alias": alias})
