"""LLM spend limits (C-41 opt-in, #420): what a call costs and whether the next one may run.

Shared by the wrapper (`LlmClient`) and the Cognee meter, so both count with the same rules:

- `LLM_PROJECT_BUDGET_USD` / `LLM_TENANT_BUDGET_USD` refuse the next model call, non-retryably,
  once the recorded spend (`llm_calls.cost_usd`, failed attempts included) reaches them. The check
  runs before a call, so concurrent calls can overshoot by what is in flight (see ADR 0015).
- A budget needs a cost to count. Bifrost's own figure wins; else `LLM_PRICE_USD_PER_MTOK` for the
  alias. A call that used tokens and reports neither would be counted as free, so the alias is
  refused from then on (per process) and the reason is logged once. A free model is declared with
  a zero price.
"""

import asyncio
import logging
from decimal import Decimal

from sbam_workers.llm.store import CallStore, LlmScope
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.llm")
MILLION = Decimal(1_000_000)


class BudgetExceeded(Exception):  # noqa: N818
    """Recorded spend reached a configured budget. Retrying cannot help until it is raised."""

    retryable = False


class Budget:
    def __init__(self, settings: WorkerSettings, store: CallStore) -> None:
        self.settings = settings
        self.store = store
        # Aliases whose calls used tokens but came back with no cost and no configured price while
        # a budget is set: their spend cannot be counted, so the budget refuses them.
        self.blind: set[str] = set()

    @property
    def active(self) -> bool:
        return (
            self.settings.llm_project_budget_usd is not None
            or self.settings.llm_tenant_budget_usd is not None
        )

    def cost_of(self, alias: str, tokens_in: int, tokens_out: int, reported: Decimal) -> Decimal:
        """Bifrost's figure, else (budget set) the configured price for the alias, else 0."""
        price = self.settings.llm_price_usd_per_mtok.get(alias)
        if reported > 0 or price is None or not self.active:
            return reported
        return (tokens_in * price["in"] + tokens_out * price["out"]) / MILLION

    def note(self, alias: str, cost: Decimal, tokens_in: int, tokens_out: int) -> None:
        """Call after a model call: remember an alias whose spend this budget cannot see."""
        priced = alias in self.settings.llm_price_usd_per_mtok  # even {"in": 0, "out": 0}: declared
        if not self.active or priced or cost > 0 or not (tokens_in or tokens_out):
            return
        if alias not in self.blind:
            log.error(
                "llm budget is set but Bifrost reported no cost for %s and no price is configured "
                "for it (LLM_PRICE_USD_PER_MTOK); this alias is refused from now on. A free "
                'model: {"in": 0, "out": 0}',
                alias,
            )
        self.blind.add(alias)

    async def check(self, scope: LlmScope, alias: str) -> None:
        if self.active and alias in self.blind:
            raise BudgetExceeded(
                f"{alias} calls report no cost and LLM_PRICE_USD_PER_MTOK has no price for it, "
                "so the LLM budget cannot count them"
            )
        for limit, tenant, what in (
            (self.settings.llm_project_budget_usd, False, "project"),
            (self.settings.llm_tenant_budget_usd, True, "tenant"),
        ):
            if limit is None:
                continue
            spent = await asyncio.to_thread(self.store.spent, scope, tenant=tenant)
            if spent >= limit:
                raise BudgetExceeded(
                    f"the {what} has spent ${spent:.4f} of its ${limit:.4f} LLM budget"
                )
