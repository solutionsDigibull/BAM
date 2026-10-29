"""Deterministic ids for stub activities (E10.1): the same run + step always gives the same id, so
an activity retry or a resumed pipeline never invents a second Workflow / Sub-Skill. Real
activities upsert by their natural keys instead (TD §13.4).

A stub always succeeds, so a deployed worker must never run one: `@stub_activity` makes it raise a
non-retryable NOT_IMPLEMENTED unless `SBAM_ENV` names a dev environment (A-01)."""

import functools
import os
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID, uuid5

from sbam_contracts.build import BuildStageInput
from sbam_contracts.runs import RunInput
from temporalio.exceptions import ApplicationError

# An allowlist, not a deny-list: an unknown spelling ("prod-eu", "live") is refused. Unset counts
# as development, like `Settings.sbam_env`; compose_lint requires it on deployed workers.
STUB_ENVS = frozenset({"development", "test", "ci"})
STUB_MARK = "__sbam_stub__"


def stub_env() -> str:
    return (os.environ.get("SBAM_ENV") or "development").strip().lower()


def stub_activity[**P, R](
    fn: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """Mark `fn` as a stub activity and refuse it outside `STUB_ENVS` (non-retryable)."""

    @functools.wraps(fn)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        env = stub_env()
        if env not in STUB_ENVS:
            raise ApplicationError(
                f"{fn.__name__} is a stub and cannot run when SBAM_ENV={env!r}",
                type="NOT_IMPLEMENTED",
                non_retryable=True,
            )
        return await fn(*args, **kwargs)

    setattr(guarded, STUB_MARK, True)
    return guarded


def stub_id(inp: RunInput, step: str) -> UUID:
    return uuid5(inp.run_id, step)


def stage_stub_id(inp: BuildStageInput, step: str) -> UUID:
    """Same as `stub_id`, per Sub-Skill build and attempt (the real stages' idempotency key)."""
    key = f"{inp.sub_skill_id}:{inp.build_no}:{inp.cycle}:{inp.attempt}:{step}"
    return uuid5(inp.run_id, key)
