# SBAM security and design audit: index

Audited: `origin/dev` @ `d8b0dce`, 2026-10-28. Branch: `audit/full-security-review` (docs only; nothing pushed, no
GitHub issues filed). Read in this order.

| File | What it is | Read it if you want |
|---|---|---|
| `01-findings.md` | **Final, merged findings** (F-01..F-37, N-01..N-08), evidence level, priority, fix | the verdict on what is wrong |
| `02-upgrade-plan.md` | Current design vs proposed change, what it does, why, size, wave order | the roadmap to review and decide on |
| `03-issue-drafts.md` | 21 issue drafts with comparison, acceptance criteria, owners (not filed) | to approve issues for filing |
| `04-second-pass.md` | History: what the second pass corrected, live pentest results, severity changes | how confident to be in each finding |
| `05-api-endpoint-inventory.md` | All 79 routes with policy, write controls, review depth | endpoint coverage and gaps |
| `06-flows-and-design-review.md` | Components, execution flow, 19 design choices questioned, open questions | design-level review |
| `poc/` | Scripts and raw scanner output (`poc/README.md` explains the harness) | to reproduce anything |

## Headline results
- **Deploy blocker found in round 3:** the shipped Caddyfile is invalid for the pinned Caddy, so the only public service would not start (F-25); the repo's own validation test fails but is skipped in CI.
- **Round 3 also found:** purge and knowledge rebuild leave deleted documents' data behind (F-26, F-27), a 532 KB PNG needs 2.3 GB (F-28), archived projects stay editable (F-29), the mock-LLM guard is a deny-list (F-30).
- **Release blocker:** certification, gates, tests and freeze are stubs that always succeed, with no production guard (F-01).
- **Reachable today:** a crafted PDF costs the shared ingest worker 10 s and 523 MB (F-24); parallel calls after login
  return 500 for ~37% of requests in the repro (N-01).
- **Before the sandbox ships:** the codegen pre-check is a denylist (F-02) and the correction loop can change the
  approved specification then certify it (F-23).
- **No cross-tenant leak was found** (static sweep plus ~90 live checks); tenancy hardening (RLS, role split) is
  defence in depth.

## What this audit does not cover
Live-tested in round 3: Keycloak, tusd, Caddy, the Next.js server (with real Chromium) and the ISATVON/builds/workflows/notifications APIs. Round 4 added Temporal failure injection (no bug found) and F-27 reproduction. Still not tested: a hung activity or killed worker on a real Temporal server, Cognee live, CubeSandbox (not built), LLM prompt injection (needs a real model and a built codegen path). Issues filed: #427 to #461 on GitHub. Not reviewed
in depth: knowledge/Cognee, ISATVON edit APIs, builds router, notifications, most client pages, 33 local-only
branches. 25 of 79 endpoints are inventoried only (`05`). Findings tagged `Unverified` need confirmation.

## Corrections made along the way (so earlier readers are not misled)
First pass claims that were wrong or overstated and are fixed here: F-07 (audit trigger exists), F-03 (no phantom
Workflow), F-04 (bounded to 300 s), F-12 (pool did not fail), F-02 SQL impact (unknown), and "PDF parsing is not
implemented" (it is, and is vulnerable). `04-second-pass.md` lists each.
