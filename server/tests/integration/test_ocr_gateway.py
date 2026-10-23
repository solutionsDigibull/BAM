"""E4.8 against the Compose stack: a vision request for `sbam-ocr` through Bifrost and the mock.

Needs `make up` with the default LLM_PROVIDER=mock (the mock answers `sbam-ocr` for the page the
request names and cannot read the image). Scenarios are set per call with `metadata.mock_scenario`,
which Bifrost forwards to the upstream, so no admin port is published (as in test_llm_gateway.py).
What this proves: the image part survives Bifrost, the fallback model gets it when the primary is
down, and the wrapper sees a provider outage as a retryable transport error.
"""

import asyncio
import json
import urllib.error
import urllib.request
import uuid
from typing import Any

import httpx
import pytest
from llm_db import llm_sessions, rows
from pydantic import AnyHttpUrl
from sbam_contracts.documents import OcrPage
from sbam_workers.llm.client import LlmClient, LlmImage, LlmTransportError, compose
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.settings import WorkerSettings

from .conftest import is_open, port

pytestmark = pytest.mark.integration

BASE = f"http://127.0.0.1:{port('BIFROST_PORT', 8081)}"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 16
TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()


@pytest.fixture(autouse=True)
def _bifrost_up() -> None:
    if not is_open(port("BIFROST_PORT", 8081)):
        pytest.skip("bifrost is not published (make up)")


def request_body(page: int = 2, *, image: bool = True, **metadata: str) -> dict[str, Any]:
    """What `LlmClient.run(images=...)` sends for one page."""
    prompt = PromptRegistry().get("ocr-page")
    images = [LlmImage("page-1", "image/png", PNG)] if image else []
    messages, _ = compose(prompt, OcrPage, {"page_number": str(page)}, images)
    return {
        "model": "sbam-ocr",
        "messages": messages,
        "metadata": {"prompt_id": "ocr-page", **metadata},
    }


def call(body: dict[str, Any]) -> tuple[int, dict[str, str], dict[str, Any]]:
    req = urllib.request.Request(  # noqa: S310 - fixed http://127.0.0.1 URL
        f"{BASE}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read())


def answer(body: dict[str, Any]) -> OcrPage:
    return OcrPage.model_validate_json(body["choices"][0]["message"]["content"], strict=True)


def make() -> tuple[LlmClient, Any]:
    sessions = llm_sessions()
    conf = WorkerSettings(
        _env_file=None,  # type: ignore[call-arg]
        temporal_host="temporal:7233",
        database_url="sqlite://",
        bifrost_url=AnyHttpUrl(BASE),
        llm_timeout_seconds=90,
        llm_backoff_seconds=0.1,
    )
    client = LlmClient(
        conf, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(conf), httpx.AsyncClient()
    )
    return client, sessions


def new_scope(page: int) -> LlmScope:
    return LlmScope(TENANT, PROJECT, "ocr-gw", f"wf-{uuid.uuid4()}", f"act-{page}")


def ask(client: LlmClient, page: int, scope: LlmScope, **kwargs: Any) -> Any:
    return asyncio.run(
        client.run(
            "sbam-ocr",
            "ocr-page",
            OcrPage,
            {"page_number": str(page)},
            scope,
            images=[LlmImage("page-1", "image/png", PNG)],
            **kwargs,
        )
    )


def test_e4_8_a_vision_request_gets_a_page_of_text_from_the_primary_model() -> None:
    status, headers, body = call(request_body(2))
    assert status == 200 and headers["X-Bifrost-Resolved-Model"] == "sbam-ocr-primary"
    out = answer(body)
    assert (out.page, out.unreadable) == (2, False) and out.markdown == "Mock OCR text of page 2."


def test_e4_8_l03_when_the_primary_is_down_the_fallback_model_gets_the_image_and_answers() -> None:
    status, headers, body = call(request_body(3, mock_scenario="5xx"))
    assert status == 200 and headers["X-Bifrost-Resolved-Model"] == "sbam-ocr-fallback"
    out = answer(body)
    # the mock answers an unreadable page when no image arrives, so this text proves the image did
    assert (out.page, out.unreadable, out.markdown) == (3, False, "Mock OCR text of page 3.")


def test_e4_8_a_request_that_reaches_the_model_without_an_image_is_an_unreadable_page() -> None:
    status, _, body = call(request_body(2, image=False))
    assert status == 200 and answer(body).unreadable is True


def test_e4_8_t05_when_both_models_fail_the_caller_sees_the_error() -> None:
    status, _, body = call(request_body(2, mock_scenario="5xx_all"))
    assert status >= 500 and "error" in body


def test_e4_8_l03_through_the_wrapper_the_fallback_is_recorded_as_the_model() -> None:
    client, sessions = make()
    scope = new_scope(4)
    result = ask(client, 4, scope, metadata={"mock_scenario": "5xx"})
    assert result.output.page == 4 and result.output.markdown == "Mock OCR text of page 4."
    [row] = [r for r in rows(sessions, scope) if r.status == "SUCCEEDED"]
    assert row.model == "sbam-ocr-fallback" and row.alias == "sbam-ocr"
    assert row.context_manifest["_images"]["page-1"]["bytes"] == len(PNG)


def test_e4_8_t05_through_the_wrapper_a_provider_outage_is_a_retryable_transport_error() -> None:
    client, sessions = make()
    scope = new_scope(5)
    with pytest.raises(LlmTransportError):
        ask(client, 5, scope, metadata={"mock_scenario": "5xx_all"})
    failed = rows(sessions, scope)
    assert len(failed) == client.settings.llm_transport_attempts
    assert {r.status for r in failed} == {"FAILED"}
    assert "base64" not in json.dumps([r.context_manifest for r in failed])  # no image stored


def test_e4_8_through_the_wrapper_an_always_invalid_answer_ends_as_schema_invalid() -> None:
    client, sessions = make()
    scope = new_scope(6)
    with pytest.raises(SchemaInvalidAfterRetries):
        ask(client, 6, scope, metadata={"mock_scenario": "schema_invalid"})
    assert {r.status for r in rows(sessions, scope)} == {"SCHEMA_INVALID"}
    assert len(rows(sessions, scope)) == 4  # the call and three re-asks


def test_e4_8_through_the_wrapper_two_invalid_answers_then_a_valid_one_is_accepted() -> None:
    client, _ = make()
    result = ask(client, 7, new_scope(7), metadata={"mock_scenario": "schema_invalid:2"})
    assert result.output.page == 7 and result.attempts == 3
