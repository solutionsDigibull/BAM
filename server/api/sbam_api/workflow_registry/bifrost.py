"""Bifrost's management API as the registry of certified Workflow servers (ADR 0028, decision 4).

Written against the real API (spike #750, checked again for #760 on a throwaway Bifrost v2.2.6):

- `POST /api/mcp/client` registers an HTTP server. It answers `{"status": "success"}` without an
  id, so the id is read back from the list. A second POST of a name answers 409 ("slug already
  exists"): that is the idempotent case, not an error.
- `GET /api/mcp/clients` lists the entries, 25 per page by default, each with its tools and their
  schemas.
- `PUT /api/mcp/client/{id}` with `{"disabled": true}` turns an entry off. It stays listed (C-31:
  nothing is revoked or deleted, a superseded version is disabled).
- The management API needs the setup token in `X-Bifrost-Setup-Token` (a wrong one is 403). The
  token is a secret: it comes from settings, is never logged and never put in an error.
- Bifrost's own database can answer `500 database is locked` while other writes run (spike F9):
  calls retry with a short backoff.

The client is a small protocol so the registrar can be tested with a fake, and so a lost Bifrost
database can be rebuilt from SBAM's table (`service.reconcile`).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from sbam_contracts.api import ErrorCode

from sbam_api.core.conventions import ApiError
from sbam_api.core.settings import Settings

PAGE = 100
RETRYABLE = {500, 502, 503, 504}


class RegistryUnavailable(ApiError):  # noqa: N818 - names from the spec
    """Bifrost could not be reached, or refused the call. Nothing sensitive is in the message."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            503, ErrorCode.SERVICE_UNAVAILABLE, "The Workflow registry is unavailable", detail
        )


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    name: str
    disabled: bool
    tools: tuple[str, ...]  # the tool names the server exposed when Bifrost connected to it


class BifrostRegistry(Protocol):
    def register(self, name: str, address: str) -> RegistryEntry:
        """Register an HTTP MCP server, or return the entry that already has this name."""
        ...

    def find(self, name: str) -> RegistryEntry | None: ...

    def disable(self, entry_id: str) -> None:
        """Turn an entry off. It stays listed; nothing is ever deleted."""
        ...

    def entries(self) -> list[RegistryEntry]: ...


class HttpBifrostRegistry:
    def __init__(
        self,
        base_url: str,
        token: str,
        client: httpx.Client | None = None,
        *,
        attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"X-Bifrost-Setup-Token": token}
        self._client = client or httpx.Client(timeout=30.0)
        self._attempts = attempts
        self._sleep = sleep

    def register(self, name: str, address: str) -> RegistryEntry:
        body = {
            "name": name,
            "connection_type": "http",
            "connection_string": address,
            "auth_type": "none",
            "tools_to_execute": ["*"],
        }
        response = self._send("POST", "/api/mcp/client", json=body, accept=(200, 409))
        entry = self.find(name)
        if entry is None:
            raise RegistryUnavailable(f"{name!r} ({response.status_code}) is not listed")
        return entry

    def find(self, name: str) -> RegistryEntry | None:
        return next((entry for entry in self.entries() if entry.name == name), None)

    def disable(self, entry_id: str) -> None:
        self._send("PUT", f"/api/mcp/client/{entry_id}", json={"disabled": True})

    def entries(self) -> list[RegistryEntry]:
        found: list[RegistryEntry] = []
        offset = 0
        while True:
            page = self._send("GET", "/api/mcp/clients", params={"limit": PAGE, "offset": offset})
            payload: dict[str, Any] = page.json()
            clients: list[dict[str, Any]] = payload.get("clients", [])
            found.extend(_entry(client) for client in clients)
            offset += len(clients)
            if not clients or offset >= int(payload.get("total_count", offset)):
                return found

    def _send(
        self,
        method: str,
        path: str,
        *,
        accept: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> httpx.Response:
        for attempt in range(self._attempts):
            last = attempt == self._attempts - 1
            try:
                response = self._client.request(
                    method, self._base + path, headers=self._headers, **kwargs
                )
            except httpx.TransportError as error:
                if last:
                    raise RegistryUnavailable("Bifrost did not answer") from error
                self._sleep(0.2 * 2**attempt)
                continue
            if response.status_code in accept:
                return response
            if response.status_code in (401, 403):
                raise RegistryUnavailable("Bifrost refused the setup token")
            if response.status_code in RETRYABLE and not last:  # e.g. "database is locked"
                self._sleep(0.2 * 2**attempt)
                continue
            raise RegistryUnavailable(f"Bifrost answered {response.status_code} to {method} {path}")
        raise RegistryUnavailable("Bifrost did not answer")  # pragma: no cover - the loop returns


def from_settings(settings: Settings) -> HttpBifrostRegistry:
    """The registry client the API and workers use. No token means nothing is registered."""
    if settings.bifrost_setup_token is None:
        raise RegistryUnavailable("the Bifrost setup token is not configured (BIFROST_SETUP_TOKEN)")
    return HttpBifrostRegistry(
        str(settings.bifrost_url), settings.bifrost_setup_token.get_secret_value()
    )


def _entry(client: dict[str, Any]) -> RegistryEntry:
    config: dict[str, Any] = client.get("config", {})
    tools = tuple(str(tool["name"]) for tool in client.get("tools", []))
    return RegistryEntry(
        id=str(config["client_id"]),
        name=str(config["name"]),
        disabled=bool(config.get("disabled", False)),
        tools=tools,
    )
