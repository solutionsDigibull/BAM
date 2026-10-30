"""Cognee's model calls, counted (#420).

Cognee runs inside worker-knowledge and calls Bifrost itself (`sbam-knowledge` for graph
extraction, `sbam-embed` for embeddings), so those calls never pass `LlmClient` and were invisible
to the budgets. Cognee makes them through LiteLLM, which reports every finished call to a callback;
this module is that callback. It writes one `llm_calls` row per call, in the project that asked, so
the recorded spend the budgets read includes Cognee. It does not refuse anything: a call already
running cannot be taken back, so a `cognify` that starts under budget may overshoot it (by one run),
and the next call of any alias is the one refused.

Who asked comes from `metered(...)`, which the Cognee adapter opens around each backend call; a
context variable follows the call into LiteLLM's callback. A call outside `metered` (nothing in
SBAM makes one) is logged and not recorded, never attributed to a guess.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from typing import Any

from sbam_api.core.logging import correlation_id_var
from temporalio import activity

from sbam_workers.llm.budget import Budget
from sbam_workers.llm.store import CallStore, LlmScope
from sbam_workers.llm.trace import new_trace_id
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.llm.meter")
ZERO_SHA = "0" * 64
_scope: ContextVar[LlmScope | None] = ContextVar("sbam_cognee_scope", default=None)
_installed = False


@contextmanager
def metered(tenant_id: uuid.UUID, project_id: uuid.UUID, what: str) -> Iterator[None]:
    """Calls Cognee makes inside this block are recorded for this tenant and project."""
    try:
        info = activity.info()
        workflow, activity_id, attempt = (
            info.workflow_id or "cognee",
            info.activity_id,
            info.attempt,
        )
    except RuntimeError:  # not inside an activity (a script, a test)
        workflow, activity_id, attempt = "cognee", what, 1
    scope = LlmScope(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id_var.get() or "cognee",
        temporal_workflow_id=workflow,
        activity_id=activity_id,
        attempt=attempt,
    )
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)


async def record_call(
    store: CallStore,
    budget: Budget,
    *,
    alias: str,
    model: str,
    call_type: str,
    tokens_in: int,
    tokens_out: int,
    reported_cost: float | None,
    started: datetime,
    ended: datetime,
) -> bool:
    """Write the row for one finished call; False when it was not attributable (not recorded)."""
    scope = _scope.get()
    if scope is None:
        log.warning(
            "a %s call to %s was made outside a metered block; not recorded", call_type, alias
        )
        return False
    cost = budget.cost_of(alias, tokens_in, tokens_out, Decimal(str(reported_cost or 0)))
    budget.note(alias, cost, tokens_in, tokens_out)
    await asyncio.to_thread(
        store.add,
        scope,
        {
            "alias": alias,
            "model": model,
            "prompt_id": f"cognee.{call_type}",
            "prompt_version": "-",
            "prompt_sha256": ZERO_SHA,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "latency_ms": int((ended - started).total_seconds() * 1000),
            "attempt": 1,
            "status": "SUCCEEDED",
            "trace_id": new_trace_id(),
            "context_manifest": {"cognee": {"call_type": call_type}},
        },
    )
    return True


def install(store: CallStore, settings: WorkerSettings) -> Budget:
    """Register the LiteLLM callback (once per process). Imported lazily: only worker-knowledge
    loads LiteLLM."""
    global _installed
    import litellm
    from litellm.integrations.custom_logger import CustomLogger

    budget = Budget(settings, store)

    class CogneeMeter(CustomLogger):
        async def async_log_success_event(
            self,
            kwargs: dict[str, Any],
            response_obj: Any,
            start_time: datetime,
            end_time: datetime,
        ) -> None:
            try:
                usage = getattr(response_obj, "usage", None)
                detail = kwargs.get("standard_logging_object") or {}
                await record_call(
                    store,
                    budget,
                    alias=str(kwargs.get("model") or detail.get("model") or "unknown"),
                    model=str(getattr(response_obj, "model", None) or kwargs.get("model") or ""),
                    call_type=str(detail.get("call_type") or kwargs.get("call_type") or "call"),
                    tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
                    tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
                    reported_cost=kwargs.get("response_cost") or detail.get("response_cost"),
                    started=start_time,
                    ended=end_time,
                )
            except Exception:
                log.exception("could not record a Cognee model call")

    if not _installed:
        litellm.logging_callback_manager.add_litellm_callback(CogneeMeter())
        _installed = True
    return budget
