"""E5.7 Cognee's Bifrost aliases: `sbam-knowledge` (generated JSON), `sbam-embed` (embeddings)."""

import importlib.util
import json
import math
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[3]
BIFROST_DIR = REPO / "compose/bifrost"
FIXTURES = REPO / "server/tests/llm-mock"


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = load("bifrost_gen_config_e5_7", BIFROST_DIR / "gen_config.py")
srv = load("llm_mock_server_e5_7", REPO / "compose/docker/llm-mock/server.py")


# The shape of Cognee 1.6.2's KnowledgeGraph (cognee.shared.data_models), restated so the unit test
# does not import Cognee. The end-to-end check against Cognee itself is E5.7-T04 (integration).
class Node(BaseModel):
    id: str
    name: str
    type: str
    description: str


class Edge(BaseModel):
    source_node_id: str
    target_node_id: str
    relationship_name: str


class KnowledgeGraph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]


class Summary(BaseModel):
    summary: str
    description: str


TEXT = "Supplier Mouser quoted ZEBRA-7731 for 500 PCBA boards to Acme."


@pytest.fixture
def mock() -> Any:
    return srv.build(FIXTURES, BIFROST_DIR / "aliases.json")


def litellm_json_request(model: type[BaseModel], text: str = TEXT) -> dict[str, Any]:
    """What Cognee's LiteLLM adapter sends for a model LiteLLM can't enforce a schema on."""
    schema = json.dumps(model.model_json_schema(), indent=2)
    system = (
        "Extract a knowledge graph.\n\nYou MUST respond with valid JSON conforming to this "
        f"schema:\n```json\n{schema}\n```\nDo not include any text outside the JSON object."
    )
    return {
        "model": "sbam-knowledge-primary",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
        "response_format": {"type": "json_object"},
    }


def content(body: dict[str, Any]) -> str:
    return str(body["choices"][0]["message"]["content"])


# --- aliases and configs ----------------------------------------------------------------------


def test_e5_7_t01_both_configs_route_the_cognee_aliases() -> None:
    assert gen.main(["--check"]) == 0, "run python compose/bifrost/gen_config.py"
    for mode in gen.MODES:
        rules = {r["name"]: r for r in gen.build_config(mode)["governance"]["routing_rules"]}
        assert {"sbam-knowledge", "sbam-embed"} <= set(rules)
        assert rules["sbam-embed"]["cel_expression"] == 'model == "sbam-embed"'
    real = {r["name"]: r for r in gen.build_config("bifrost")["governance"]["routing_rules"]}
    embed = real["sbam-embed"]
    assert embed["targets"][0]["model"] == "openai/text-embedding-3-small"
    assert embed["fallbacks"] == ["openrouter/openai/text-embedding-3-small"]  # same vector size


# --- embeddings -------------------------------------------------------------------------------


def test_e5_7_t02_embeddings_are_deterministic_and_batched(mock: Any) -> None:
    body = {"model": "sbam-embed-primary", "input": ["a", TEXT, "c"], "encoding_format": "float"}
    status, first = mock.embeddings(body)
    _, second = mock.embeddings(body)
    assert status == 200 and first == second
    vectors = [d["embedding"] for d in first["data"]]
    assert [d["index"] for d in first["data"]] == [0, 1, 2]
    assert all(len(v) == srv.EMBEDDING_DIMENSIONS == 1536 for v in vectors)
    assert all(math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, abs_tol=1e-3) for v in vectors)


def test_e5_7_shared_words_make_vectors_close(mock: Any) -> None:
    def vec(text: str) -> list[float]:
        body = mock.embeddings({"model": "sbam-embed-primary", "input": text})[1]
        return list(body["data"][0]["embedding"])

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    canary = vec("ZEBRA-7731")
    assert cos(canary, vec(TEXT)) > cos(canary, vec("Arrow quoted cable harnesses to Globex"))


def test_e5_7_embedding_dimensions_follow_the_request(mock: Any) -> None:
    status, body = mock.embeddings({"model": "sbam-embed-primary", "input": "x", "dimensions": 8})
    assert status == 200 and len(body["data"][0]["embedding"]) == 8


