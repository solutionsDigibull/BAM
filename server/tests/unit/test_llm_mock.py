"""E1.15 static and unit checks: Bifrost alias configs, the llm-mock upstream and its fixtures.

The Bifrost-in-Docker checks (T01, T02, T04, T06, T08 against the real stack) are in
tests/integration/test_llm_gateway.py. Caller-side retries (T05: re-ask up to 3 times, then
SchemaInvalidAfterRetries) belong to the LLM client and are not built yet; here T05 only proves that
the mock can emit an invalid body and that the schema rejects it.
"""

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.isatvon14 import SubSkill
from sbam_contracts.sow import SowDocument

REPO = Path(__file__).resolve().parents[3]
BIFROST_DIR = REPO / "compose/bifrost"
FIXTURES = REPO / "server/tests/llm-mock"
ALIASES = [
    "sbam-ocr",
    "sbam-sow",
    "sbam-isatvon",
    "sbam-codegen",
    "sbam-testgen",
    "sbam-corrector",
]


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = load("bifrost_gen_config", BIFROST_DIR / "gen_config.py")
srv = load("llm_mock_server", REPO / "compose/docker/llm-mock/server.py")
SMOKE = [{"role": "user", "content": "sbam-mock-smoke"}]


@pytest.fixture
def mock() -> Any:
    return srv.build(FIXTURES, BIFROST_DIR / "aliases.json")


def chat(
    mock: Any, model: str, messages: Any = SMOKE, **metadata: str
) -> tuple[int, dict[str, Any]]:
    body = {
        "model": model,
        "messages": messages,
        "metadata": {"prompt_id": "smoke", **metadata},
    }
    return mock.chat(body)  # type: ignore[no-any-return]


# --- aliases and generated Bifrost configs --------------------------------------------------------


def test_e1_15_t01_six_aliases_each_with_primary_and_fallback() -> None:
    aliases = gen.load_aliases()
    assert list(aliases) == ALIASES
    for mode in gen.MODES:
        rules = gen.build_config(mode)["governance"]["routing_rules"]
        assert [r["name"] for r in rules] == ALIASES
        assert len({r["priority"] for r in rules}) == len(rules)  # Bifrost: one rule per priority
        for rule in rules:
            alias = rule["name"]
            primary, fallback = gen.model_ids(alias, aliases[alias], mode)
            assert primary != fallback
            assert rule["cel_expression"] == f'model == "{alias}"'
            assert [t["model"] for t in rule["targets"]] == [primary]
            assert rule["fallbacks"] == [
                f"{gen.fallback_provider(aliases[alias], mode)}/{fallback}"
            ]


def test_e1_15_committed_configs_match_aliases_json() -> None:
    assert gen.main(["--check"]) == 0, "run python compose/bifrost/gen_config.py"


def test_e1_15_t02_only_alias_models_are_allowed_upstream() -> None:
    for mode in gen.MODES:
        config = gen.build_config(mode)
        rules = config["governance"]["routing_rules"]
        referenced = {f"openrouter/{t['model']}" for r in rules for t in r["targets"]} | {
            f for r in rules for f in r["fallbacks"]
        }
        allowed = {
            f"{name}/{model}"
            for name, provider in config["providers"].items()
            for key in provider["keys"]
            for model in key["models"]
        }
        assert allowed == referenced  # nothing else is callable upstream
        assert "openrouter/gpt-4o" not in allowed and "openrouter/openai/gpt-4o" not in allowed
        assert config["client"]["enforce_auth_on_inference"] is False  # internal network only


def test_e1_15_nemotron_fallback_goes_to_nvidia_and_ocr_keeps_a_vision_model() -> None:
    config = gen.build_config("bifrost")
    nvidia = config["providers"]["nvidia"]
    assert nvidia["keys"][0]["value"] == "env.NVIDIA_API_KEY"  # env only, never a literal key
    assert nvidia["custom_provider_config"]["base_provider_type"] == "openai"
    assert nvidia["network_config"]["base_url"] == gen.NVIDIA_BASE_URL
    by_alias = {r["name"]: r["fallbacks"] for r in config["governance"]["routing_rules"]}
    assert by_alias["sbam-sow"] == ["nvidia/nvidia/nemotron-3-super-120b-a12b"]
    assert by_alias["sbam-ocr"] == ["openrouter/anthropic/claude-haiku-5.5"]
    assert "nvidia" not in gen.build_config("mock")["providers"]


