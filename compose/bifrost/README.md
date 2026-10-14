# Bifrost aliases and the mock LLM (E1.15)

Callers send `model: "sbam-sow"` (one of the six aliases in `aliases.json`) to Bifrost at
`http://bifrost:8080/v1/chat/completions`. Nothing else is accepted.

| `LLM_PROVIDER` | Bifrost config mounted | Upstream |
|---|---|---|
| `mock` (default, CI) | `config.mock.json` | `sbam-llm-mock` (internal network, no key) |
| `bifrost` | `config.bifrost.json` | OpenRouter, key from `OPENROUTER_API_KEY` (bifrost container only) |

## Change a model or add an alias

Edit `aliases.json`, then `python compose/bifrost/gen_config.py`. Commit the two generated configs. A unit
test fails when they drift (`--check`).

## How it works (Bifrost v2.2.6)

- Bifrost has no "alias" key. Each alias is a **routing rule** (`model == "sbam-sow"` → primary model, with the
  fallback model in `fallbacks`). Fallbacks run on 5xx, 429 and network errors. `priority` must be unique per rule.
- Routing rules belong to Bifrost's governance module, which needs the **config store**. It is a SQLite file inside
  the container, rebuilt from the JSON on every start (`source_of_truth: config.json`).
- The OpenRouter key's `models` list holds only the primary and fallback ids, so `gpt-4o` or
  `openrouter/openai/gpt-4o` fail in Bifrost with a 400 and no upstream call.
- `enforce_auth_on_inference` is `false`: Bifrost is reachable only from the internal network and from
  `127.0.0.1`. Virtual keys per consumer (ADR 0001) are a later issue.
- Bifrost forwards the model id and the body `metadata`, not request headers. The mock therefore gets the alias
  from the model id (`sbam-sow-primary`, `sbam-sow-fallback`) and the prompt from `metadata.prompt_id`. In
  `bifrost` mode the real models receive `metadata` too, so pass `prompt_id` only when you need a fixture.

## The mock (`compose/docker/llm-mock/server.py`)

- Fixtures: `server/tests/llm-mock/<alias>/*.json` with `prompt_id`, `input_hash` (sha256 of the canonical
  `messages`), the recorded `messages` and `response` (JSON) or `response_text`. An unseen input is a 422
  `MOCK_FIXTURE_MISSING` whose message gives the hash to record. Same input, same bytes.
- Scenarios, per call with `metadata.mock_scenario` or for an alias with `PUT /__mock/scenario` from inside the
  network: `ok`, `schema_invalid`, `timeout`, `5xx` (primary model only), `5xx_all`.
- It refuses to start with `LLM_PROVIDER=mock` when `SBAM_ENV` is `staging` or `production`.
- `sbam-sow`, `sbam-isatvon` and `sbam-ocr` fixtures are real (the first two validate against `SowDocument` and
  `SubSkill`). `sbam-codegen`, `sbam-testgen` and `sbam-corrector` use a placeholder shape because their output
  contracts are not in `server/contracts` yet. Replace them when the contracts land.
