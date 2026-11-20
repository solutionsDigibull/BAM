"""Cognee's model calls, counted (#420).

Cognee runs inside worker-knowledge and calls Bifrost itself (`sbam-knowledge` for graph
extraction, `sbam-embed` for embeddings), so those calls never pass `LlmClient` and were invisible
to the budgets. Cognee makes them through LiteLLM, which reports every finished call to a callback;
this module is that callback. It writes one `llm_calls` row per call, in the project that asked, so
the recorded spend the budgets read includes Cognee. A call already running cannot be taken back,
so a `cognify` that starts under budget may overshoot it (by one run), and the next call of any
alias is the one refused (`refuse_if_capped`, at the start of each build step).

Two things end a running `cognify` at once (#777): the provider refusing for credit (Bifrost
answers `502 upstream_credentials_exhausted`; LiteLLM would retry it for an hour) and the knowledge
token cap being reached. A callback cannot raise into Cognee (LiteLLM swallows what a logging
callback raises), so it sets a flag on the `Stop` of the metered block; `Stop.run` races the backend
call against that flag, cancels the call and raises the typed, non-retryable `CogneeStopped`.

Who asked comes from `metered(...)`, which the Cognee adapter opens around each backend call; a
context variable follows the call into LiteLLM's callback. A call outside `metered` (nothing in
SBAM makes one) is logged and not recorded, never attributed to a guess.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sbam_api.core.logging import correlation_id_var
from temporalio import activity

from sbam_workers.llm.budget import Budget, BudgetExceeded
from sbam_workers.llm.client import _cost
from sbam_workers.llm.store import CallStore, LlmScope
from sbam_workers.llm.trace import Generation, LangfuseTracer, new_trace_id
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.llm.meter")
ZERO_SHA = "0" * 64
_scope: ContextVar[LlmScope | None] = ContextVar("sbam_cognee_scope", default=None)
_stop: ContextVar["Stop | None"] = ContextVar("sbam_cognee_stop", default=None)
_installed = False
_budget: Budget | None = None
CREDIT_MARKER = "upstream_credentials_exhausted"  # Bifrost's `502` body code (ADR 0001 section 7)
CANCEL_WAIT_SECONDS = 10.0  # how long a cancelled Cognee call may take to unwind


class StopCause(StrEnum):
    """Why a Cognee build was ended at once. The value is the build's `error_reason` code, which
    the Context step turns into a plain message (`client/src/workbench/rag/stop-cause.ts`)."""

    CREDIT_EXHAUSTED = "ModelCreditExhausted"
    TOKEN_CAP = "KnowledgeTokenCap"  # noqa: S105 - a code, not a secret


class CogneeStopped(Exception):  # noqa: N818
    """A Cognee build was ended because the model account has no credit or the knowledge token cap
    was reached. Retrying cannot help until the account is topped up or the cap raised."""

    retryable = False

    def __init__(self, cause: StopCause, detail: str) -> None:
        super().__init__(detail)
        self.cause = cause


def credit_refused(text: str) -> bool:
    return CREDIT_MARKER in text


class Stop:
    """The 'end it now' flag of one metered block. Callbacks `trigger` it, `run` obeys it."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.error: CogneeStopped | None = None

    def trigger(self, cause: StopCause, detail: str) -> None:
        if self.error is None:  # the first cause stays
            self.error = CogneeStopped(cause, detail)
            self.event.set()

    def raise_if_set(self) -> None:
        if self.error is not None:
            raise self.error

    async def run[R](self, work: Awaitable[R]) -> R:
        """Await `work`, but give it up (cancelled) the moment a callback has triggered the stop."""
        self.raise_if_set()
        task = asyncio.ensure_future(work)
        waiter = asyncio.ensure_future(self.event.wait())
        try:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                return task.result()
            task.cancel()
            await asyncio.wait({task}, timeout=CANCEL_WAIT_SECONDS)
            self.raise_if_set()
            raise RuntimeError("unreachable: the stop flag was set")
        finally:
            waiter.cancel()
            if not task.done():
                task.cancel()


@contextmanager
def metered(tenant_id: uuid.UUID, project_id: uuid.UUID, what: str) -> Iterator[Stop]:
    """Calls Cognee makes inside this block are recorded for this tenant and project. Yields the
    block's `Stop`: run the Cognee call as `await stop.run(...)` to have it ended at once."""
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
    stop = Stop()
    token, stop_token = _scope.set(scope), _stop.set(stop)
    try:
        yield stop
    finally:
        _scope.reset(token)
        _stop.reset(stop_token)


