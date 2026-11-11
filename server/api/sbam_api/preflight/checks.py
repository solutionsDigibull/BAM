"""The preflight checks. Each returns a `PreflightCheck`; none raises, none leaks a host, key or
provider message.

The provider probes cost next to nothing (one embedding of "ping", one chat call limited to one
output token) and the router caches the answer, so opening the page repeatedly bills nothing more.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any

import httpx
from sbam_contracts.preflight import CheckStatus, PreflightCheck

from sbam_api.core.settings import Settings

log = logging.getLogger("sbam_api.preflight")
PROBE_TIMEOUT_SECONDS = 20.0
_REFUSED = frozenset({401, 402, 403})

KEY_FIX = (
    "Ask an admin to check OPENROUTER_API_KEY in compose/.env: it is invalid, revoked or out of "
    "credit, or may not use this model. Then restart the stack."
)


def _classify(check_id: str, name: str, status: int, body: str) -> PreflightCheck:
    if status == 200:
        return PreflightCheck(id=check_id, name=name, status=CheckStatus.OK)
    if status in _REFUSED or (status == 502 and "upstream_credentials_exhausted" in body):
        return PreflightCheck(
            id=check_id,
            name=name,
            status=CheckStatus.DOWN,
            reason="credentials_refused",
            fix=KEY_FIX,
        )
    if status == 429:
        return PreflightCheck(
            id=check_id,
            name=name,
            status=CheckStatus.DEGRADED,
            reason="rate_limited",
            fix="The provider is rate limiting us. Wait a minute, or raise the provider's limit.",
        )
    return PreflightCheck(
        id=check_id,
        name=name,
        status=CheckStatus.DEGRADED if status >= 500 else CheckStatus.DOWN,
        reason=f"gateway_http_{status}",
        fix="The LLM gateway answered unexpectedly. Check the bifrost container's logs.",
    )


async def _probe(
    settings: Settings, check_id: str, name: str, path: str, body: Mapping[str, object]
) -> PreflightCheck:
    url = f"{str(settings.bifrost_url).rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        log.warning(
            "preflight probe failed", extra={"check": check_id, "exc_type": type(exc).__name__}
        )
        return PreflightCheck(
            id=check_id,
            name=name,
            status=CheckStatus.DOWN,
            reason="gateway_unreachable",
            fix="The LLM gateway (bifrost) did not answer. Check that its container is running.",
        )
    return _classify(check_id, name, response.status_code, response.text[:2000])


def mock_check(check_id: str, name: str) -> PreflightCheck:
    return PreflightCheck(
        id=check_id,
        name=name,
        status=CheckStatus.MOCK,
        reason="mock_llm",
        fix="LLM_PROVIDER=mock: answers are synthetic. Set LLM_PROVIDER=bifrost for real results.",
    )


Probe = Callable[[], Coroutine[Any, Any, PreflightCheck]]


def provider_probes(settings: Settings) -> dict[str, Probe]:
    if settings.llm_provider == "mock":
        return {
            "embeddings": lambda: _done(mock_check("embeddings", "Embeddings (knowledge search)")),
            "chat_model": lambda: _done(mock_check("chat_model", "Language model (SOW, build)")),
        }
    return {
        "embeddings": lambda: _probe(
            settings,
            "embeddings",
            "Embeddings (knowledge search)",
            "/v1/embeddings",
            {"model": "sbam-embed", "input": "ping"},
        ),
        "chat_model": lambda: _probe(
            settings,
            "chat_model",
            "Language model (SOW, build)",
            "/v1/chat/completions",
            {
                "model": "sbam-sow",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
        ),
    }


async def _done(check: PreflightCheck) -> PreflightCheck:
    return check


PLATFORM = {
    "db": ("Database", "Check the postgres container and DATABASE_URL."),
    "storage": ("File storage", "Check the storage volume is mounted and writable."),
    "temporal": (
        "Workflow engine",
        "Check the temporal container; builds cannot start without it.",
    ),
    "keycloak_jwks": ("Sign-in (Keycloak)", "Check the keycloak container; nobody can sign in."),
}


async def platform_check(check_id: str, check: Callable[[], Awaitable[bool]]) -> PreflightCheck:
    name, fix = PLATFORM.get(check_id, (check_id, "Check the service."))
    try:
        ok = await asyncio.wait_for(check(), 5.0)
    except Exception as exc:
        log.warning(
            "preflight check failed", extra={"check": check_id, "exc_type": type(exc).__name__}
        )
        ok = False
    if ok:
        return PreflightCheck(id=check_id, name=name, status=CheckStatus.OK)
    return PreflightCheck(
        id=check_id, name=name, status=CheckStatus.DOWN, reason="unreachable", fix=fix
    )
