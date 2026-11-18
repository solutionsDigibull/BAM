# 0032 — Staging replaces the pre-commit preview: FDE mode builds, Staging mode runs the complete workflow

- Status: Accepted
- Date: 2026-11-18
- Related: epic #748, issue #859, issues #765 (WG-17), #766 (WG-18), #767 (WG-19), #768 (WG-20), #857 (WG-25), refines [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) (decision 7, and the preview parts of decisions 4 and 9), [0023](0023-sandbox-adapter.md), [0030](0030-project-connections-and-secrets.md), [0031](0031-srim-walks-the-workflow-dag-with-temporal.md)

## Context

[0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) decision 7 has the FDE see the whole product, UI
included, in a time-limited **preview** microVM *before* Commit, and Commit promotes the digest that was demoed.

The owner decided otherwise on 2026-11-18. The workbench already has an **FDE / Staging** toggle in one login
(FDE blue, Staging orange). FDE mode is for building. Staging mode is where the finished workflow is shown and
run. This ADR states the flow that follows from that, and corrects the places in 0028 that said otherwise.
Nothing here changes a confirmed decision (C-xx) or an open item default (O-xx); no plan rows are copied.

## Decision

1. **FDE mode builds and checks. It does not run the workflow UI.** It generates the code, builds the workflow
   server container and the UI bundle, runs the automated checks and certification in the sandbox, and shows the
   code (with its reused, customized and generated tags), the gate results and the certification evidence,
   read-only. The workflow's own UI is not shown and the workflow is not run in FDE mode. There is no preview
   microVM before Commit.
2. **Commit is the FDE's final approval, and it moves the workflow to Staging.** It freezes the workflow,
   registers it as a **Staging entry** in the registry (reachable only by the assigned FDE; SRIM's production
   key cannot see it), and starts its container in Staging. The image is the one that was built and certified:
   no rebuild. A stale hash is refused with 409, as with the SOW approval. Commit is blocked until every
   required secret is set and its connection test passed ([0030](0030-project-connections-and-secrets.md)).
   Certification stays automatic and all-or-nothing; Commit is an additional human gate, not a replacement.
3. **Staging mode shows the complete generated UI and runs the workflow, in the same FDE login.** The toggle
   is the only way in: one sign-in, one session, no second login. After a successful Commit the workbench
   switches to Staging by itself. In Staging the FDE sees the full UI bundle that BAM generated and can run the
   workflow end to end: the Temporal orchestrator of [0031](0031-srim-walks-the-workflow-dag-with-temporal.md) walks the
   manifest DAG, the deployer starts the containers by digest, tool calls go through Bifrost, and data is kept
   in a database per project. Real services are allowed within the egress allowlist the code declares (the
   guardrails planned for the old preview carry over). Only the assigned FDE can reach it; other tenants and
   projects get 404. Staging stays orange.
4. **Staging lives until the next version.** One Staging deployment per project and version, kept running, with
   no time limit. A newer committed version replaces it; the older entry is marked superseded and never
   deleted (no revocation, C-31).
5. **A problem found in Staging goes through Request change.** The FDE writes a note ([0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md)
   decision 8, #767); it produces a new generated version, which is certified and committed again and replaces
   Staging. The FDE still edits only secrets.
6. **Promotion to SRIM production is a separate, later step and is not decided here.** Until it exists,
   nothing committed is visible to clients. What the step is called, who may do it, and whether it needs its own
   approval are open.

## What this overrides in 0028 and 0023

- 0028 decision 7: the demo before Commit, the time-limited preview, and "promotes the same image digest that was
  demoed". Now Commit promotes the digest that was built and certified to Staging. The **Preview time limit**
  default (60 minutes) in its defaults table is dropped.
- 0028 decision 4: "an FDE preview is a separate entry that SRIM's key cannot see". The Staging entry now plays
  that role. Registry entries get a kind (staging, later production).
- 0028 decision 9: "only the demo uses real services". Now Staging does.
- [0023](0023-sandbox-adapter.md): the long-lived, FDE-only preview VM becomes the Staging runtime. Where its
  containers run (a sandbox VM or the deployer's Docker host) is open.

## Open points

- Where Staging containers run, and how the UI bundle is served inside the FDE shell.
- Whether Staging data is fresh for each version or carried over, and how it is reset.
- The registry change for entry kinds and for promotion (follow-up issue).
- The promotion step to SRIM production (decision 6).

## Consequences

- **Issues.** #765 is rescoped from "live preview microVM before Commit" to the Staging runtime (start the
  container, serve the UI bundle, FDE-only route, guardrails; no time limit). #766 changes: its Validation
  screen has no live preview, and Commit freezes, registers a Staging entry, starts Staging and flips the toggle.
  #768's bundle is shown in Staging. #760 (merged) needs a follow-up for registry entry kinds.
- **Workbench.** `deployToStaging()` is wired to the backend, and the toggle switches to Staging after a
  successful Commit. The colours do not change.
- **Contracts** are not changed by this ADR. The UI bundle entry is #857.