def test_e1_15_no_provider_key_literal_is_committed() -> None:
    for name in (
        "config.mock.json",
        "config.bifrost.json",
        "aliases.json",
        "README.md",
    ):
        assert "nvapi-" not in (gen.HERE / name).read_text()


def test_e1_15_openrouter_config_reads_key_from_env_and_mock_config_has_none() -> None:
    real = gen.build_config("bifrost")["providers"]["openrouter"]
    assert real["keys"][0]["value"] == "env.OPENROUTER_API_KEY"
    assert "base_url" not in real["network_config"]
    mocked = gen.build_config("mock")["providers"]["openrouter"]
    assert mocked["network_config"]["base_url"] == gen.MOCK_BASE_URL
    assert "env." not in mocked["keys"][0]["value"]


# --- mock behaviour -------------------------------------------------------------------------------


@pytest.mark.parametrize("alias", ALIASES)
def test_e1_15_t01_every_alias_has_a_fixture_and_the_mock_answers_it(mock: Any, alias: str) -> None:
    files = sorted((FIXTURES / alias).glob("*.json"))
    assert files, f"no fixture for {alias}"
    for path in files:
        data = json.loads(path.read_text())
        assert data["input_hash"] == srv.input_hash(data["messages"]), f"stale hash in {path.name}"
        status, body = mock.chat(
            {
                "model": f"{alias}-primary",
                "messages": data["messages"],
                "metadata": {"prompt_id": data["prompt_id"]},
            }
        )
        assert status == 200 and body["model"] == f"{alias}-primary"


def test_e1_15_t03_same_input_gives_byte_identical_output(mock: Any) -> None:
    first = json.dumps(chat(mock, "sbam-sow-primary"), sort_keys=True).encode()
    second = json.dumps(chat(mock, "sbam-sow-primary"), sort_keys=True).encode()
    assert first == second


def test_e1_15_t03_hash_ignores_key_order_but_not_content() -> None:
    a = [{"role": "user", "content": "x"}]
    b = [{"content": "x", "role": "user"}]
    assert srv.input_hash(a) == srv.input_hash(b)
    assert srv.input_hash(a) != srv.input_hash([{"role": "user", "content": "y"}])


def test_e1_15_t04_primary_5xx_leaves_the_fallback_model_answering(mock: Any) -> None:
    assert chat(mock, "sbam-sow-primary", mock_scenario="5xx")[0] == 503
    status, body = chat(mock, "sbam-sow-fallback", mock_scenario="5xx")
    assert status == 200 and body["model"] == "sbam-sow-fallback"
    assert chat(mock, "sbam-sow-fallback", mock_scenario="5xx_all")[0] == 503


def test_e1_15_t05_schema_invalid_scenario_returns_a_body_the_schema_rejects(
    mock: Any,
) -> None:
    status, ok = chat(mock, "sbam-sow-primary")
    SowDocument.model_validate_json(ok["choices"][0]["message"]["content"])
    status, bad = chat(mock, "sbam-sow-primary", mock_scenario="schema_invalid")
    assert status == 200
    with pytest.raises(ValidationError):
        SowDocument.model_validate_json(bad["choices"][0]["message"]["content"])


def test_e1_15_t05_scenarios_can_be_set_per_alias_and_reset(mock: Any) -> None:
    mock.set_scenario("sbam-sow", "5xx_all")
    assert chat(mock, "sbam-sow-primary")[0] == 503
    assert chat(mock, "sbam-codegen-primary")[0] == 200  # other aliases are untouched
    mock.reset()
    assert chat(mock, "sbam-sow-primary")[0] == 200
    with pytest.raises(ValueError):
        mock.set_scenario("sbam-sow", "explode")
    with pytest.raises(ValueError):
        mock.set_scenario("sbam-nope", "ok")


