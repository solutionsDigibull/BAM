"""Startup self-check of the test worker's sandbox settings (E13.2, ADR 0023).

A worker that is told to use a sandbox must be able to. A failure stops it before it polls (exit 3,
`SANDBOX_MISCONFIGURED`), so a half-configured host is a failed deploy and not a Sub-Skill that
waits in TESTING_BLOCKED for a reason nobody can see:

  * `SANDBOX_MODE` set but what the mode needs missing (CUBESANDBOX: `CUBE_API_URL`,
    `CUBE_PROXY_NODE_IP`, `SANDBOX_EGRESS_LOG_PATH`; LOCAL_DOCKER_NONCERT: the agent's URL and
    token);
  * a deployed environment (anything but development, test, ci) running LOCAL_DOCKER_NONCERT: that
    mode can never certify, so a staging or production worker on it would run tests whose results
    certification must refuse.

`SANDBOX_MODE` unset is allowed: such a worker runs everything but the sandbox tests, which end as
an infrastructure error (TESTING_BLOCKED). Messages name variables, never their values.
"""

from sbam_api.core.settings import deployed_env
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sandbox import SandboxMode

from sbam_workers.registry import PreflightError, preflight_for
from sbam_workers.sandbox.factory import problem
from sbam_workers.settings import WorkerSettings

MISCONFIGURED = "SANDBOX_MISCONFIGURED"


def check_sandbox(settings: WorkerSettings) -> None:
    if settings.sandbox_mode is None:
        return
    why = problem(settings)
    if why is not None:
        raise PreflightError(MISCONFIGURED, why)
    if settings.sandbox_mode is SandboxMode.LOCAL_DOCKER_NONCERT and deployed_env(
        settings.sbam_env
    ):
        raise PreflightError(
            MISCONFIGURED,
            f"SANDBOX_MODE=LOCAL_DOCKER_NONCERT can never certify and is not allowed when "
            f"SBAM_ENV={settings.sbam_env.strip().lower()!r}",
        )


@preflight_for(TaskQueue.TEST)
def sandbox_preflight() -> None:
    check_sandbox(WorkerSettings())  # type: ignore[call-arg]  # values from the env
