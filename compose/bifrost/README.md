# Bifrost aliases and the mock LLM (E1.15)

Callers send `model: "sbam-sow"` (one of the six aliases in `aliases.json`) to Bifrost at
`http://bifrost:8080/v1/chat/completions`. Nothing else is accepted.

Cognee, inside `worker-knowledge`, has two aliases of its own (#78, #259): `sbam-knowledge` for graph
extraction (chat) and `sbam-embed` for embeddings (`/v1/embeddings`). It sends them as `openai/sbam-knowledge`
and `openai/sbam-embed`; LiteLLM strips the `openai/` prefix, so Bifrost sees the bare alias. `sbam-embed`
falls back to the same model, because another embedding model would change the vector size.

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
  network: `ok`, `schema_invalid`, `schema_invalid:N` (invalid for the first N calls of a prompt, then valid),
  `timeout`, `5xx` (primary model only), `5xx_all`. A re-ask adds an assistant and a user turn; the mock counts
  the assistant turns and finds the fixture by the original prompt, so re-asks work without new fixtures.
- Callers re-ask with `sbam_workers.llm.reask.run_structured` (E1.15-T05): at most 3 re-asks, errors only in the
  re-ask message, then `SchemaInvalidAfterRetries`. It takes any async caller, so the E6.1 Bifrost client reuses it.
- It refuses to start with `LLM_PROVIDER=mock` when `SBAM_ENV` is `staging` or `production`.
- Cognee's aliases need no fixtures (`"mock": "generated"` and `"kind": "embedding"` in `aliases.json`):
  - `sbam-knowledge` answers an unseen input with JSON built from the schema the caller sends (in
    `response_format`, or the ```json block LiteLLM puts in the system prompt). A knowledge-graph schema gets the
    input's capitalised words and codes as nodes, linked in order; no schema gets the plain answer `test`
    (Cognee's connection check).
  - `sbam-embed` is served on `/v1/embeddings`: one unit vector per input, hashed from its words (same text, same
    vector; shared words, close vectors). Size: `dimensions` in the request, else `LLM_MOCK_EMBEDDING_DIMENSIONS`
    (1536).
  - The scenarios apply to both. A real Cognee `cognify` therefore runs free in CI and on laptops.
- `sbam-sow`, `sbam-isatvon` and `sbam-ocr` fixtures are real (the first two validate against `SowDocument` and
  `SubSkill`). `sbam-codegen`, `sbam-testgen` and `sbam-corrector` use a placeholder shape because their output
  contracts are not in `server/contracts` yet. Replace them when the contracts land.
