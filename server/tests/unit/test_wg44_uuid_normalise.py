# ruff: noqa: E501
"""WG-44 (#932): the mock key ignores the ids of uploaded documents, so a second upload replays a recording.

Prompts render `[chunk c1 doc=<uuid> line=1]` (sow/context.py). The same documents uploaded twice get new ids. These
tests use synthetic prompts only and no network.
"""

import importlib.util
import json
import re
import uuid
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "server/tests/llm-mock"


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


srv = load("llm_mock_server_wg44", REPO / "compose/docker/llm-mock/server.py")
rec = load("llm_record_proxy_wg44", REPO / "scripts/llm_record_proxy.py")


def prompt(
    doc_id: str, text: str = "Rule A applies.", task: str = "write the SOW"
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": task},
        {
            "role": "user",
            "content": f"[chunk c1 doc={doc_id} line=1]\n{text}\n[entity e1 doc={doc_id} line=2] Acme",
        },
    ]


def test_wg44_t01_two_uploads_of_the_same_documents_hash_the_same() -> None:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert srv.input_hash(prompt(a)) == srv.input_hash(prompt(b))
    assert rec.input_hash(prompt(a)) == rec.input_hash(prompt(b))


def test_wg44_t02_changed_document_text_hashes_differently() -> None:
    a = str(uuid.uuid4())
    assert srv.input_hash(prompt(a)) != srv.input_hash(prompt(a, text="Rule B applies."))


def test_wg44_t03_different_prompt_hashes_differently() -> None:
    a = str(uuid.uuid4())
    assert srv.input_hash(prompt(a)) != srv.input_hash(prompt(a, task="write something else"))


def test_wg44_t04_a_uuid_outside_a_chunk_header_still_changes_the_hash() -> None:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    one = prompt(a, text=f"see {a}")
    two = prompt(b, text=f"see {b}")
    assert srv.input_hash(one) != srv.input_hash(two)
    other = [{"role": "user", "content": f"doc={a}"}]  # `doc=` outside a header is not rewritten
    assert srv.normalise_ids(json.dumps(other)) == json.dumps(other)


def test_wg44_t05_old_committed_fixtures_still_resolve() -> None:
    paths = sorted(FIXTURES.glob("*/*.json"))
    assert len(paths) >= 6
    for path in paths:
        data = json.loads(path.read_text())
        messages = data.get("messages")
        if messages is not None and "[chunk " not in json.dumps(messages):
            assert srv.input_hash(messages) == data["input_hash"], path.name
    assert srv.load_fixtures(FIXTURES)


def test_wg44_t06_the_recorder_writes_the_normalised_key(tmp_path: Path) -> None:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    recorder = rec.Recorder(tmp_path, 1.0)
    body = {
        "model": "sbam-sow",
        "messages": prompt(a),
        "metadata": {"prompt_id": "sow-generate"},
    }
    reply = {"choices": [{"message": {"content": "{}"}}], "usage": {"cost": 0}}
    path = recorder.record(body, reply)
    assert path is not None
    data = json.loads(path.read_text())
    assert data["input_hash"] == srv.input_hash(prompt(b))  # a second upload finds it
    fixtures = {(path.parent.name, data["prompt_id"], data["input_hash"]): data}
    assert ("sbam-sow", "sow-generate", srv.input_hash(prompt(b))) in fixtures


def test_wg44_t07_the_recorder_and_the_mock_normalise_alike() -> None:
    a = str(uuid.uuid4())
    text = json.dumps(prompt(a))
    assert srv.normalise_ids(text) == rec.normalise_ids(text)
    assert "<DOC_UUID>" in srv.normalise_ids(text) and a not in srv.normalise_ids(text)


def test_wg44_t08_a_uuid_ref_is_normalised_too() -> None:
    a, r1, r2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    one = [{"role": "user", "content": f"[chunk {r1} doc={a} line=1] x"}]
    two = [{"role": "user", "content": f"[chunk {r2} doc={uuid.uuid4()} line=1] x"}]
    assert srv.input_hash(one) == srv.input_hash(two)


def test_wg44_t09_no_key_like_strings() -> None:
    src = Path(__file__).read_text()
    assert not re.search(r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}", src)
