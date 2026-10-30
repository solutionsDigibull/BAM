"""A-01: a stub activity (it always succeeds) must never run in a deployed environment."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import WorkflowRef
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.build import stubs as build_stubs
from sbam_workers.certify import stubs as certify_stubs
from sbam_workers.stub_ids import STUB_MARK
from temporalio.exceptions import ApplicationError

DEPLOYED = ["staging", "production", "prod", " Production ", "prod-eu", "live", "uat", "stage"]
DEV = ["development", "test", "ci", "CI", " development ", "", None]

_COMMON: dict[str, Any] = {
    "tenant_id": uuid4(),
    "project_id": uuid4(),
    "run_id": uuid4(),
    "correlation_id": "corr-a01",
    "workflow_id": uuid4(),
}
STAGE = BuildStageInput(**_COMMON, sub_skill_id=uuid4(), build_no=1)
WF = WorkflowRef(**_COMMON)

# every stub family: (activity, input)
STUBS = [
    (build_stubs.render_skeleton, STAGE),
    (certify_stubs.certify_sub_skill, STAGE),
    (certify_stubs.certify_macros, WF),
    (certify_stubs.validate_workflow, WF),
    (certify_stubs.certify_workflow, WF),
    (certify_stubs.freeze_and_version, WF),
]


def _set_env(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("SBAM_ENV", raising=False)
    else:
        monkeypatch.setenv("SBAM_ENV", value)


@pytest.mark.parametrize("env", DEPLOYED)
@pytest.mark.parametrize("stub,inp", STUBS, ids=[s.__name__ for s, _ in STUBS])
def test_a01_t02_stub_raises_not_implemented_when_deployed(
    monkeypatch: pytest.MonkeyPatch, env: str, stub: Any, inp: Any
) -> None:
    _set_env(monkeypatch, env)
    with pytest.raises(ApplicationError) as err:
        asyncio.run(stub(inp))
    assert err.value.type == "NOT_IMPLEMENTED"
    assert err.value.non_retryable is True


@pytest.mark.parametrize("env", DEV)
@pytest.mark.parametrize("stub,inp", STUBS, ids=[s.__name__ for s, _ in STUBS])
def test_a01_t03_stub_unchanged_in_dev_and_test(
    monkeypatch: pytest.MonkeyPatch, env: str | None, stub: Any, inp: Any
) -> None:
    _set_env(monkeypatch, env)
    assert asyncio.run(stub(inp)) is not None


def test_a01_t03_freeze_still_returns_1_0_0_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, "development")
    assert asyncio.run(certify_stubs.freeze_and_version(WF)).version == "1.0.0"


def test_a01_t01_prod_worker_serves_no_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every registered activity named in the issue is a guarded stub, and none escapes the guard:
    in a production env each one raises NOT_IMPLEMENTED, whichever queue serves it."""
    _set_env(monkeypatch, "production")
    registry.load_all()
    served = {
        fn.__name__: fn
        for queue in TaskQueue
        for fn in registry.collect(queue)[1]
        if getattr(fn, STUB_MARK, False)
    }
    assert {s.__name__ for s, _ in STUBS} <= set(served), "a stub is not registered or not marked"
    for stub, inp in STUBS:
        with pytest.raises(ApplicationError):
            asyncio.run(served[stub.__name__](inp))
