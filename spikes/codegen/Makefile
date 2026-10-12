# Spike E0.11 (#29). From the repo root: make -C spikes/codegen <target>   (Git Bash / WSL2 on Windows)
# Everything runs from server/ (so sbam_contracts is importable) with the mock LLM: no key, no Docker.
#
#   selfcheck     render.py self-check: escaping, sandbox, StrictUndefined (7 checks)
#   render-twice  E0.11-T01  render MS01.1 twice -> byte-identical files and bundle sha256
#   generate      E0.11-T02  MS01.1 end to end -> 6 files, 5 AST gates + ruff/black/mypy pass
#   generate-bom  edge case  SK-BOM scrub (different inputs) with the same template -> all pass
#   t03           E0.11-T03  '{{ 7*7 }} """ import os' in action_step -> literal, escaped, parses
#   t04 .. t07    E0.11-T04..T07 seeded violation -> the matching AST gate fails with file:line
#   t08           E0.11-T08  LLM output missing check_body -> 4 attempts, exit 2, nothing written
#   t08-recover   E0.11-T08  invalid first answer, valid re-ask -> passes on attempt 2
#   test          pytest for all of the above (23 tests)
#   lint          ruff + black --check + mypy --strict on the spike code
#   live          optional: real LLM through Bifrost (needs BIFROST_URL, BIFROST_API_KEY)

PINS   := --with jinja2==3.1.6 --with pyyaml==6.0.2 --with fastmcp==4.1.0 --with pydantic-ai-slim==2.50.0
UV     := cd ../../server && PYTHONPATH=../spikes/codegen uv run -q $(PINS)
CLI    := $(UV) python -m codegen.cli
MS01   := tests/fixtures/isa14/ms01_1_manifest.json
BOM    := ../spikes/codegen/fixtures/sk_bom_scrub_manifest.json
BODIES := ../spikes/codegen/fixtures/mock_bodies
OUT    := ../spikes/codegen/out

.PHONY: help selfcheck render-twice generate generate-bom t03 t04 t05 t06 t07 t08 t08-recover \
        test lint live clean

help:
	@sed -n '1,16p' Makefile

selfcheck:
	$(UV) python ../spikes/codegen/codegen/render.py

render-twice:
	$(CLI) render --manifest $(MS01) --out $(OUT)/t01a
	$(CLI) render --manifest $(MS01) --out $(OUT)/t01b > /dev/null
	diff -r out/t01a out/t01b && echo "T01 OK: both renders byte-identical (same bundle_sha256)"

generate:
	$(CLI) generate --manifest $(MS01) --bodies $(BODIES)/ms01_1_good.yaml --out $(OUT)/ms01_1

generate-bom:
	$(CLI) generate --manifest $(BOM) --bodies $(BODIES)/sk_bom_good.yaml --out $(OUT)/sk_bom

t03:
	$(CLI) render --manifest $(MS01) --out $(OUT)/t03 --action-step '{{ 7*7 }} """ import os' > /dev/null
	grep -n -A1 '7\*7' out/t03/ms01_1_investor_auth_manager/action_tool.py
	if grep -q '49' out/t03/ms01_1_investor_auth_manager/action_tool.py; then exit 1; fi
	$(UV) python -c "import ast; ast.parse(open('$(OUT)/t03/ms01_1_investor_auth_manager/action_tool.py').read())"
	@echo 'T03 OK: payload literal and escaped, not evaluated, file parses'

t04 t05 t06 t07:
	$(CLI) generate --manifest $(MS01) --out $(OUT)/$@ --no-static \
	  --bodies $(BODIES)/$(notdir $(wildcard fixtures/mock_bodies/$@_*.yaml)); \
	  test $$? -eq 1 && echo "$@ OK: seeded violation detected by the expected gate"

t08:
	$(CLI) generate --manifest $(MS01) --bodies $(BODIES)/t08_missing_check_body.yaml --out $(OUT)/t08 \
	  --no-static; test $$? -eq 2 && test ! -e out/t08 && echo "T08 OK: rejected, nothing assembled"

t08-recover:
	$(CLI) generate --manifest $(MS01) --bodies $(BODIES)/t08_retry_then_good.yaml --out $(OUT)/t08r --no-static

test:
	$(UV) pytest ../spikes/codegen/tests -q -p no:cacheprovider -W ignore::DeprecationWarning

lint:
	cd ../../server && uv run -q ruff check --no-cache ../spikes/codegen/codegen ../spikes/codegen/tests
	cd ../../server && uv run -q black --check --config pyproject.toml ../spikes/codegen
	MYPYPATH=.:tests uv run -q --project ../../server $(PINS) --with types-pyyaml \
	  mypy --strict --cache-dir ../../server/.mypy_cache codegen tests

live:
	@test -n "$$BIFROST_URL" || (echo "set BIFROST_URL (and BIFROST_API_KEY) first"; exit 2)
	$(CLI) generate --manifest $(MS01) --out $(OUT)/live

clean:
	rm -rf out
