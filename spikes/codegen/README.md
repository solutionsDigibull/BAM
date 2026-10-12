# Spike E0.11 (#29): hybrid codegen (Jinja2 skeleton + LLM bodies)

Turns a Skill Manifest into a FastMCP Sub-Skill (TD §12). Jinja2 renders everything fixed, the LLM fills
only marked slots, and five AST gates prove it stayed inside them. Findings and recommendation:
[`docs/spikes/codegen.md`](../../docs/spikes/codegen.md).

Self-contained: nothing here is imported by `server/` or `client/`, and CI doesn't run it. It runs from
`server/` only so that it can use the real contracts (`SkillManifest`, `gate_expr`).

## Run

From the repo root (Git Bash / WSL2 on Windows). You need `uv` and `make`; no Docker and no API key, because
the LLM is a replay mock by default:

```bash
make -C spikes/codegen help          # list targets
make -C spikes/codegen generate      # T02: MS01.1 end to end, 8/8 gates
make -C spikes/codegen test          # T01-T08 as pytest (23 tests)
```

| Target | Test | Expect |
|---|---|---|
| `selfcheck` | T01, T03 | `7/7 checks passed` (render.py escaping, sandbox, StrictUndefined) |
| `render-twice` | T01 | `T01 OK: both renders byte-identical` |
| `generate` / `generate-bom` | T02 / edge | all `[PASS]`, exit 0 |
| `t03` | T03 | payload shown literally escaped, `T03 OK` |
| `t04` `t05` `t06` `t07` | T04–T07 | exactly one gate `[FAIL]` with `file:line`, then `tNN OK` |
| `t08` / `t08-recover` | T08 | `invalid after 4 attempts … no files assembled` / passes on `attempts 2` |
| `lint` | | ruff, black, mypy `--strict` clean on the spike code |
| `live` | | same as `generate` but against the Bifrost alias `sbam-codegen` (`BIFROST_URL`, `BIFROST_API_KEY`) |

Output goes to `spikes/codegen/out/<name>/` (git-ignored):
- `<package>/*.py`: the generated code;
- `bundle.json`: sha256 per file + bundle;
- `gates.json`: `{check, passed, findings[{file, line, message}]}`.

## Layout

```text
codegen/
  render.py       safe Jinja2 env + filters (pylit, docstring, pyident, pyvalue); self-check in __main__
  context.py      SkillManifest (validated) -> template context; gate_outputs via gate_expr
  bodies.py       CodeBodies schema, prompt, Pydantic AI agent (retries=3), mock + Bifrost models
  assemble.py     render skeleton, fill slots (re-indented), Black, write bundle + hashes
  ast_gates.py    AST_SKELETON, AST_CONFORMANCE, AST_DECOUPLING, AST_NO_MANUAL_LOOP, AST_HANDOFF
  cli.py          render | generate (+ ruff / black --check / mypy --strict on the bundle)
templates/fastmcp-loopy/0.1.0/
  template.yaml   files, render order, context per file, slot -> CodeBodies field
  *.py.j2         server, models, state, state_handoff (fixed); action_tool, check_tool (slots)
fixtures/
  sk_bom_scrub_manifest.json   second MSC-domain skill (MS04.2_BOM_Scrubber), different inputs
  mock_bodies/*.yaml           good bodies for both skills + seeded violations t04-t08
tests/test_codegen_spike.py    E0.11-T01..T08 + edge cases
```

## Slots

| Slot | `CodeBodies` field | File | What the LLM writes |
|---|---|---|---|
| `helpers` | `helpers` | `action_tool.py`, `check_tool.py` | module-level pure functions only |
| `action` | `action_body` | `action_tool.py` | body of the action tool; must call `state_handoff.append(...)` |
| `check` | `check_body` | `check_tool.py` | body of the check tool; returns every `GATE_OUTPUTS` key |
| `state_queries` | `state_queries` | `state.py` | a `{name: "SELECT … ?"}` dict, rendered by the assembler (data, not code) |
