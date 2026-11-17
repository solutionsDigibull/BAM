"""SBAM's own health check of a registered Workflow server (spike #750, F11; #760).

Bifrost's `state` lags: it still said `healthy` eight seconds after a server was stopped. So a
registered server is checked by asking it, over MCP streamable HTTP, for its tools (`initialize`,
then `tools/list`), and by comparing them with the tools the certified manifest promised.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx

from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_api.workflow_registry.service import default_address

ACCEPT = "application/json, text/event-stream"
PROTOCOL = "2025-03-26"


class ProbeError(Exception):
    """The server did not answer as an MCP server."""


class Probe(Protocol):
    def tool_names(self, address: str) -> tuple[str, ...]: ...


class McpProbe:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=10.0)

    def tool_names(self, address: str) -> tuple[str, ...]:
        try:
            init = self._post(
                address,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {},
                        "clientInfo": {"name": "sbam-health", "version": "1"},
                    },
                },
            )
            session = init.headers.get("mcp-session-id")
            headers = {"mcp-session-id": session} if session else {}
            self._post(address, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
            listed = self._post(
                address, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers
            )
        except httpx.HTTPError as error:
            raise ProbeError("the server did not answer") from error
        result = _result(listed)
        return tuple(str(tool["name"]) for tool in result.get("tools", []))

    def _post(
        self, address: str, body: dict[str, Any], headers: dict[str, str] | None = None
    ) -> httpx.Response:
        response = self._client.post(
            address, json=body, headers={"Accept": ACCEPT, **(headers or {})}
        )
        response.raise_for_status()
        return response


def _result(response: httpx.Response) -> dict[str, Any]:
    """The JSON-RPC `result`, from a JSON body or from the `data:` line of an event stream."""
    text = response.text
    if "text/event-stream" in response.headers.get("content-type", ""):
        lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        text = lines[-1] if lines else ""
    try:
        message = json.loads(text)
    except ValueError as error:
        raise ProbeError("the answer is not JSON-RPC") from error
    result = message.get("result") if isinstance(message, dict) else None
    if not isinstance(result, dict):
        raise ProbeError("the answer has no result")
    return result


def check(
    rows: Sequence[RegisteredWorkflowRow],
    probe: Probe,
    address_for: Callable[[str], str] = default_address,
) -> dict[str, bool]:
    """Registry name -> healthy, for the enabled entries.

    Healthy means the server answers and lists every tool the manifest promised.
    """
    health: dict[str, bool] = {}
    for row in rows:
        if row.status != "REGISTERED":
            continue
        promised = {str(tool["name"]) for tool in row.tools}
        try:
            offered = set(probe.tool_names(address_for(row.registry_name)))
        except ProbeError:
            health[row.registry_name] = False
            continue
        health[row.registry_name] = promised <= offered
    return health
