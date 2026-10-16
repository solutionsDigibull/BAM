"""E5.7 Cognee's Bifrost aliases on the live stack (LLM_PROVIDER=mock), through Bifrost's host port.

E5.7-T04/T05 (a real Cognee `cognify` on the mock, and #78's tests unskipped) are the E5.1 tests in
`test_cognee_adapter.py`; their `cognee_aliases` fixture now finds `sbam-embed`.
"""

import json
import urllib.request
from typing import Any

import pytest

from .conftest import port

pytestmark = pytest.mark.integration


def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port('BIFROST_PORT', 8081)}/v1/{path}",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": "Bearer sbam-local"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed local URL
        return dict(json.loads(resp.read()))


def test_e5_7_t03_embeddings_through_bifrost() -> None:
    body = {"model": "sbam-embed", "input": ["ZEBRA-7731", "Mouser"], "encoding_format": "float"}
    first, second = post("embeddings", body), post("embeddings", body)
    vectors = [d["embedding"] for d in first["data"]]
    assert len(vectors) == 2 and all(len(v) == 1536 for v in vectors)
    assert vectors == [d["embedding"] for d in second["data"]]


def test_e5_7_knowledge_alias_answers_through_bifrost() -> None:
    reply = post(
        "chat/completions",
        {
            "model": "sbam-knowledge",
            "messages": [
                {"role": "system", "content": 'Respond to me with the following string: "test"'},
                {"role": "user", "content": "test"},
            ],
        },
    )
    assert reply["choices"][0]["message"]["content"] == "test"
