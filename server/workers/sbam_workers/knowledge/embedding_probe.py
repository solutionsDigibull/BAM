"""One cheap embeddings call before a build, so a dead provider key fails fast and says why.

Without it a rejected OpenRouter key (Bifrost answers 502 `upstream_credentials_exhausted`) reaches
us as Cognee's `EmbeddingException`, which `classify` maps to the retryable
`CogneeUnavailable`: five attempts, a 30 s Cognee connection test each, and an error that names
Cognee instead of the key.
Only a permanent credential refusal is raised here; a timeout or a 5xx is left to Cognee's own path.
"""

import os
from collections.abc import Mapping

import httpx

PROBE_TIMEOUT_SECONDS = 20.0
_REFUSED = frozenset({401, 402, 403})


class EmbeddingProviderRejected(RuntimeError):  # noqa: N818
    """The embedding provider refused our credentials; retrying cannot help."""


def _refused(status: int, body: str) -> bool:
    return status in _REFUSED or (status == 502 and "upstream_credentials_exhausted" in body)


async def probe_embeddings(env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    endpoint = env.get("EMBEDDING_ENDPOINT", "").rstrip("/")
    if not endpoint:
        return
    model = env.get("EMBEDDING_MODEL", "").removeprefix("openai/")  # LiteLLM strips it too
    headers = {"Authorization": f"Bearer {env.get('EMBEDDING_API_KEY', '')}"}
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{endpoint}/embeddings", json={"model": model, "input": "ping"}, headers=headers
            )
    except httpx.HTTPError:
        return  # unreachable or slow: transient, let the build's own retry handle it
    if _refused(resp.status_code, resp.text):
        raise EmbeddingProviderRejected(
            f"the embedding provider refused its credentials (HTTP {resp.status_code}); "
            "check OPENROUTER_API_KEY and its credit, or run with LLM_PROVIDER=mock"
        )
