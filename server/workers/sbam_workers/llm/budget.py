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

from sbam_contracts.budget import (
    BUILD_PREFIX,
    KNOWLEDGE_ALIASES,
    BudgetCaps,
    BudgetLevel,
    BudgetScope,
)
from sbam_contracts.notifications import NotificationType

from sbam_workers.llm.store import CallStore, LlmScope
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.llm")
MILLION = Decimal(1_000_000)
RANK = {level: n for n, level in enumerate(BudgetLevel)}  # OK < FIRST < SECOND < STOPPED
PERCENT = {BudgetLevel.FIRST_ALERT: "first", BudgetLevel.SECOND_ALERT: "second"}


class BudgetExceeded(Exception):  # noqa: N818
    """Recorded spend or tokens reached a budget. Retrying cannot help until it is raised."""

    retryable = False


STOP_REASON = "token budget reached"  # what a stopped build shows (NEEDS_FDE_REVIEW)


class Budget:
    def __init__(
        self, settings: WorkerSettings, store: CallStore, caps: BudgetCaps | None = None
    ) -> None:
        self.settings = settings
        self.store = store
        # An explicit `caps` wins (tests, a fixed deployment); else each check reads the project's
        # own row, falling back to the platform defaults (#776).
        self.caps = caps
        # Aliases whose calls used tokens but came back with no cost and no configured price while
        # a budget is set: their spend cannot be counted, so the budget refuses them.
        self.blind: set[str] = set()

    @property
    def active(self) -> bool:
        return (
            self.settings.llm_project_budget_usd is not None
            or self.settings.llm_tenant_budget_usd is not None
            or self.settings.llm_build_budget_usd is not None
        )

    def cost_of(self, alias: str, tokens_in: int, tokens_out: int, reported: Decimal) -> Decimal:
        """Bifrost's figure, else the configured price for the alias, else 0."""
        price = self.settings.llm_price_usd_per_mtok.get(alias)
        if reported > 0 or price is None:
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
        build_cap = self.settings.llm_build_budget_usd
        if build_cap is not None and scope.temporal_workflow_id.startswith(BUILD_PREFIX):
            built = await asyncio.to_thread(self.store.spent_build, scope)
            if built >= build_cap:
                raise BudgetExceeded(
                    f"this build has spent ${built:.4f} of its ${build_cap:.4f} per-build budget"
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

    async def check_tokens(self, scope: LlmScope, alias: str) -> None:
        """Refuse the call when the build's recorded tokens reached the cap, and alert once at
        each level. The count is a query over `llm_calls` (failed attempts included), so a retry
        that reuses an activity id cannot step around it."""
        caps: BudgetCaps = (
            self.caps if self.caps is not None else await asyncio.to_thread(self.store.caps, scope)
        )
        kind = BudgetScope.KNOWLEDGE if alias in KNOWLEDGE_ALIASES else BudgetScope.WORKFLOW
        used = await asyncio.to_thread(
            self.store.tokens, scope, KNOWLEDGE_ALIASES, inside=kind is BudgetScope.KNOWLEDGE
        )
        level = caps.level(kind, used)
        sub_key = ""
        if kind is BudgetScope.WORKFLOW and scope.temporal_workflow_id.startswith(BUILD_PREFIX):
            # One Sub-Skill build has its own, smaller cap; the worse of the two levels rules.
            sub_used = await asyncio.to_thread(self.store.tokens_build, scope)
            sub_level = caps.level(BudgetScope.SUB_SKILL, sub_used)
            if RANK[sub_level] > RANK[level]:
                kind, used, level = BudgetScope.SUB_SKILL, sub_used, sub_level
                sub_key = f":{scope.temporal_workflow_id}"
        if level is BudgetLevel.OK:
            return
        cap = caps.cap(kind)
        what = {
            BudgetScope.KNOWLEDGE: "knowledge build",
            BudgetScope.WORKFLOW: "workflow build",
            BudgetScope.SUB_SKILL: "sub-skill build",
        }[kind]
        pct = {
            BudgetLevel.FIRST_ALERT: caps.first_alert_pct,
            BudgetLevel.SECOND_ALERT: caps.second_alert_pct,
            BudgetLevel.STOPPED: 100,
        }[level]
        type_ = (
            NotificationType.TOKEN_BUDGET_WARNING
            if level is BudgetLevel.FIRST_ALERT
            else NotificationType.TOKEN_BUDGET_LIMIT
        )
        action = (
            "The build has stopped. Raise the limit or retry after review."
            if level is BudgetLevel.STOPPED
            else ""
        )
        try:  # an alert that cannot be written must never block the call that triggered it
            await asyncio.to_thread(
                self.store.alert,
                scope,
                type_,
                f"{kind.value}:{level.value}{sub_key}",
                {
                    "percent": f"{pct}%",
                    "what": what,
                    "used": f"{used:,}",
                    "cap": f"{cap:,}",
                    "next_action": action,
                },
            )
        except Exception:
            log.exception("token budget alert could not be written")
        if level is BudgetLevel.STOPPED:
            raise BudgetExceeded(f"{STOP_REASON}: {used:,} of {cap:,} tokens ({what})")
