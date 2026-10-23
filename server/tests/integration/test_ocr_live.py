"""E4.8 against the real vision model: a page image through Bifrost to OpenRouter (opt-in).

Costs a fraction of a cent per page, so it only runs with `LIVE_LLM=1`. Needs Bifrost started with
`LLM_PROVIDER=bifrost` and `OPENROUTER_API_KEY` in compose/.env (see test_llm_live.py):

    LIVE_LLM=1 uv run pytest -m integration tests/integration/test_ocr_live.py -s

It proves what the unit tests cannot: that Bifrost and OpenRouter forward the image to the model,
that `sbam-ocr` (gpt-4o-mini, falling back to gpt-4o) reads it, and that the answer fits `OcrPage`.
"""

import asyncio
import io
import os
import uuid
from typing import Any

import httpx
import pytest
from fixtures import fixture_path
from llm_db import llm_sessions, rows
from PIL import Image, ImageDraw, ImageFont
from pydantic import AnyHttpUrl
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest import ocr
from sbam_workers.ingest.ocr import OcrDeps, OcrRun
from sbam_workers.ingest.rasterize import open_source
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.settings import WorkerSettings

from .conftest import is_open, port

pytestmark = pytest.mark.integration

BIFROST = f"http://127.0.0.1:{port('BIFROST_PORT', 8081)}"
TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()


@pytest.fixture(autouse=True)
def _live() -> None:
    if os.environ.get("LIVE_LLM") != "1":
        pytest.skip("live model calls cost money: set LIVE_LLM=1")
    if not is_open(port("BIFROST_PORT", 8081)):
        pytest.skip("bifrost is not published (start it with LLM_PROVIDER=bifrost)")


def make() -> tuple[OcrDeps, Any]:
    sessions = llm_sessions()
    conf = WorkerSettings(
        _env_file=None,  # type: ignore[call-arg]
        temporal_host="temporal:7233",
        database_url="sqlite://",
        bifrost_url=AnyHttpUrl(BIFROST),
        llm_timeout_seconds=120,
    )
    client = LlmClient(
        conf, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(conf), httpx.AsyncClient()
    )
    return OcrDeps(sessions=sessions, storage=None, reader=client), sessions


def stage() -> StageRef:
    return StageRef(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=uuid.uuid4(),
        correlation_id="live-ocr",
        ref_id=uuid.uuid4(),
    )


def typed_page() -> bytes:
    """A page of typed text, as an image the model has to read."""
    image = Image.new("RGB", (1400, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=44)
    draw.text((40, 40), "Quote Q-1042", fill="black", font=font)
    draw.text((40, 130), "12 cables, part CBL-7731", fill="black", font=font)
    draw.text((40, 220), "Delivery March 2027, net 30 days", fill="black", font=font)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def read(deps: OcrDeps, data: bytes, kind: str, n: int, run: OcrRun) -> Any:
    source = open_source(data, kind)
    try:
        return asyncio.run(ocr.read_page(deps, stage(), run, source, n))
    finally:
        source.close()


def test_e4_8_live_the_model_reads_a_page_image_and_the_call_is_recorded() -> None:
    deps, sessions = make()
    inp = stage()
    run = OcrRun(f"wf-{uuid.uuid4()}", "act-live", 1, "run-live")
    source = open_source(typed_page(), "png")
    try:
        page = asyncio.run(ocr.read_page(deps, inp, run, source, 1))
    finally:
        source.close()
    print(f"\nread: {page.markdown!r} warning={page.warning!r}")
    assert page.readable, page.warning
    text = page.markdown.lower()
    assert "q-1042" in text and "cbl-7731" in text and "march" in text
    scope = LlmScope(
        TENANT, PROJECT, "live-ocr", f"{run.workflow_id}:{run.run_id}", "act-live:page-1"
    )
    [row] = [r for r in rows(sessions, scope) if r.status == "SUCCEEDED"]
    print(
        f"model={row.model} in={row.tokens_in} out={row.tokens_out} "
        f"cost={row.cost_usd} latency={row.latency_ms}ms trace={row.trace_id}"
    )
    assert row.alias == "sbam-ocr" and row.prompt_id == "ocr-page"
    assert row.context_manifest["_images"]["page-1"]["media_type"] == "image/png"


def test_e4_8_live_a_blank_page_is_reported_unreadable_not_invented() -> None:
    deps, _ = make()
    blank = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(blank, format="PNG")
    page = read(
        deps, blank.getvalue(), "png", 1, OcrRun(f"wf-{uuid.uuid4()}", "act-blank", 1, "run-blank")
    )
    print(f"\nblank page: readable={page.readable} markdown={page.markdown!r} {page.warning!r}")
    assert not page.readable  # no text may be made up for an empty page


def test_e4_8_live_the_golden_scanned_pdf_pages_are_answered_for_the_right_page() -> None:
    deps, _ = make()
    data = fixture_path("documents", "scanned_full.pdf").read_bytes()
    run = OcrRun(f"wf-{uuid.uuid4()}", "act-golden", 1, "run-golden")
    for n in (1, 2):
        page = read(deps, data, "pdf", n, run)
        print(f"\npage {n}: readable={page.readable} text={page.markdown[:120]!r} {page.warning!r}")
        assert page.number == n  # an answer for another page would have been re-asked
