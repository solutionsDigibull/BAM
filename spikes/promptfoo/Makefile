# Spike E0.12 (#30). From the repo root: make -C spikes/promptfoo <target>   (Git Bash / WSL2 on Windows)
#
#   run    E0.12-T01/T05/T06  3 cases, both containers --network none -> exit 0, total 3 passed 3
#   t02    E0.12-T02          output without state_handoff_id -> wrapper exits 1 naming output_schema
#   t03    E0.12-T03          tool prints plain text -> is-json fails, no crash (exit 1)
#   t04    E0.12-T04          empty suite -> rejected before Promptfoo runs (exit 2)
#   t06    E0.12-T06          the promptfoo container cannot reach the internet
#   local  same as run, without Docker (needs uv + Node >= 22.22)
COMPOSE := docker compose
UV_TOOL := uv run -q --no-project --python 3.12 --with fastmcp==4.1.0 python tool/harness.py

.PHONY: build run t02 t03 t04 t06 local clean

build:
	$(COMPOSE) build

run: build
	mkdir -p out
	$(COMPOSE) run --rm tool
	$(COMPOSE) run --rm promptfoo

t02: build
	mkdir -p out
	$(COMPOSE) run --rm -e SPIKE_BREAK=missing_handoff tool
	$(COMPOSE) run --rm promptfoo; test $$? -eq 1 && echo "T02 OK: failed as expected"

t03: build
	mkdir -p out
	$(COMPOSE) run --rm -e SPIKE_BREAK=plain_text tool
	$(COMPOSE) run --rm promptfoo; test $$? -eq 1 && echo "T03 OK: failed as expected, no crash"

t04: build
	mkdir -p out
	$(COMPOSE) run --rm promptfoo /spike/empty_suite.yaml; test $$? -eq 2 && echo "T04 OK: empty suite rejected"

t06: build
	$(COMPOSE) run --rm --entrypoint node promptfoo -e "fetch('https://example.com').then(() => { console.log('NETWORK REACHABLE'); process.exit(1) }, (e) => console.log('T06 OK: no network:', e.cause?.code ?? e.message))"

local:
	npm --prefix promptfoo install --no-audit --no-fund --loglevel=error
	$(UV_TOOL)
	node promptfoo/run_suite.mjs

clean:
	rm -rf out
