"""Embeddings through Bifrost's `sbam-embed` alias (E6.2: ranking chunks when a bundle is too big).

The same route Cognee uses (#259), so the mock provider answers it in CI with deterministic
vectors. Vectors are only used to order chunks inside one activity, never stored, and the texts
are already the project's own bundle; no `llm_calls` row is written (nothing is generated).
Failures are `LlmTransportError` (retryable), like the chat route's.
"""

from collections.abc import Sequence
from contextlib import nullcontext

import httpx

from sbam_workers.llm.client import LlmTransportError
from sbam_workers.settings import WorkerSettings

EMBED_ALIAS = "sbam-embed"
BATCH = 64


async def embed(
    settings: WorkerSettings, texts: Sequence[str], http: httpx.AsyncClient | None = None
) -> list[list[float]]:
    """One vector per text, in order."""
    url = f"{str(settings.bifrost_url).rstrip('/')}/v1/embeddings"
    out: list[list[float]] = []
    async with httpx.AsyncClient() if http is None else nullcontext(http) as client:
        for start in range(0, len(texts), BATCH):
            batch = list(texts[start : start + BATCH])
            try:
                response = await client.post(
                    url,
                    json={"model": EMBED_ALIAS, "input": batch},
                    timeout=settings.llm_timeout_seconds,
                )
            except httpx.HTTPError as exc:
                raise LlmTransportError(f"{type(exc).__name__} calling Bifrost") from exc
            if response.status_code != 200:
                raise LlmTransportError(f"Bifrost answered {response.status_code} to embeddings")
            try:
                data = sorted(response.json()["data"], key=lambda d: d["index"])
                vectors = [[float(x) for x in d["embedding"]] for d in data]
            except (KeyError, TypeError, ValueError) as exc:
                raise LlmTransportError(
                    "Bifrost answered embeddings with an unreadable body"
                ) from exc
            if len(vectors) != len(batch):
                raise LlmTransportError("Bifrost answered a different number of embeddings")
            out.extend(vectors)
    return out