async def refuse_if_capped(tenant_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Before a build step that makes model calls: raise `CogneeStopped` when the knowledge token
    cap is already reached, so the next call after the cap is refused (WG24-T03)."""
    if _budget is None:
        return
    scope = LlmScope(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id_var.get() or "cognee",
        temporal_workflow_id="cognee",
        activity_id="cap-check",
        attempt=1,
    )
    try:
        await _budget.check_tokens(scope, "sbam-knowledge")
    except BudgetExceeded as exc:
        raise CogneeStopped(StopCause.TOKEN_CAP, str(exc)) from None


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
    tracer: LangfuseTracer | None = None,
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
    trace_id = new_trace_id()
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
            "trace_id": trace_id,
            "context_manifest": {"cognee": {"call_type": call_type}},
        },
    )
    if tracer is not None:  # Cognee's calls never pass LlmClient: trace them here (never raises)
        await tracer.record(
            Generation(
                trace_id=trace_id,
                alias=alias,
                model=model,
                prompt_id=f"cognee.{call_type}",
                prompt_version="-",
                tenant_id=str(scope.tenant_id),
                project_id=str(scope.project_id),
                attempt=1,
                status="SUCCEEDED",
                started_at=started,
                ended_at=ended,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=float(cost),
            )
        )
    return True


def install(store: CallStore, settings: WorkerSettings) -> Budget:
    """Register the LiteLLM callback (once per process). Imported lazily: only worker-knowledge
    loads LiteLLM."""
    global _installed
    import litellm
    from litellm.integrations.custom_logger import CustomLogger

    global _budget
    budget = Budget(settings, store)
    _budget = budget
    tracer = LangfuseTracer(settings)

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
                    # Bifrost's own figure rides in `usage.cost` (what LlmClient reads); it wins
                    # over LiteLLM's estimate, which cannot price `sbam-knowledge` at all. Without
                    # it the budget saw $0 and refused the alias after the first call.
                    reported_cost=_cost(getattr(usage, "cost", None))
                    or kwargs.get("response_cost")
                    or detail.get("response_cost"),
                    # LiteLLM's times are naive local datetimes; Langfuse refuses those
                    started=start_time.astimezone(UTC),
                    ended=end_time.astimezone(UTC),
                    tracer=tracer,
                )
                await self._stop_at_cap(str(kwargs.get("model") or "unknown"))
            except Exception:
                log.exception("could not record a Cognee model call")

        async def async_log_failure_event(
            self,
            kwargs: dict[str, Any],
            response_obj: Any,
            start_time: datetime,
            end_time: datetime,
        ) -> None:
            """A refused call: when the provider has no credit left, end the build now instead of
            letting LiteLLM retry it until the activity times out."""
            try:
                stop = _stop.get()
                if stop is not None and credit_refused(_failure_text(kwargs)):
                    stop.trigger(
                        StopCause.CREDIT_EXHAUSTED,
                        "the model account is out of credit (the provider answered "
                        f"{CREDIT_MARKER}); top it up and rebuild",
                    )
            except Exception:
                log.exception("could not inspect a failed Cognee model call")

        async def _stop_at_cap(self, alias: str) -> None:
            """After a recorded call: when the knowledge cap is now reached, end the running build
            (the same call also writes the alerts at each level)."""
            stop, scope = _stop.get(), _scope.get()
            if stop is None or scope is None:
                return
            try:
                await budget.check_tokens(scope, alias)
            except BudgetExceeded as exc:
                stop.trigger(StopCause.TOKEN_CAP, str(exc))

    if not _installed:
        litellm.logging_callback_manager.add_litellm_callback(CogneeMeter())
        _installed = True
    return budget


def _failure_text(kwargs: dict[str, Any]) -> str:
    """The error LiteLLM reports for a failed call, as text: the exception and its body (Bifrost's
    code is in the response body) along the cause chain, plus the traceback LiteLLM keeps."""
    parts: list[str] = []
    exc = kwargs.get("exception")
    while exc is not None and len(parts) < 12:
        parts.append(str(exc))
        parts.append(str(getattr(exc, "body", "") or ""))
        exc = exc.__cause__ or exc.__context__
    parts.append(str(kwargs.get("traceback_exception") or ""))
    return " ".join(parts)