@pytest.mark.parametrize(
    "body",
    [
        {"model": "sbam-sow-primary", "input": "x"},  # a chat alias
        {"model": "text-embedding-3-small", "input": "x"},  # not an alias model
    ],
)
def test_e5_7_embeddings_only_for_the_embedding_alias(mock: Any, body: dict[str, Any]) -> None:
    assert mock.embeddings(body)[0] == 404


BAD_EMBEDDINGS = [{"input": 3}, {"input": ["ok", 4]}, {"input": "x", "dimensions": 0}]


@pytest.mark.parametrize("bad", BAD_EMBEDDINGS)
def test_e5_7_bad_embedding_requests_are_400(mock: Any, bad: dict[str, Any]) -> None:
    assert mock.embeddings({"model": "sbam-embed-primary", **bad})[0] == 400


def test_e5_7_embedding_alias_is_refused_on_chat(mock: Any) -> None:
    status, body = mock.chat({"model": "sbam-embed-primary", "messages": [{"role": "user"}]})
    assert status == 400 and "/v1/embeddings" in body["error"]["message"]


def test_e5_7_scenarios_apply_to_embeddings(mock: Any) -> None:
    mock.set_scenario("sbam-embed", "5xx")
    assert mock.embeddings({"model": "sbam-embed-primary", "input": "x"})[0] == 503
    assert mock.embeddings({"model": "sbam-embed-fallback", "input": "x"})[0] == 200


# --- generated answers (sbam-knowledge) -------------------------------------------------------


def test_e5_7_knowledge_graph_from_the_schema_in_the_prompt(mock: Any) -> None:
    status, body = mock.chat(litellm_json_request(KnowledgeGraph))
    assert status == 200
    graph = KnowledgeGraph.model_validate_json(content(body))
    ids = [n.id for n in graph.nodes]
    assert "ZEBRA-7731" in ids and "Mouser" in ids
    assert len(graph.edges) == len(graph.nodes) - 1
    assert all(e.source_node_id in ids and e.target_node_id in ids for e in graph.edges)


def test_e5_7_schema_in_response_format_is_used_too(mock: Any) -> None:
    body = litellm_json_request(Summary)
    body["messages"][0]["content"] = "Summarise."
    body["response_format"] = {
        "type": "json_schema",
        "json_schema": {"name": "Summary", "schema": Summary.model_json_schema()},
    }
    Summary.model_validate_json(content(mock.chat(body)[1]))


def test_e5_7_same_input_same_answer(mock: Any) -> None:
    first = mock.chat(litellm_json_request(KnowledgeGraph))
    assert first == mock.chat(litellm_json_request(KnowledgeGraph))


def test_e5_7_no_schema_gets_cognee_s_connection_test_answer(mock: Any) -> None:
    status, body = mock.chat(
        {
            "model": "sbam-knowledge-primary",
            "messages": [
                {"role": "system", "content": 'Respond to me with the following string: "test"'},
                {"role": "user", "content": "test"},
            ],
        }
    )
    assert status == 200 and content(body) == "test"


def test_e5_7_schema_invalid_scenario_still_applies(mock: Any) -> None:
    mock.set_scenario("sbam-knowledge", "schema_invalid")
    assert content(mock.chat(litellm_json_request(KnowledgeGraph))[1]) == srv.INVALID_BODY


def test_e5_7_recorded_aliases_still_need_fixtures(mock: Any) -> None:
    body = litellm_json_request(KnowledgeGraph)
    body["model"] = "sbam-sow-primary"
    status, err = mock.chat(body)
    assert status == 422 and err["error"]["code"] == "MOCK_FIXTURE_MISSING"


def test_e5_7_generated_values_cover_json_schema_basics() -> None:
    schema = {
        "type": "object",
        "required": ["n", "k", "flag", "tags", "choice", "maybe"],
        "properties": {
            "n": {"type": "integer", "minimum": 2},
            "k": {"type": "number"},
            "flag": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 2},
            "choice": {"enum": ["A", "B"]},
            "maybe": {"anyOf": [{"type": "null"}, {"type": "string"}]},
        },
    }
    value = srv.instance(schema, schema, ["X"])
    assert value == {
        "n": 2,
        "k": 0.0,
        "flag": False,
        "tags": ["mock X", "mock X"],
        "choice": "A",
        "maybe": "mock X",
    }