def test_e1_15_timeout_scenario_sleeps_then_504(mock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(srv.time, "sleep", slept.append)
    mock.timeout_s = 65.0
    assert chat(mock, "sbam-sow-primary", mock_scenario="timeout")[0] == 504
    assert slept == [65.0]


def test_e1_15_t08_missing_fixture_fails_loudly_and_names_the_hash(mock: Any) -> None:
    other = [{"role": "user", "content": "never recorded"}]
    status, body = chat(mock, "sbam-sow-primary", other)
    assert status == 422 and body["error"]["code"] == "MOCK_FIXTURE_MISSING"
    assert srv.input_hash(other) in body["error"]["message"]
    status, body = chat(mock, "sbam-sow-primary", prompt_id="other_prompt")
    assert status == 422 and body["error"]["code"] == "MOCK_FIXTURE_MISSING"


@pytest.mark.parametrize("model", ["gpt-4o", "openai/gpt-4o", "sbam-sow", "sbam-nope-primary", ""])
def test_e1_15_t02_mock_rejects_models_that_are_not_alias_models(mock: Any, model: str) -> None:
    status, body = chat(mock, model)
    assert status == 404 and body["error"]["code"] == "MOCK_UNKNOWN_MODEL"
    assert mock.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"model": "sbam-sow-primary", "messages": []},
        {"model": "sbam-sow-primary", "messages": "hi"},
        {"model": "sbam-sow-primary", "messages": SMOKE, "metadata": "x"},
        {
            "model": "sbam-sow-primary",
            "messages": SMOKE,
            "metadata": {"mock_scenario": "boom"},
        },
    ],
)
def test_e1_15_mock_answers_malformed_requests_with_400(mock: Any, body: dict[str, Any]) -> None:
    status, payload = mock.chat(body)
    assert status == 400 and payload["error"]["code"].startswith("MOCK_")


def test_e1_15_isatvon_fixture_is_schema_valid_sub_skills() -> None:
    fixture = json.loads((FIXTURES / "sbam-isatvon/smoke.json").read_text())
    for raw in fixture["response"]:
        SubSkill.model_validate(raw)


# --- production guard -----------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["production", "staging", "PRODUCTION", "prod"])
def test_e1_15_t09_mock_provider_is_refused_in_staging_and_production(env: str) -> None:
    with pytest.raises(SystemExit, match="forbidden"):
        srv.check_env(env, "mock")


@pytest.mark.parametrize(
    ("env", "provider"),
    [
        ("development", "mock"),
        ("ci", "mock"),
        ("production", "bifrost"),
        ("staging", "bifrost"),
    ],
)
def test_e1_15_t09_other_combinations_start(env: str, provider: str) -> None:
    srv.check_env(env, provider)


# --- fixtures -------------------------------------------------------------------------------------


def test_e1_15_sow_fixture_is_the_schema_valid_sow_sample() -> None:
    fixture = json.loads((FIXTURES / "sbam-sow/smoke.json").read_text())
    sample = json.loads((REPO / "server/tests/fixtures/sow/ms01_1_sow.json").read_text())
    assert fixture["response"] == sample  # copy must not drift from the contract-checked sample
    SowDocument.model_validate(fixture["response"])


def test_e1_15_fixtures_are_unique_and_load() -> None:
    fixtures = srv.load_fixtures(FIXTURES)
    assert {alias for alias, _, _ in fixtures} == set(ALIASES)


# --- T06 / T07: the OpenRouter key only reaches bifrost -------------------------------------------


COMPOSE_TEXT = (REPO / "compose/compose.yaml").read_text()


def service_block(name: str) -> str:
    """Text of one top-level service (2-space indent) up to the next service or comment."""
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|^\S|\Z)", COMPOSE_TEXT, re.M | re.S)
    assert match, f"service {name} not found"
    return match.group(1)


@pytest.mark.parametrize("secret", ["OPENROUTER_API_KEY", "NVIDIA_API_KEY"])
def test_e1_15_t06_only_bifrost_gets_the_provider_keys(secret: str) -> None:
    holders = [
        name
        for name in re.findall(r"^  ([a-z][\w-]*):\n", COMPOSE_TEXT, re.M)
        if secret in service_block(name)
    ]
    assert holders == ["bifrost"]
    assert secret not in COMPOSE_TEXT.split("\nservices:")[0]  # the shared worker anchor
    assert not re.search(
        r"^\s+env_file:", COMPOSE_TEXT, re.M
    )  # would hand every secret to a service


def test_e1_15_t07_mock_runs_on_the_internal_network_without_secrets() -> None:
    mock = service_block("sbam-llm-mock")
    assert "networks: [sbam_internal]" in mock
    assert "ports:" not in mock
    assert re.search(r"^  sbam_internal:\n    internal: true", COMPOSE_TEXT, re.M)  # no internet
    assert "sbam-llm-mock: { condition: service_healthy }" in service_block("bifrost")
