# Spike E0.11 (#29): hybrid codegen (Jinja2 skeleton + LLM bodies) on MS01.1

## Question

Can SBAM generate a Sub-Skill's FastMCP code (TD §12) by rendering a fixed Jinja2 skeleton from the Skill
Manifest and letting the LLM (`sbam-codegen`) write **only** marked slot bodies? And can AST gates prove,
before anything runs, that the LLM didn't touch the skeleton, couple the check tool to the action tool,
write its own retry loops or skip the audit handoff?

## Setup

| Item | Value |
|---|---|
| Prototype | `spikes/codegen/` (`codegen/` package, `templates/fastmcp-loopy/0.1.0/`, fixtures, tests, Makefile) |
| Templates | Jinja2 `3.1.6`, `SandboxedEnvironment` + `StrictUndefined`, `autoescape=False`, filters `pylit` / `docstring` / `pyident` / `pyvalue` |
| LLM layer | Pydantic AI `pydantic-ai-slim==2.50.0`, `Agent(output_type=CodeBodies, retries=3)`. Model = Bifrost alias `sbam-codegen` if `BIFROST_URL` is set, otherwise a `FunctionModel` mock that replays YAML fixtures |
| Runtime | FastMCP `4.1.0`, Pydantic 2, Python 3.12 |
| Static gate | ruff / black / mypy `--strict` with `server/pyproject.toml` settings (line length 100) |
| Manifests | MS01.1 (`server/tests/fixtures/isa14/ms01_1_manifest.json`, #20) and a second MSC-domain skill SK-BOM scrub (`fixtures/sk_bom_scrub_manifest.json`, `MS04.2_BOM_Scrubber`), both validated by `SkillManifest` |
| Reused contracts | `sbam_contracts.manifest.SkillManifest` (#20), `sbam_contracts.gate_expr.parse / identifiers / evaluate` (#19) |

Pipeline as built: manifest → `context.py` (validated manifest → template context) → `render_skeleton` →
`bodies.generate` (one structured LLM call, schema-validated, re-asked ≤ 3×) → `assemble` (slots filled,
re-indented, Black) → `bundle.json` (sha256 per file + bundle) → 5 AST gates → ruff / black / mypy.

## Findings

| ID | Scenario | Result |
|---|---|---|
| E0.11-T01 | render MS01.1 twice | byte-identical files and `bundle_sha256` (`make render-twice`, `diff -r` clean); LF only |
| E0.11-T02 | MS01.1 + mock bodies | 6 files + `__init__.py`, `ast.parse` OK, **8/8 gates pass** (5 AST + RUFF, BLACK, MYPY). The bundle also *runs*: both tools answer over a FastMCP client, the handoff row is written, and `success_gate` evaluates `True` for the right OTP and `False` for a wrong one |
| E0.11-T03 | `action_step = '{{ 7*7 }} """ import os'` | appears literally, escaped as `{{ 7*7 }} \"\"\" import os`, no `49`, file parses, docstring equals the payload. The sandbox blocks `''.__class__.__mro__…`, and StrictUndefined rejects a missing field |
| E0.11-T04 | check body `from action_tool import run` | `AST_DECOUPLING` fails: `check_tool.py:44 imports from the action_tool module` |
| E0.11-T05 | action body with `while True:` | `AST_NO_MANUAL_LOOP` fails: `action_tool.py:41 while loop` |
| E0.11-T06 | LLM returns the whole tool with `mobile_number` renamed to `phone` | `AST_CONFORMANCE` fails: `action_tool.py:41 slot redefines tool auth_mcp_session_otp_evaluator()`. Editing the protected signature itself fails both `AST_SKELETON` (`protected region differs from fresh render`) and `AST_CONFORMANCE` |
| E0.11-T07 | action body without `state_handoff.append` | `AST_HANDOFF` fails: `state_handoff.append(...) is never called; required_fields: iteration_pass, device_fingerprint, otp_match, access_scope` |
| E0.11-T08 | every answer is missing `check_body` | 4 model calls (1 + 3 re-asks), then `UnexpectedModelBehavior`. CLI exits 2 and **no output directory is created**. With one bad answer and then a good one, generation succeeds on attempt 2 |
| edge | SK-BOM scrub: inputs `string, integer, string, boolean`, a different V parameter, `"EA"` quotes in `action_step` | same template, **no change**, 8/8 gates pass |
| edge | `action_tool == fixed_check_tool` | rejected before rendering (`ManifestError`, ISATVON rule 4) |

Every row is automated in `spikes/codegen/tests/test_codegen_spike.py` (23 tests, about 25 s) and has a
`make` target. On Windows 11 (warm uv cache, wall-clock including interpreter start):
- `render` takes about 5 s;
- `generate` takes about 5.5 s without the static gate and about 13 s with it (mypy dominates).

The generated MS01.1 package is 274 lines.

### Slot design (what worked)

- **4 slots, each mapped to one `CodeBodies` field** (listed in `template.yaml`):

  | Slot | Field | Where |
  |---|---|---|
  | `action` | `action_body` | inside the action tool |
  | `check` | `check_body` | inside the check tool |
  | `helpers` | `helpers` | module level, copied into **both** tool files, so there's no import path between them |
  | `state_queries` | `state_queries` | `state.py` |

  `models.py`, `state_handoff.py` and `server.py` have **no slot**.
- **Markers are comments** (`# >>> LLM_BODY:x` … `# <<< LLM_BODY:x`). The placeholder is `raise NotImplementedError("LLM_BODY:x")`, so an unfilled skeleton imports and fails loudly at exactly the slot.
- **`state_queries` is data, not code.** The LLM returns `{name: SELECT … ?}`, and the assembler writes the dict literal itself. No Python from the LLM ever reaches `state.py`, and `read()` refuses non-`SELECT` at runtime too.
- **Schema validation does most of the security work before assembly:**
  - each body must parse inside a function;
  - bodies may not contain slot markers (so they can't fake a slot end);
  - `helpers` may only define functions;
  - queries must be `SELECT` with exactly one `?`;
  - `extra="forbid"`.

  Every failure becomes a re-ask, which Pydantic AI does for free.
- **Re-indenting each body to its marker** means a body can never escape the function it belongs to.

### Protected regions (what worked)

- `AST_SKELETON` restores every slot to its placeholder, then compares `ast.dump()` with a fresh render. It **must be an AST comparison, not a text diff**: Black rewrites the skeleton (`repr` gives `'…'`, Black prefers `"…"`), so a text diff would always fail.
- Marker sanity is checked first: exactly the declared slots, in order, once each. A body that adds or removes a marker can't move the boundary.
- `template.yaml` is cross-checked against the `.j2` files (`jinja2.meta.find_undeclared_variables` + slot regex) by a test, so the template manifest can't drift from the templates.

### Problems found (input for E-codegen stories)

1. **Use `sbam_build_record.isatvon_detail`, not the flat ISA14 block.** Only the structured form has `fresh_state_parameters` and V `parameters` as identifiers.
   - With the flat block, `failed_login_attempts` (fresh state) is misread as a value the check tool must produce.
   - With the structured block, the check tool returns exactly `otp_match, policy_violation`.
2. **The bodies need V parameters at runtime** (`max_device_limit`). The template now renders them into `state.PARAMETERS`, and the prompt says "never hard-code".
3. **Manifest prose breaks ruff `E501`** (docstrings > 100 chars). Fix: Jinja `wordwrap(88, break_long_words=false)` after escaping. It breaks only at spaces, so `\"` pairs are never split.
4. **Long SQL breaks `E501` too**, and Black can't split strings. The assembler emits implicitly concatenated literals (same string value).
5. **No runtime SQL string-building in the skeleton.** An f-string table name tripped ruff `S608`. The `INSERT` is now a constant rendered at template time (table name through `pyident`).
6. **No write path.** `state` is read-only and the handoff is insert-only, so `n_notification.database_updates` (e.g. "Update investor_profiles with failed attempts") has nowhere to go. SBAM needs a decision: a template-owned `state.write(...)` limited to `v_variables.tables`, with a gate, or the runtime owns those updates.
7. **Types.** 0.1.0 maps only `string / integer / number / boolean`; `array` / `object` fail the render loudly.
   - **Optional inputs** are optional in `SkillInput` but required in the tool signatures.
   - The real template needs defaults, or must reject optional inputs.
8. **SQL paramstyle** is `?` (sqlite, used in tests). psycopg uses `%s`, so the template must own the paramstyle, and the prompt and the `state_queries` validator must follow it.
9. **FastMCP runs sync tools in a worker thread.** The injected DB connection must be thread-safe (pool), which showed up in the runtime test.
10. **`AST_CONFORMANCE` can only prove check-tool outputs when every `return` is a dict literal.** That's fine to require in the prompt; anything else is a finding.
11. Pydantic AI `2.50` has no `output_retries` argument; `retries=3` covers output validation (1 + 3 calls). Set `PYDANTIC_AI_NO_BANNER=1` in workers.

### Not covered by this spike

- **LLM failure rates are not measured.** No Bifrost/OpenRouter key was available, so the LLM path ran only with the replay mock. `make live` runs the same pipeline against `sbam-codegen` once `BIFROST_URL` is set. The suggested measurement is 10 generations per skill, recording:
  - first-try schema-valid %;
  - gate-pass %;
  - re-asks used.
- **TD §12.5 checks not prototyped:**
  - `AST_IMPORTS` (allow-list + forbidden calls);
  - `AST_SQL_SAFETY` beyond the `SELECT`/`?` check;
  - output-schema conformance;
  - `AST_SYNTAX`, which is implicitly covered by `ast.parse` in validation and in every gate.
- `loop_spec.json`, `schema.sql`, ClamAV/Bandit and the correction loop (§12.4) are out of scope.

## Recommendation

Adopt the hybrid design for #112 → #113 → #114 → #117/#118, as follows:

1. **Templates:**
   - Keep `fastmcp-loopy` with the 4-slot layout and `template.yaml` (slot → `CodeBodies` field, context list, supported types).
   - Treat a template version as immutable once used for certification.
   - Fix problems 6–8 in `1.0.0` before the first certified build.
2. **Rendering:**
   - Build the context only from the validated `SkillManifest`, using `sbam_build_record.isatvon_detail`.
   - Manifest text reaches code only through `pylit` / `docstring` / `pyident` / `pyvalue`, with `wordwrap` for prose.
3. **LLM:**
   - Make one structured call returning `CodeBodies`, via the Bifrost alias, with `retries=3`.
   - Never assemble on validation failure.
   - Keep `state_queries` as data.
4. **Gates:**
   - Port `ast_gates.py`: findings `{file, line, message}` → `gate_results`.
   - Add `AST_IMPORTS` and `AST_SQL_SAFETY`.
   - Run the static gate on the written bundle with the pinned ruff/black/mypy config.
5. **Before #113 relies on it:** measure real failure rates with `make live` and record them here.

No ADR needed: this confirms TD §12.2–12.5 as written. Problems 1, 2 and 6–8 refine §12.3 and should be raised on the E-codegen stories.
