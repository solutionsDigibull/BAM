"""Temporal client for the API and the workers (TD §13.1).

`connect()` is the one place a client is built: namespace from settings, Pydantic data converter
(so contracts travel as JSON), and the OpenTelemetry interceptor, which carries the W3C
`traceparent` from the API request into workflows and activities (E1.7-T08).
"""

from datetime import timedelta

from opentelemetry.trace import Tracer
from sbam_contracts.api import ErrorCode
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter

from sbam_api.core.conventions import ApiError

# An API request that holds a database row lock while it calls Temporal must not wait on a hung
# server (A-30): a call that takes longer fails as OrchestratorUnavailable (503), the request rolls
# back and the lock is released.
RPC_TIMEOUT = timedelta(seconds=10)


class OrchestratorUnavailable(ApiError):  # noqa: N818 - name from the E1.7 spec
    """Temporal can't be reached or refused the call; nothing was started or written."""

    def __init__(self) -> None:
        super().__init__(
            503,
            ErrorCode.ORCHESTRATOR_UNAVAILABLE,
            "Orchestrator unavailable",
            "The orchestrator is unavailable; nothing was changed. Try again shortly.",
        )


async def connect(host: str, namespace: str, tracer: Tracer | None = None) -> Client:
    """Connect to Temporal. Raises RuntimeError when the server is unreachable."""
    return await Client.connect(
        host,
        namespace=namespace,
        data_converter=pydantic_data_converter,
        interceptors=[TracingInterceptor(tracer)],
    )
