"""The sandbox a worker is configured for (E13.2, ADR 0023, decision 1).

`SANDBOX_MODE` is read from the worker's environment and nowhere else. A worker that is asked to run
without one, or without what its mode needs, raises `SandboxInfraError(NOT_CONFIGURED)`: the run is
retried and then `TESTING_BLOCKED`, so a missing setting can never read as "the tests passed".
"""

from dataclasses import dataclass

from sbam_contracts.sandbox import InfraReason, SandboxMode

from sbam_workers.sandbox.backend import SandboxBackend, SandboxInfraError
from sbam_workers.sandbox.cube_backend import CubeSandboxBackend, CubeSettings
from sbam_workers.sandbox.egress import JsonlEgressReader
from sbam_workers.sandbox.executor import EgressReader
from sbam_workers.sandbox.local_backend import LocalDockerBackend
from sbam_workers.settings import WorkerSettings


@dataclass(frozen=True)
class Sandbox:
    mode: SandboxMode
    backend: SandboxBackend
    egress_reader: EgressReader | None  # required exactly when the mode certifies


def problem(settings: WorkerSettings) -> str | None:
    """What is missing for the configured mode (names only, never values), or None."""
    if settings.sandbox_mode is None:
        return "SANDBOX_MODE is not set"
    if settings.sandbox_mode is SandboxMode.CUBESANDBOX:
        missing = [
            name
            for name, value in (
                ("CUBE_API_URL", settings.cube_api_url),
                ("CUBE_PROXY_NODE_IP", settings.cube_proxy_node_ip),
                ("SANDBOX_EGRESS_LOG_PATH", settings.sandbox_egress_log_path),
            )
            if not value
        ]
        return f"{', '.join(missing)} not set" if missing else None
    missing = [
        name
        for name, value in (
            ("SANDBOX_AGENT_URL", settings.sandbox_agent_url),
            ("SANDBOX_AGENT_TOKEN", settings.sandbox_agent_token),
        )
        if not value
    ]
    return f"{', '.join(missing)} not set" if missing else None


def make_sandbox(settings: WorkerSettings) -> Sandbox:
    why = problem(settings)
    if why is not None or settings.sandbox_mode is None:
        raise SandboxInfraError(InfraReason.NOT_CONFIGURED, why or "SANDBOX_MODE is not set")
    if settings.sandbox_mode is SandboxMode.CUBESANDBOX:
        if not settings.cube_api_url or not settings.sandbox_egress_log_path:  # `problem` checked
            raise SandboxInfraError(InfraReason.NOT_CONFIGURED, "CUBE_API_URL is not set")
        key = settings.cube_api_key.get_secret_value() if settings.cube_api_key else None
        backend: SandboxBackend = CubeSandboxBackend(
            CubeSettings(
                api_url=settings.cube_api_url,
                proxy_node_ip=settings.cube_proxy_node_ip,
                proxy_port=settings.cube_proxy_port_http,
                sandbox_domain=settings.cube_sandbox_domain,
                api_key=key,
            )
        )
        return Sandbox(
            SandboxMode.CUBESANDBOX, backend, JsonlEgressReader(settings.sandbox_egress_log_path)
        )
    if not settings.sandbox_agent_url or not settings.sandbox_agent_token:  # `problem` checked
        raise SandboxInfraError(InfraReason.NOT_CONFIGURED, "SANDBOX_AGENT_URL is not set")
    local = LocalDockerBackend(
        settings.sandbox_agent_url, settings.sandbox_agent_token.get_secret_value()
    )
    return Sandbox(SandboxMode.LOCAL_DOCKER_NONCERT, local, None)
