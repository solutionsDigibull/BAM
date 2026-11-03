# 0026 — Generated code is untrusted: static checks are a fast fail, the sandbox is the boundary

- Status: Proposed
- Date: 2026-11-03
- Related: issue #428 (Audit A-02), #625, #120, #123, refines [0012](0012-testing-sandbox-and-certification.md) and [0023](0023-sandbox-adapter.md), [TD §12.5](../design/12-code-generation.md)

## Context

Documents reach the code generator through prompts, so prompt injection is a realistic way to get adversarial
Python and SQL into a Sub-Skill. The audit (F-02) showed that the checks in front of the sandbox were denylists:
six Python escapes (a string-built `getattr`, `open()`, a class walk that never names `__subclasses__`,
`globals()`, an unbounded loop, a memory bomb) and three `state_queries` (`SELECT ... FROM sbam.users`,
`pg_read_file`, `pg_sleep`) returned no findings. A denylist loses to indirection, and a restricted `exec` inside
the interpreter is known to be bypassable. A bundle that passes its own tests can be certified and frozen (E14).

## Decision

1. **Treat every generated body as hostile.** The model's output is data until the sandbox has run it. Nothing the
   model writes sets a platform field, an import, a status or an id (TD §12.2); that rule is unchanged.
2. **Static checks are a fast fail, never the authority.** They save a sandbox run and give the corrector precise
   findings; they do not make code safe. Three layers, each of which may be bypassed without breaking the next:
   - the **bodies pre-check** (`codegen/bodies.py`) now works from an allowlist (`codegen/allowlist.py`): permitted
     node types and builtins, no underscore or interpreter-internals attributes, a size cap on literal repeats and
     `range`; `state_queries` are parsed with sqlglot (`codegen/state_sql.py`) and must be a single plain SELECT
     over unqualified, non-system tables with allowlisted functions;
   - the **AST gate** on the assembled files (E12.2, `build/gates/ast_rules.py`) and the import, static-analysis
     and SQL-safety gates (E12.4, E12.5);
   - the **review of findings** by the corrector, bounded at two attempts (E11.4).
3. **The sandbox is the boundary.** A certifying run happens in a CubeSandbox microVM (a kernel boundary) with the
   egress policy of ADR 0023, cgroup limits, a read-only root and a wall-clock kill. A deterministic tool needs
   no network except the declared mock API. What the code *does* is contained there, not by Python-level
   restrictions.
4. **`local-docker` never certifies** (golden rule, ADR 0023). The hardened local container (read-only root, all
   capabilities dropped, `no-new-privileges`, memory/CPU/pids limits) exists for development; its runs are
   `NON_CERTIFYING` and the certification checklist rejects them (`NON_CERTIFYING_RUN`).
5. **Fresh-state SQL runs under a database role that can only read the workflow's schema**, with a statement
   timeout. The sqlglot check above is a fast fail for that; the role is the control.

## What this ADR does not claim

- **Escape behaviour is not tested by the pre-check.** Reading `/etc/passwd`, opening a socket, a fork bomb, a 10 GB
  allocation and a `sleep` past the deadline are contained by the sandbox. They need a live sandbox to be
  shown, which needs the CubeSandbox host (#122); until then the evidence is the local container's hardening only.
- **The runtime audit hook (#120)** for imports and file or network access is not built here.
- **A bundle can still forge its own verdict today.** The in-VM runner imports the bundle into the same process as
  the harness (#625). That is a separate fix (child process under another uid, outputs written by the parent) and
  an open design question for the owner of E13.
- **The fresh-state role** (decision 5) is the stated requirement; whether the sandbox runner already connects as
  such a role has not been verified in this change.

## Consequences

- A body that used to pass the pre-check can now be refused (a class, `print`, `getattr`, `iter`, a `pg_*` call).
  The corrector sees stable codes (`FORBIDDEN_CALL`, `DISALLOWED_SYNTAX`, `PRIVATE_ATTRIBUTE`, `RESOURCE_BOMB`,
  `STATE_QUERY_INVALID`). The `sbam-codegen` prompt does not yet state the allowlist, so a real model may spend
  corrections until a prompt version does (a golden-set run, with its cost, is the check).
- The allowlists are code, not configuration: widening one is a reviewed change with a test, and the recorded
  legitimate answers (MS01.1, stock-reserve, edge-enum) must keep passing.
- Any future static check is judged by the same question: does it shorten the loop, or is it being asked to be
  the boundary?
