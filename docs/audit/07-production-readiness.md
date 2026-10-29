# Production readiness: what matters most, and in what order

Written 2026-10-29, after filing all findings (F-01..F-38, N-01..N-08, #427-#461, #464). This is a judgement
call on sequencing for the owner, not a new finding. Numbers refer to `01-findings.md`.

## 1. Before M3/M4 (Build, Test, Certify) carries real weight

The audit's biggest structural risk is that the stage that is supposed to be the trust boundary — gates, tests,
sandbox, certify, freeze — is exactly the stage that is stubbed (F-01: every activity but `build_manifest` is a
fail-open stub with no production guard) and exactly the stage whose real implementation is a denylist, not an
allowlist (F-02: 6 Python bypasses; F-36: the unmerged AST gate stack has 4 more). None of this is urgent *today*
because nothing is certified yet, but it is the one place where "ship the feature, hub the gate later" is the
wrong order: once FDEs start relying on a green certify to mean something, every schema and code-safety gate needs
to already be an allowlist, not a name-blocklist. **Recommendation: treat F-01, F-02 and F-36 as a blocking
dependency of whichever issue first turns on real certification, not as independent backlog items.** The
schema-SQL gate (F-34 and its addendum) is the one gate that already exists end-to-end and is close: it needs the
same allowlist treatment (restrict function bodies to a known-safe shape) before it is relied on, not after.

## 2. Deploy blocker, fix now regardless of milestone

**F-25**: the shipped Caddyfile is invalid for the pinned Caddy version, so the only public-facing service would
not start, and the repo's own test that would have caught this is skipped in CI. This blocks every environment,
not just production. It is small and already has P0 on it; there is no reason to sequence it behind anything else.

## 3. Data lifecycle: fix before any real customer data is loaded

F-26 (purge leaves parsed derivatives and Cognee data behind) and F-27 (a knowledge rebuild keeps removed
documents in the graph, confirmed live) are both "deleted data doesn't go away" bugs. For a product whose pitch is
ingesting a client's documents, these are reputational, not just technical: once a customer document is removed
in the UI, it must actually leave the vector/graph store. `delete_dataset` already works (confirmed live), so the
fix is wiring, not a new primitive. Recommend both land before the first real (non-synthetic) project.

## 4. Hygiene that is cheap now and expensive later

- **F-09 / #435**: replace `${VAR:-default}` with `${VAR:?required}` for every secret before any shared or staging
  deploy; the audit found the default Cognee secret in active use would let anyone who read `compose.yaml` derive
  every project's Cognee password.
- **#437 (F-10)**: wire a dependency/image CVE scan into the gate that blocks merges, not just reports — ties
  directly into the CI `image-scan` failure seen on PRs #465/#466 today (a CRITICAL in a vendored Rust crate inside
  `temporalio`); right now nothing stops that from staying red indefinitely other than a human noticing.
- **#440 (F-06, Postgres RLS)**: no cross-tenant leak was found by the audit, but RLS is cheap defense-in-depth for
  a multi-tenant product where a single missed `WHERE tenant_id = ...` is catastrophic. Do it before the tenant
  count grows past what a code review can keep track of.
- **F-35 / #458**: session cookie usable ~5 minutes after sign-out. Small fix, meaningfully reduces the blast
  radius of a stolen laptop/cookie.

## 5. Decisions the audit surfaced but cannot make

These are product questions, not bugs; `06-flows-and-design-review.md` has the full list. The two worth the
owner's attention first:
- **D-18**: one FDE can both author and approve a SOW. Fine for MVP; ask customers whether a second approver is a
  contractual requirement before it is expensive to retrofit (approval is already a state-machine transition, so
  the change is additive, not a rewrite).
- **F-23 / #446**: the correction loop can change the approved ISATVON specification and then certify the changed
  version without the FDE re-reviewing it. Decide whether "FDE approved X" must still be true after a correction
  changes X, or whether re-certification implicitly re-approves.

## 6. What does not need more audit time right now

- Endpoint coverage (`05-api-endpoint-inventory.md`): all 79 routes are now reviewed (41 `read`, 38 `live`); none
  are dead code or speculative surface to prune. The remaining gap is depth (`read` -> `live`), not scope.
- CubeSandbox and real-model LLM behaviour: correctly deferred per the owner until the above lands; auditing a
  sandbox that doesn't exist yet would be speculative.
