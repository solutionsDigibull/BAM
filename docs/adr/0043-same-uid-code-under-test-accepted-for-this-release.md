# 0043 — The code under test keeps the runner's uid for this release; the second uid waits for the VM template

- Status: Accepted
- Date: 2026-11-20
- Related: issue #625, follow-up #967, refines [0027](0027-code-under-test-runs-in-its-own-process.md), related [0026](0026-generated-code-threat-model.md), [0040](0040-imports-are-a-suggestion-not-a-restriction.md), #122

## Context

Issue #625 proposed three parts: the code under test in a child process under a different uid, the runner writing
`outputs.json` only after that process group is dead, and a root-owned read-only harness. PR #664 and #671 shipped
the second and third parts and the child process (ADR 0027). The uid split was left open: the container runs as 10001
with `cap_drop ALL`, so a uid switch needs a root parent or `CAP_SETUID`, which the local image does not have and
which belongs in the VM template (#122, no KVM host yet). The issue also left one limit unverified: `os._exit()`.

## Decision

1. **Accept ADR 0027's same-uid mitigation for this release.** Non-dumpable runner, child subreaper, kill-and-reap of
   everything below the runner before `outputs.json` is written, `out/` recreated, root-owned `/opt/sbam`, and an
   outputs file from a runner that did not exit cleanly is not evidence. No capability is added to the image.
2. **The uid split and a restricted database role for the child are follow-up #967**, blocked by #122.
3. **`os._exit()` is verified, not assumed.** With the code under test in its own process, `os._exit()` ends only the
   child: the runner survives, writes its own file, and the case is a `RUNTIME_ERROR` (never infrastructure, never a
   pass). `test_e13_2_625_a_tool_that_hard_exits_after_forging_cannot_yield_a_pass` forges a file and then calls
   `os._exit(0)` before the runner writes. For generated code the forbidden-module deny list (ADR 0040) already
   refuses `os`; the test bypasses that gate on purpose, because the sandbox is the boundary.
4. **What "forgery" means.** The verdict is computed by the harness from real state and the check tool. A bundle that
   writes an honest handoff row can pass; that is a correct bundle. The tests therefore assert that the runner's own
   file is the one that survives and that the verdict matches the runner's trace, not that an attacking bundle fails.

## Consequences

- #625 stays open until #967 lands or the owner decides the residual is final.
- Residual risk, unchanged from ADR 0027: the code under test can read `request.json` and use the case database as the
  same database user. Synthetic data only.
