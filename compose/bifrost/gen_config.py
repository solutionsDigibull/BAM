#!/usr/bin/env python3
"""Generate the Bifrost configs from `aliases.json` (E1.15, TD §5.4).

    python compose/bifrost/gen_config.py           # rewrite config.mock.json and config.bifrost.json
    python compose/bifrost/gen_config.py --check   # exit 1 if a committed file differs (CI, unit test)

Compose mounts `config.${LLM_PROVIDER:-mock}.json`:
- `mock`:       provider `openrouter` pointed at the sbam-llm-mock service. Models are `<alias>-primary` and
                `<alias>-fallback`, so the mock can tell which alias a call was for.
- `bifrost`:    the real OpenRouter models from `aliases.json`. The key is read from `OPENROUTER_API_KEY`
                in the bifrost container only (ADR 0001).

Each alias is a Bifrost routing rule (`model == "<alias>"` -> primary, with the fallback model). The provider
key lists only these models, so any other model id (`gpt-4o`, `openrouter/openai/gpt-4o`) is rejected by
Bifrost without an upstream call. Routing rules live in Bifrost's governance module, which needs the config
store (SQLite, rebuilt from this file on every start because `source_of_truth` is `config.json`).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MODES = ("mock", "bifrost")
MOCK_BASE_URL = "http://sbam-llm-mock:8091"


def load_aliases() -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = json.loads((HERE / "aliases.json").read_text())
    return data


def model_ids(alias: str, spec: dict[str, str], mode: str) -> tuple[str, str]:
    if mode == "mock":
        return f"{alias}-primary", f"{alias}-fallback"
    return spec["primary"], spec["fallback"]


def build_config(mode: str) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    aliases = load_aliases()
    rules = []
    allowed: list[str] = []
    for priority, (alias, spec) in enumerate(aliases.items(), start=1):
        primary, fallback = model_ids(alias, spec, mode)
        for model in (primary, fallback):
            if model not in allowed:
                allowed.append(model)
        rules.append(
            {
                "id": f"alias-{alias}",
                "name": alias,
                "cel_expression": f'model == "{alias}"',
                "targets": [{"provider": "openrouter", "model": primary, "weight": 1}],
                "fallbacks": [f"openrouter/{fallback}"],
                "enabled": True,
                # Bifrost allows one rule per priority within a scope.
                "priority": priority,
            }
        )
    if mode == "mock":
        key = {"name": "mock", "value": "mock-key", "models": allowed, "weight": 1.0}
        network: dict[str, Any] = {
            "base_url": MOCK_BASE_URL,
            "allow_private_network": True,
            "max_retries": 0,
        }
    else:
        key = {
            "name": "sbam",
            "value": "env.OPENROUTER_API_KEY",
            "models": allowed,
            "weight": 1.0,
        }
        network = {"max_retries": 1}
    return {
        "$schema": "https://www.getbifrost.ai/schema",
        # Bifrost listens on the internal network only (host port is 127.0.0.1); callers send no key.
        "client": {"drop_excess_requests": False, "enforce_auth_on_inference": False},
        "providers": {"openrouter": {"keys": [key], "network_config": network}},
        "governance": {"routing_rules": rules},
        "source_of_truth": "config.json",
        "config_store": {
            "enabled": True,
            "type": "sqlite",
            "config": {"path": "/app/data/config.db"},
        },
    }


def render(mode: str) -> str:
    return json.dumps(build_config(mode), indent=2) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a committed config is stale")
    args = parser.parse_args(argv)
    stale = []
    for mode in MODES:
        path = HERE / f"config.{mode}.json"
        text = render(mode)
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(path.name)
        else:
            path.write_text(text)
    if stale:
        print(f"stale: {', '.join(stale)}; run python compose/bifrost/gen_config.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
