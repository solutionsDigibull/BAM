"""E4.8 the mock upstream answers `sbam-ocr` for the page a vision request names."""

import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sbam_contracts.documents import OcrPage

REPO = Path(__file__).resolve().parents[3]
BIFROST_DIR = REPO / "compose/bifrost"
FIXTURES = REPO / "server/tests/llm-mock"


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


srv = load("llm_mock_server_e4_8", REPO / "compose/docker/llm-mock/server.py")
PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8).decode()


@pytest.fixture
def mock() -> Any:
    return srv.build(FIXTURES, BIFROST_DIR / "aliases.json")


def vision_request(page: int | None, model: str = "sbam-ocr-primary") -> dict[str, Any]:
    """The shape `LlmClient.run(images=...)` sends: text parts and an image part."""
    data = f'<data name="page_number">\n{page}\n</data>' if page is not None else "(no documents)"
    parts = [
        {"type": "text", "text": data},
        {"type": "text", "text": '<image name="page-1">'},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
    ]
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "read the page"},
            {"role": "user", "content": parts},
        ],
        "metadata": {"prompt_id": "ocr-page"},
    }


def answer(mock: Any, body: dict[str, Any]) -> OcrPage:
    status, reply = mock.chat(body)
    assert status == 200, reply
    return OcrPage.model_validate_json(reply["choices"][0]["message"]["content"], strict=True)


def test_e4_8_the_alias_is_marked_for_a_page_answer() -> None:
    specs = json.loads((BIFROST_DIR / "aliases.json").read_text())
    assert specs["sbam-ocr"]["mock"] == "ocr"
    assert [a for a, s in specs.items() if s.get("mock") == "ocr"] == ["sbam-ocr"]


@pytest.mark.parametrize("page", [1, 2, 17, 200])
def test_e4_8_the_answer_is_for_the_page_the_request_names(mock: Any, page: int) -> None:
    out = answer(mock, vision_request(page))
    assert (out.page, out.unreadable) == (page, False)
    assert out.markdown == f"Mock OCR text of page {page}."


def test_e4_8_a_request_that_names_no_page_gets_page_one(mock: Any) -> None:
    assert answer(mock, vision_request(None)).page == 1


def test_e4_8_the_image_bytes_are_not_part_of_the_answer(mock: Any) -> None:
    status, reply = mock.chat(vision_request(3))
    assert status == 200 and PNG_B64[:40] not in json.dumps(reply)


def test_e4_8_the_same_request_gets_the_same_bytes(mock: Any) -> None:
    first = mock.chat(vision_request(2))
    assert mock.chat(vision_request(2)) == first


def test_e4_8_the_fallback_model_answers_too(mock: Any) -> None:
    assert answer(mock, vision_request(4, "sbam-ocr-fallback")).page == 4


def test_e4_8_the_failure_scenarios_apply_to_ocr(mock: Any) -> None:
    mock.set_scenario("sbam-ocr", "schema_invalid")
    status, reply = mock.chat(vision_request(2))
    assert status == 200 and reply["choices"][0]["message"]["content"] == srv.INVALID_BODY
    mock.set_scenario("sbam-ocr", "5xx_all")
    assert mock.chat(vision_request(2))[0] == 503


def test_e4_8_other_aliases_still_need_a_recorded_fixture(mock: Any) -> None:
    body = vision_request(1, "sbam-sow-primary")
    body["metadata"] = {"prompt_id": "never-recorded"}
    status, reply = mock.chat(body)
    assert status == 422 and reply["error"]["code"] == "MOCK_FIXTURE_MISSING"
