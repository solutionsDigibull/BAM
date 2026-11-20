# 0028 — Workflow code generation: repo-first code, one MCP server per workflow, Bifrost as the registry, named agent roles, a token budget, a demo before Commit

- Status: Accepted
- Date: 2026-11-16
- Related: epic #748, issue #751 (WG-3), spike #750 ([report](../spikes/bifrost-mcp.md)), refines [0010](0010-isatvon-and-skill-manifest.md) (O-19), [0011](0011-code-generation-and-correction.md), [0012](0012-testing-sandbox-and-certification.md), [0013](0013-versioning-immutability-and-reuse.md), [0015](0015-llm-gateway.md), [0023](0023-sandbox-adapter.md), [TD §11](../design/11-manifest-design.md), [TD §12](../design/12-code-generation.md)

## Context

SBAM generates one FastMCP server per Sub-Skill from a Jinja2 skeleton and model-written bodies, certifies it,
and ends at an export package. The product goal is wider: SBAM acts as the developer and SRIM as the user
acceptance side. SBAM must build a whole workflow, test it, show it working, and hand SRIM the exact tested
build, so that the client sees a verified product.

That needs decisions the earlier ADRs did not make or made differently: where code comes from (open-source
repositories before generation), how many servers a workflow has, where certified servers are registered
(the manifest named MCP Jungle; Bifrost is the gateway the team runs), who does what in the generation,
what the model may spend, how a human sees the product before it goes live, and how API keys are handled.
The spike [#750](../spikes/bifrost-mcp.md) showed that Bifrost can be that registry over HTTP, with limits listed
there. [PLAN §3](../plan/02-confirmed-decisions.md) ids (C-xx) stay owned by their ADRs; nothing below changes
a confirmed decision. Where this ADR changes a *default* of an earlier ADR it says so.

## Decision

1. **Where code comes from, in order, per Sub-Skill.** (1) A repository named in the Sub-Skill's ISATVON
   (an optional `source_repos` list in the T block); (2) a matching repository from the Admin masters (the
   Agents `repo` column and the Tooling `links_path`); (3) a question to the FDE in the workbench when several
   candidates are close or the match is weak, or when there is none; (4) generate everything with the hybrid
   generator of [0011](0011-code-generation-and-correction.md). A repository is never enough alone: the
   generator fills the gaps, and every build still passes every gate.
2. **Repositories are untrusted input.** They come only from the ISATVON, the Admin masters or the FDE, never
   from a search. A clone is pinned to a commit and made in an isolated fetcher, with no scripts run. The
   upstream tree is kept untouched, with its LICENSE and NOTICE, and every change is a separate patch file
   with a reason. The licence of the repository and its dependencies is checked, and so are known
   vulnerabilities and malware. The repository commit and the patch hashes join the certification reuse key
   of [0013](0013-versioning-immutability-and-reuse.md) (C-33), so any change to a repository or a patch gives a
   new PATCH version.
3. **One MCP server per workflow.** Every Sub-Skill keeps its Action tool and its independent Fixed Check
   tool (C-50 is unchanged), namespaced `MS01_1__action` and `MS01_1__check` (no dot: model clients restrict tool names to letters,
   digits, underscore and hyphen, and Bifrost adds `<server>-` in front), and all of them are registered in
   one FastMCP server, built into one image pinned by digest. The order of the steps stays in the manifest DAG
   (`next` and data edges), not in the server. The server calls no model at run time. The Workflow manifest
   records the server name, version, tool list and image digest.
4. **Bifrost is the registry and the MCP gateway for SRIM, over HTTP.** A certified, frozen server is
   registered there; nothing uncertified is. Names use underscores (Bifrost forbids hyphens), a new version is a
   new entry and the old one is **disabled, never deleted** (no revocation, C-31, is unchanged). Each consumer
   has its own virtual key granting only the entries and tools it may use, and an FDE preview is a separate
   entry that SRIM's key cannot see. SBAM keeps its own table of registered workflows as the source of truth and
   reconciles it with Bifrost. This changes the default of **O-19** (the manifest's `srim_runtime_definition`
   gateway) from MCP Jungle to Bifrost; new manifests carry the Bifrost value, and manifests already frozen
   keep theirs.
5. **Named roles, not a free-form team.** The master is the existing Temporal workflow (no model). The repo
   scout compares close candidates (a cheap model, only when needed). The coder fills the gaps and writes patch
   files. The reviewer is **required**: one model pass over the diff against the spec, run only after every
   gate passed, and by a different model from the coder's. The corrector (two attempts) and the test writer are
   unchanged. All are Temporal activities with their own Bifrost aliases. No second orchestrator is added.
6. **A token budget.** Caps per Sub-Skill build, per workflow build and per knowledge build. The count is a
   query over `llm_calls`. The model wrapper refuses a call that would go over; alerts go out at 50% and 80%;
   at 100% the build stops cleanly in `NEEDS_FDE_REVIEW` with the reason "token budget reached", and the FDE or
   an Admin may raise the cap once per build. A provider refusal for credit reasons is a clear, non-retryable
   error, not a silent retry. Admin can change the caps per project. Cognee's calls get a price entry so their
   cost is recorded. Cognee's calls bypass the wrapper, so its meter callbacks end a running `cognify`
   at once (#777): on Bifrost's `502 upstream_credentials_exhausted`, or when the knowledge cap is reached,
   the callback sets a stop flag, the adapter cancels the call and the build fails with the non-retryable code
   `ModelCreditExhausted` or `KnowledgeTokenCap`, which the Context step shows in plain words.
7. **A demo before Commit.** After certification and the automated checks, the FDE sees the whole product, UI
   included, in a time-limited **preview** microVM that only the assigned FDE can reach, with real data and the
   real services the code declares. Commit is the FDE's final approval: it freezes the workflow, registers it
   and **promotes the same image digest that was demoed** (no rebuild). A stale hash is refused with 409, as
   with the SOW approval. Certification stays automatic and all-or-nothing; Commit is an additional human gate,
   not a replacement. Hosting the certified server for SRIM remains outside SBAM; SBAM registers a launch
   recipe (the image digest and how to start it) and SRIM or operations start the container.
8. **The FDE edits only secrets.** The generated code is read-only for the FDE, who sees all of it with
   reused, customized and generated tags. A change is requested with a **Request change** note, which is passed
   to the coder as data and never as instructions, produces a new generated version, and is limited per workflow
   version. Keys and settings are entered in a Connections panel, stored encrypted per project, write-only,
   injected only when a container starts, never in code, manifest, logs or export, and not part of the artifact
   hash (changing one needs no recertification). SRIM gets the same values unless the FDE replaces them at
   Commit. Commit is blocked until every required value is set and its connection test passed.
9. **Certification tests keep their mocks; only the demo uses real services.** A WSL2 or other development host
   is never a certifying host.

### Defaults, pending owner answer

| Item | Default | Where it is used |
|---|---|---|
| Token caps | 200,000 per Sub-Skill build, 2,000,000 per workflow build, 30,000,000 per knowledge build; alerts at 50% and 80% | decision 6 |
| Licence allowlist | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC; copyleft, unknown and custom licences refused unless a person overrides with a recorded reason | decision 2 |
| Preview time limit | 60 minutes, then the VM and its data are destroyed | decision 7 |
| Request-change limit | 3 notes per workflow version, then the ISATVON Grid | decision 8 |
| Who starts the certified server | SBAM registers a launch recipe; SRIM or operations start it | decisions 4 and 7 |
| Model tiers | the current aliases first; new aliases for the repo scout and the reviewer, on cheaper models | decision 5 |

A different answer from the owner becomes a new ADR that supersedes this one for that item.

## Consequences

- **Contracts change first, in their own PR** (WG-4, #752): `source_repos` in T, a workflow server entry in the
  Workflow manifest, the Bifrost gateway value, a repo decision record and a budget contract. Existing
  manifests and the ISA14 sample must keep validating, and frozen hashes must not change.
- **What this overrides in earlier ADRs.** [0010](0010-isatvon-and-skill-manifest.md): the O-19 default names
  MCP Jungle; new manifests use Bifrost (decision 4). [0011](0011-code-generation-and-correction.md): servers are
  now per workflow, not per Sub-Skill, and the tools per Sub-Skill are unchanged (decision 3).
  [0023](0023-sandbox-adapter.md): sandbox VMs also run as a long-lived, FDE-only preview (decision 7), which is
  new scope, because SBAM until now ended at certified, frozen and exported. Earlier ADRs are immutable and
  keep their text; this ADR is the reference for these points, and the index lists the refinement. A formal
  supersession of 0010 would have to carry all ten of its plan rows for one changed default, so it is not done
  here; the owner can ask for it.
- **New security surface.** The repository fetcher, the per-project secret store and the preview route are new
  attack surface. Each is its own issue (WG-7, WG-16, WG-17) with its own threat model, and each reuses the
  rule of [0026](0026-generated-code-threat-model.md): the sandbox is the boundary, and checks are a fast fail.
- **Production needs more than the spike showed.** Bifrost's data directory must be a volume, dashboard
  authentication must be on, TLS and a network policy must limit who reaches the MCP endpoint, and a health
  check and a reconcile job must run. See the [spike report](../spikes/bifrost-mcp.md).
- **Not decided here.** What SRIM needs to look up in Bifrost (the lookup contract, to be agreed with the SRIM
  owners); a chunks-only knowledge mode that skips the graph step to save cost (a separate design); the workflow
  UI template (to be supplied by the owner before WG-20); the OpenRouter credit limit seen on 15 November, which
  is an account matter, not an architecture one.
- **Issues:** WG-4 contracts (#752), WG-5 budget (#753), WG-6 repository catalog (#754), WG-7 fetcher (#755),
  WG-8 choose-a-repo step (#756), WG-9 patch overlay (#757), WG-10 aggregate server (#758), WG-11 reviewer and
  scout (#759), WG-12 registration (#760), WG-13 gates (#761), WG-14 host acceptance (#762), WG-15 code
  preview (#763), WG-16 secrets (#764), WG-17 preview environment (#765), WG-18 Commit (#766), WG-19 change
  note (#767), WG-20 UI (#768).

## Implementation notes

- **Choose-a-repo step (#756).** The `choose_repo` activity of `SubSkillBuild` (behind `workflow.patched("wg8-choose-repo")`,
  once per build) applies decision 1 step 3. A repository named in the ISATVON is used without scoring. Otherwise the
  catalogue is scored by the repo scout and the bands of its prompt apply: a clear winner scores at least 0.8 and leads
  the runner-up by 0.15; a best score below 0.8 is a weak match, a closer runner-up is a tie, and an empty catalogue is
  "no candidate". A winner is recorded as an AUTO `RepoDecision`. A question pauses the build in the stage
  `wait_repo_choice` until the `repo_answer` signal, with no timeout and a reminder notification every 3 days. The
  question is the audit row `repo_question_asked` and the decision (who, when, the candidates) is the audit row
  `repo_decided`, so no table was added. The FDE answers in the workbench, the API checks the choice against the stored
  short list (an own repository is allowed only when the catalogue had none, and only after the catalogue's URL rules),
  and "no repository" is a GENERATED decision. Fetching and using the chosen repository is the next steps (#755, #862).
- **Vendored trees and the server image (#867).** `build_workflow_server` copies each attached repository's stored
  tree untouched into the server bundle under `vendor/<repo>@<commit>/` (the tree is found through the scan report named
  in `build.repos` and must match the recorded tree digest), so the Dockerfile's `COPY vendor/` layer exists. The
  activity `build_workflow_image` (behind `workflow.patched("wg29-workflow-image-build")`, after the server files, no
  new pipeline stage) builds the image from the stored tar through an `ImageBuilder` port and writes the audit row
  `workflow_image_built`: the image digest, the bundle hash it came from, the `mcp_server` entry and the content hash of
  the draft sealed with it. Commit (#766) merges that entry with `workflow_image.manifest_with_image`; the draft row and
  frozen manifests are never rewritten. The manifest is not placed inside the server bundle, because it records the
  digest the bundle decides (circular). The worker holds no docker socket, so the builder is opt-in: set
  `IMAGE_BUILDER=docker` on a build host whose `DOCKER_HOST` is reachable (a developer machine or the Staging
  deployer's host); the default `none` records `workflow_image_failed` with `BUILDER_DISABLED` and the pipeline goes on.
  The recorded digest is the local image id; pushing to a registry for the deployer (#765) is a later step.

## Sources

- The design document of revision 2 (15 November 2026), issues #748 to #768, and the owner's confirmations in
  conversation on 15 and 16 November 2026 (D1 and D3 agreed to these changes).
- [Bifrost MCP spike](../spikes/bifrost-mcp.md), [ADR 0013](0013-versioning-immutability-and-reuse.md) (C-31 to C-33),
  [ADR 0011](0011-code-generation-and-correction.md) (C-22 to C-24, C-50), [ADR 0023](0023-sandbox-adapter.md).
