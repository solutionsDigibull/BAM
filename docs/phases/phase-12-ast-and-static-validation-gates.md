> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 12 — AST and Static Validation Gates

| Epic | Milestone | Stories |
|---|---|---|
| [#13](https://github.com/solutionsDigibull/BAM/issues/13) `E12` | M3 Breakdown + Build | 6 |


**Goal:** Prove generated code is syntactically valid, conforms to its manifest and passes strict quality and security checks before testing.

**Owner:** Dev C · **Indicative effort:** 1.5 weeks · **Complexity:** M · **Depends on:** Phase 11
**Design references:** [TD §12.5–12.6](../design/12-code-generation.md).

### AST validation against the manifest

- [ ] `ast.parse()` for every file (syntax only).
- [ ] Skeleton integrity: AST of the non-slot code equals the rendered skeleton (the LLM cannot alter protected regions).
- [ ] Manifest conformance: action and check tool names, parameters (names, annotations) vs `contracts.input_schema`, outputs vs `contracts.output_schema`, and the check tool returns every identifier used by the gates.
- [ ] Decoupling: the check tool neither imports nor calls the action tool.
- [ ] No manual loops: no `while`/retry loops in tool bodies (iteration is declarative).
- [ ] Handoff: the action path appends a handoff record with all `required_fields`; no UPDATE/DELETE on the handoff table; SQL parameterised only.
- [ ] Import policy: imports ⊆ template allowlist ∪ manifest-resolved libraries; forbidden calls (`eval`, `exec`, `os.system`, `subprocess` unless declared, `pickle`, dynamic `__import__`).

### Static and security checks (toolchain image)

- [ ] Ruff (configured rule set), Black `--check`, MyPy `--strict`.
- [ ] ClamAV scan of the generated bundle; Bandit SAST *(proposed, O-03)*.
- [ ] Import resolution inside the `skill-runtime` image (import every module without running tool logic).
- [ ] Results stored per check with tool versions and config hashes.

### Frontend

- [ ] Build & Test dashboard: gate pipeline per Sub-Skill with pass/fail, findings and attempts.

### Tests

- [ ] One negative fixture per check (syntax error, altered skeleton, wrong parameter type, check imports action, `while` retry loop, missing handoff append, UPDATE on handoff table, f-string SQL, forbidden import, MyPy error, Ruff error, unresolved import).

### Exit Criteria

- [ ] No code reaches the sandbox unless AST conformance and all static/security checks pass on the same code hash.
- [ ] Every result records tool version and configuration, so it can be reproduced.

## Design pages to read

- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §12. Code generation (hybrid Jinja2 + LLM)](../design/12-code-generation.md)
- [TD §20. Docker and environments](../design/20-docker-and-environments.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#67](https://github.com/solutionsDigibull/BAM/issues/67) | `E12.1` | skill-runtime image + runtime registry generation | A Platform | M | P0 | M2 | [#38](https://github.com/solutionsDigibull/BAM/issues/38) |
| [#117](https://github.com/solutionsDigibull/BAM/issues/117) | `E12.2` | AST gate: syntax, skeleton integrity, manifest conformance | C Build | M | P0 | M3 | [#114](https://github.com/solutionsDigibull/BAM/issues/114) |
| [#118](https://github.com/solutionsDigibull/BAM/issues/118) | `E12.3` | AST gate: decoupling, no manual loops, handoff, imports, SQL safety | C Build | M | P0 | M3 | [#117](https://github.com/solutionsDigibull/BAM/issues/117) |
| [#119](https://github.com/solutionsDigibull/BAM/issues/119) | `E12.4` | Static gate in toolchain image: Ruff, Black, MyPy strict, Bandit, ClamAV | C Build | M | P0 | M3 | [#114](https://github.com/solutionsDigibull/BAM/issues/114), [#38](https://github.com/solutionsDigibull/BAM/issues/38) |
| [#120](https://github.com/solutionsDigibull/BAM/issues/120) | `E12.5` | Import resolution in skill-runtime | C Build | S | P0 | M3 | [#67](https://github.com/solutionsDigibull/BAM/issues/67), [#114](https://github.com/solutionsDigibull/BAM/issues/114) |
| [#121](https://github.com/solutionsDigibull/BAM/issues/121) | `E12.6` | Build & Test dashboard | D Frontend | M | P0 | M3 | [#107](https://github.com/solutionsDigibull/BAM/issues/107), [#45](https://github.com/solutionsDigibull/BAM/issues/45) |

---
[← Phase 11](phase-11-hybrid-code-generation-and-correction.md)   [Phases index](README.md)   [Phase 13 →](phase-13-test-data-promptfoo-and-cubesandbox-execution.md)
