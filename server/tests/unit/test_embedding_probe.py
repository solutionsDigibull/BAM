"""The pre-build embeddings probe: a refused key is permanent, anything else is left to Cognee."""

import asyncio

import httpx
import pytest
from sbam_workers.knowledge.embedding_probe import EmbeddingProviderRejected, probe_embeddings

ENV = {"EMBEDDING_ENDPOINT": "http://bifrost:8080/v1", "EMBEDDING_MODEL": "openai/sbam-embed"}


REAL_CLIENT = httpx.AsyncClient


def serve(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=handler, **kw))


@pytest.mark.parametrize("status", [401, 402, 403])
def test_refused_status_is_rejected(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    serve(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(EmbeddingProviderRejected, match=str(status)):
        asyncio.run(probe_embeddings(ENV))


def test_bifrost_credentials_exhausted_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"error": {"type": "upstream_credentials_exhausted"}}
    serve(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(502, json=body)))
    with pytest.raises(EmbeddingProviderRejected):
        asyncio.run(probe_embeddings(ENV))


def test_other_failures_are_left_to_cognee(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(503)))
    asyncio.run(probe_embeddings(ENV))

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    serve(monkeypatch, httpx.MockTransport(boom))
    asyncio.run(probe_embeddings(ENV))


def test_ok_strips_the_litellm_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bytes] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"data": []})

    serve(monkeypatch, httpx.MockTransport(ok))
    asyncio.run(probe_embeddings(ENV))
    assert b'"sbam-embed"' in seen[0]
