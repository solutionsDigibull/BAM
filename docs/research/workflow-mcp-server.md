# Research: one certified MCP server per Workflow

Status: **draft for approval**. Nothing here is implemented. Date: 2026-11-12.

This document continues SBAM from where the pipeline stands today (schema generation) through code
generation, integration, MCP server, certification, registry and SRIM handover. It answers the twelve
deliverables of the master prompt. Decisions already given by the product owner are treated as fixed:

- It is a continuation of SBAM, not a new system.
- The Skill Manifest stays the source of truth. It gains fields such as the open-source repository link.
- A Skill (Macro Skill) is a **stage** of the workflow. Its Sub-Skills are the **steps** inside it. The
  breakdown already exists in the manifests; this work does not redo it.
- **One Workflow = one MCP server** with many tools, because one deployment per workflow costs less.
- Nothing exists on the SRIM side yet, and no MCP server is registered anywhere.

How to read the evidence: statements about SBAM cite the file they come from and were read from the code
at commit `e13545a`; they were not exercised by running a build. Statements about outside technology cite a
URL. §13 lists what could not be verified.

---

## 1. Summary of the recommendation

1. **Keep the Sub-Skill as the unit that is generated, tested and certified.** That path exists and is the
   strongest part of SBAM. Add a new **assembly** stage that composes the certified Sub-Skills of a Workflow
   into one MCP server.
2. **Put a small platform-owned runner inside the server.** It runs the steps of a stage in `next` order,
   runs each step's action/check loop from `loop_spec.json`, and passes data from stage to stage. It is the
   same code the sandbox uses to test, so what is certified is what runs.
3. **Do not embed a workflow engine (Temporal, LangGraph) in the artifact.** ISATVON already forces one linear
   chain per stage, and every pass already appends a row to PostgreSQL. A run table plus those rows gives
   resumable execution with no extra service. Keep a clean seam so a durable engine can drive the same steps later.
4. **Expose `run_start` / `run_status` / `run_result` / `run_cancel` / `run_resume` tools** with a run id as
   an explicit argument. This matches the current stateless MCP specification and does not depend on the
   optional Tasks extension, which no listed client supports yet.
5. **Reuse open source in three modes, strictest first:** as a pinned library, as vendored files at a pinned
   commit, and only last as adapted code. Each mode has a licence gate and a provenance record.
6. **Certify the assembled server, not only its parts.** Replace the four stubbed workflow-level activities
   with a real end-to-end run in CubeSandbox, MCP checks, and security scans. Sign the result.
7. **The registry of certified servers lives in SBAM**, shaped like the official MCP Registry API, with the
   bytes in the container registry. **Bifrost is the connection gateway, not the registry.**
8. **Fix two gaps in today's build first** (§3.3): they block any real certification.

---

## 2. Deliverable 1: assessment of the proposed approach

| Stage in the master prompt | Assessment |
|---|---|
| Workflow and schema analysis, breakdown into components | **Not needed as a step.** The manifests already hold the breakdown (Agents and Sub-Skills, ISATVON 1.4). Extra context comes from Cognee and the uploaded documents. |
| Skill Manifest as source of truth for repositories | **Right, but the field does not exist yet.** No Sub-Skill or manifest field holds a repository URL (`server/contracts/sbam_contracts/isatvon14.py`, `manifest.py`). The Agents master has `repo` and `mcp_server` columns that nothing reads (`sbam_contracts/masters.py`). |
| Reuse and customise open-source code | **Highest-risk stage.** Today generated code is treated as hostile (ADR 0026): imports are limited to the runtime image's libraries and LLM-written code is confined to template slots. Arbitrary repository code breaks that model unless it enters through controlled modes (§6). |
| Generate missing code with designated tools | **Exists for the slot-filling case** (`sbam-codegen`, `sbam-corrector`, AST and static gates). Adapting an existing repository needs an agentic coder in a sandbox, which SBAM does not have. |
| Integrate into one implementation | **Missing today.** Each Sub-Skill is built and tested alone. Nothing passes one step's output to the next at run time. |
| One MCP server with many tools | **Half there.** Each Sub-Skill is already a FastMCP server with two tools (`server/templates/fastmcp-loopy/1.0.1/server.py.j2`). What is missing is the composition into one server. |
| Separate tool exposure from orchestration | **Correct and necessary.** Today the docs assign the loop to "the runtime (BuLLM DMR in SRIM)", which does not exist. The orchestration has to be designed here. |
| Reusability and central registry | **Missing.** SBAM's "runtime registry" is a list of libraries in the runtime image, not a registry of built servers. |
| Testing and certification | **Real for a Sub-Skill, stubbed above it.** `certify_macros`, `validate_workflow`, `certify_workflow` and `freeze_and_version` are stubs (`server/workers/sbam_workers/certify/stubs.py`). |
| Immutable versioned artifact | **Hashes exist, signatures do not.** No cosign, no provenance, no licence check (`.github/workflows/`). |
| SRIM handover | **Described only.** The docs reserve `/handover/*` and plan a checksummed export zip (E14.5); neither is built. |

Where the proposal should change:

- **Assemble, do not regenerate.** The prompt reads as if code is generated for the workflow as a whole. Keeping
  generation per Sub-Skill keeps units small and test-verified, which is where code-writing models are reliable.
- **Do not let an agent rewrite a cloned repository as the default.** Evidence in §6.4.
- **Do not pick an orchestration engine before stating what the workflows need.** ISATVON workflows are linear
  per stage, which decides the question (§5).
- **Certification must bind the assembled artifact**, including the wiring between stages, not a set of
  individually certified parts.

---

## 3. Where SBAM stands today

### 3.1 What exists

| Area | State | Source |
|---|---|---|
| Breakdown | Agent (stage) and Sub-Skill (step), ISATVON 1.4, linear `next` chain per Agent (rule 8) | `sbam_contracts/isatvon14.py` |
| Manifests | Skill Manifest per Sub-Skill per build; Workflow manifest draft with the dependency graph | `build/manifest.py`, `build/workflow_manifest.py` |
| Schema | `build_schema_sql`, checked in a throwaway database | `build/schema_sql/` |
| Code | Jinja2 template `fastmcp-loopy` 1.0.1 plus LLM-written bodies; one FastMCP server, two tools (action, check) per Sub-Skill | `server/templates/fastmcp-loopy/` |
| Gates | Eight AST gates, Ruff, Black, MyPy strict, Bandit, ClamAV, library allowlist | `build/gates/` |
| Tests | Generated cases, mock API, rubric grader, CubeSandbox microVM per run, egress evidence | `test/`, `sandbox/` |
| Certification | Per Sub-Skill, with an evidence checklist and an immutable certificate | `certify/subskill.py`, `certify/checklist.py` |
| Build orchestration | Temporal `ProjectPipeline` and `SubSkillBuild` | `pipeline/` |

### 3.2 What is missing for this goal

- **Run-time chaining.** The dependency graph (`build/dag.py`) is used only to order builds and to draw a picture.
- **A run id in the data.** A handoff row is `(skill_id, payload)` only (`state_handoff.py.j2`), so two runs of the
  same workflow cannot be told apart in a handoff table.
- **Typed wiring.** A downstream input is matched to an upstream output by name only.
- **Workflow-level validation, certification, versioning and export.** All stubs or placeholders.
- **A repository field, licence checks, signing, a registry of servers, anything for SRIM.**

### 3.3 Two gaps to close first

1. **`IMPORT_RESOLUTION` is required but never produced.** The certification checklist requires that gate, and the
   gate is not wired into `SubSkillBuild` (`build/gates/import_gate.py` says "Not registered yet"). On this reading
   a real build would stop with `MISSING_EVIDENCE:IMPORT_RESOLUTION`.
2. **The path has not been proven against a real sandbox.** ADR 0023 says the adapter has "never run against a
   real CubeSandbox". No script builds the sandbox template for a runtime digest; the runbook only describes it.
   On a laptop the test worker also has no network route to the sandbox host.

Until one Sub-Skill certifies for real, the workflow-level work has nothing to assemble.

---

## 4. Deliverables 2 and 5: recommended architecture and MCP server design

### 4.1 End-to-end flow

```
approved SOW
  -> Workflow + ISATVON (stages = Agents, steps = Sub-Skills)          exists
  -> schema.sql                                                        exists
  -> per Sub-Skill: reuse intake -> code -> gates -> sandbox tests
                    -> Sub-Skill certificate                            exists, extended by reuse (§6)
  -> ASSEMBLE: wiring check + one workflow server package              new
  -> VALIDATE: end-to-end run in CubeSandbox, MCP checks, scans        new (replaces stubs)
  -> CERTIFY + FREEZE: version, image digest, signature                new (replaces stubs)
  -> PUBLISH: registry entry in SBAM + image in the container registry new
  -> SRIM: pull by digest, verify, deploy, replay acceptance run       new
  -> other projects: discover in the registry, connect through Bifrost new
```

### 4.2 Inside the workflow server

```
+----------------------------- one container, one process -----------------------------+
|  MCP layer (FastMCP 4, Streamable HTTP, OAuth resource server)                        |
|    workflow tools: run_start  run_status  run_result  run_cancel  run_resume          |
|    stage tools:    <stage>.run            (optional, one per Agent)                   |
|    step tools:     <step>.action  <step>.check   (hidden by default, for debugging)   |
|                                                                                        |
|  Runner (platform code, not generated)                                                 |
|    reads workflow_wiring.json and each step's loop_spec.json                           |
|    for each stage, for each step in `next` order: action -> check loop, gates, budget  |
|                                                                                        |
|  Step packages (one per Sub-Skill, exactly the certified bundles)                      |
|    models.py state.py state_handoff.py action_tool.py check_tool.py vendor/ (if any)   |
+----------------------------------------------------------------------------------------+
            |                                             |
     PostgreSQL (schema.sql tables,               external APIs and files
     handoff tables, run tables)                  named in the manifests
```

Design points:

- **Composition.** Each certified Sub-Skill bundle is mounted into the workflow server unchanged, under a
  namespace (FastMCP `mount`, in-process). The bundle hash in the Sub-Skill certificate is the hash of what is mounted.
- **Three levels of tools, one default view.** A workflow with 20 steps has 40 step tools. Tool selection by a
  language model degrades past roughly 30 to 50 tools ([Anthropic tool search guidance](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)).
  The default listing therefore shows the workflow tools and the stage tools. Step tools are listed only for a
  caller with a debug scope; the MCP specification allows the listing to vary by the authorization presented.
- **Typed contracts.** Every tool has an `inputSchema` and an `outputSchema`. The specification requires a server
  to return results that conform to its `outputSchema`, which makes the stage-to-stage contract testable.
- **Stateless protocol.** The current specification (2026-07-28) removed sessions; state across calls is an
  explicit handle passed as an argument. The run id is that handle. Any replica can serve any call as long as
  run state is in PostgreSQL.
- **Transport and auth.** Streamable HTTP when deployed; the in-memory client in tests. The server is an OAuth 2.1
  resource server and validates that the token audience is itself. SRIM, as a machine caller, uses client credentials.
- **Sync or async: hybrid.** `run_start` takes an optional wait budget. If the run finishes inside it, the result
  comes back directly; otherwise the caller gets the run id and polls `run_status`. The Tasks extension can be added
  later as a second way to do the same thing.

### 4.3 How stages pass data

Today each step appends its output to an append-only handoff table and the next step reads what it needs.
That mechanism stays; three things are added:

1. **`run_id` on every handoff row and in every state query**, so concurrent runs do not see each other's data.
2. **A wiring map, `workflow_wiring.json`,** produced at assembly. For every required input of every step it
   records the single source: a workflow input, a named output of an earlier step, or fresh state.
3. **A wiring gate.** Assembly fails when an input has no source or two sources, or when the upstream output type
   does not fit the downstream input type. Today the match is by name only.

### 4.4 Component responsibilities

| Component | Owns | Does not own |
|---|---|---|
| Step package (generated) | One step's action and its independent check | Loops, ordering, other steps |
| Runner (platform) | Step order, loops, gates, budget, run state, resume, cancel | Business logic |
| MCP layer | Tool listing, schemas, auth, structured results, errors | Run state |
| PostgreSQL | Workflow tables, handoff rows, run and step records | |
| SBAM | Build, test, certify, version, publish | Running client workloads |
| Registry (in SBAM) | Which versions exist, their tools, digests, certification, status | Storing the image bytes |
| Container registry | Image bytes by digest, signatures, attestations | |
| Bifrost | Connecting consumers to deployed servers, tool filtering per key | Certification, versions |
| SRIM | Deploying a certified digest, configuration, secrets, calling the tools | Building or changing code |

---

## 5. Deliverables 3 and 6: orchestration design and technology choices

### 5.1 What the workflows need

- Steps in a stage form **one linear chain**: no branch, no cycle (ISATVON rule 8).
- Each step is a **bounded loop**: at most 10 iterations, with success, no-op and escalation conditions.
- Every pass **already writes a durable row**.
- An escalation needs **a person**, possibly hours later.
- Runs can **outlast a client connection**.

### 5.2 Options

| Option | Durable | Extra service to run per deployment | Fit |
|---|---|---|---|
| Plain in-process loop, no stored state | No | None | Too weak: a crash loses the run. |
| **Runner with run tables in PostgreSQL** | Resume from the last finished step | None (PostgreSQL is already required) | **Recommended.** |
| DBOS Transact (MIT, checkpoints in PostgreSQL, library only) | Yes | None | Keep as the upgrade if hand-written checkpointing grows. |
| Temporal (MIT) inside each artifact | Yes, strongest | A Temporal cluster or cloud account per environment | Too heavy for one server per workflow; defeats the cost goal. |
| LangGraph (MIT library) | Checkpoint per step | None for the library | Built for model-driven graphs; these workflows are deterministic chains. |
| Restate (BSL 1.1), Inngest (SSPL server) | Yes | A server | Licences are not open source; excluded. |
| SRIM owns orchestration | Depends on SRIM | Unknown | Nothing exists on SRIM, so it cannot be relied on. |

### 5.3 Recommendation

**A platform-owned runner with run state in PostgreSQL.**

- Tables `sbam_runs` and `sbam_run_steps`, added to `schema.sql` by the platform the same way the insert-only
  guard is added today.
- The runner records each step's start, each pass and each terminal state. `run_resume` continues after the last
  finished step. A step must therefore be safe to run again; the existing append-only handoff and the per-pass
  state re-read already point that way, and an idempotency key per pass closes the gap for external calls.
- An escalation ends the step as `ESCALATED` and the run as `input_required`. `run_resume` with the person's
  answer continues it.
- **The runner is the sandbox harness, promoted.** `server/workers/sbam_workers/test/harness.py` already
  implements the loop semantics for tests. Moving that code into a package that ships in the runtime image means
  tests and production run the same loop. This settles the open item on parity with SRIM (O-17) by construction.

**The seam for later.** The runner calls each step through one function: run step X for run R. That function is
an idempotent unit, which is what any durable engine wants as an activity. If SRIM later runs Temporal, it can
drive the same steps through the step tools and ignore the embedded runner.

**When to move to a durable engine:** steps that run in parallel, approvals that last days with timers, or
external side effects that must happen exactly once and cannot be made idempotent.

Temporal stays where it is today: orchestrating the build inside SBAM.

### 5.4 Technology choices

| Need | Choice | Why |
|---|---|---|
| MCP framework | FastMCP 4 (Apache-2.0), already pinned at 4.0.10 in the runtime | Built for the stateless protocol; mounting, in-memory test client, tool versioning |
| Protocol target | MCP 2026-07-28, Streamable HTTP | Current revision; HTTP+SSE is deprecated |
| Run state | PostgreSQL | Already required by every workflow |
| Long-running calls | Explicit run tools; Tasks extension later | No listed client supports Tasks yet |
| Artifact | OCI image by digest | Immutable identity, existing GHCR pipeline |
| Signing | cosign, with attestations for SBOM, provenance and certification | Makes "certified" verifiable outside SBAM |
| Licence check | ScanCode Toolkit | Detects licences in source, not only declared ones |
| Vulnerability check | pip-audit or OSV-Scanner on the workflow's lock, Trivy on the image | pip-audit and Trivy are already in CI |
| MCP checks | Own schema tests through the in-memory client, MCP Inspector CLI, an offline MCP scanner | See §8 |
| Gateway | Bifrost | Already in the stack; aggregates and filters tools |

---

## 6. Open-source reuse and code generation

### 6.1 Manifest changes

A new list on the Sub-Skill, in the T (tech stack) group, next to `libraries`:

| Field | Meaning |
|---|---|
| `repo_url` | The repository |
| `ref` | Tag or branch while authoring; resolved to a commit SHA and frozen when the build starts |
| `licence` | SPDX id, filled by the intake scan, not typed by hand |
| `mode` | `LIBRARY`, `VENDOR` or `ADAPT` (§6.2) |
| `paths`, `entrypoints` | Which files and functions are used |
| `covers` | Which part of the step it provides, in words |
| `rationale` | Why this repository was chosen |

Where candidates come from: the Agents master already has a `repo` column per agent. That becomes the catalogue.
ISATVON extraction proposes, per Sub-Skill, entries from that catalogue; the FDE reviews them in the Grid. This
keeps the rule that repositories are selected from the manifest data, never discovered from scratch.

The build record in the manifest gains a `reuse` block: resolved commit, file hashes, licence result and, for
adapted code, the hash of the diff against upstream. This is how reused, customised and generated code stay
distinguishable.

### 6.2 Three modes, strictest first

| Mode | What happens | Allowed to change the code | When to use |
|---|---|---|---|
| `LIBRARY` | The repository is a published package. It is pinned with a hash and added to the runtime image. | No | Default. Visible to the SBOM and to vulnerability scanners. |
| `VENDOR` | Named files are copied at the pinned commit into the step's `vendor/` folder. | No | The code is not packaged, or only part of it is wanted. |
| `ADAPT` | Vendored, then modified. | Yes, recorded as a diff | Only when an adapter around the unchanged code cannot produce the required output. |

In every mode the generated step calls the reused code through a **thin adapter** that maps the step's typed input
and output to the library's functions. The typical case in the brief, a repository that gives 50 to 60% of the
value, is `LIBRARY` or `VENDOR` plus a generated adapter and generated code for the remainder.

### 6.3 Intake

A new activity, **repo intake**, is the only place that touches the network:

1. Fetch the repository at the resolved commit, from an allowlist of hosts.
2. Scan: licence (allowlist such as MIT, Apache-2.0, BSD; copyleft and source-available licences blocked by
   policy), known vulnerabilities, malware.
3. Store a content-addressed snapshot through `StorageBackend`, with an index of files and symbols.

Code generation reads only the snapshot. Vendored and adapted files go through the existing static gates, with
a separate gate profile, because third-party code will not match the template's shape.

### 6.4 Generating what is missing

- **Slot filling stays the default.** It is confined, cheap and already gated.
- **`ADAPT` needs an agentic coder**: a model that edits files and runs tests in a sandbox with no network, with
  the step's test cases as the judge. Its model calls go through Bifrost under a new alias, as the golden rules
  require. This is new infrastructure and the riskiest item in this document.
- **Evidence for caution.** In FeatureBench (ICLR 2026) the top model resolves 74.4% of small bug-fix tasks but
  11.0% of feature-sized tasks ([arXiv 2602.10975](https://arxiv.org/abs/2602.10975)). No study was found that
  compares adapting a repository with generating from scratch. The reasonable reading: keep each change small,
  behind a fixed interface, and judged by executable tests.

---

## 7. Deliverable 7: registry of certified MCP servers

### 7.1 Build or adopt

| Option | Verdict |
|---|---|
| The public MCP Registry | Not for private servers. |
| Self-host the official registry service | Still in preview with warnings about data resets, and it needs its own database. Not yet. |
| **Registry tables in SBAM, exposing the official registry's API shape** | **Recommended.** SBAM already holds certifications and already enforces immutability. |
| A standalone catalogue such as ToolHive's registry server (Apache-2.0) | Revisit if the registry must live outside SBAM. |
| Bifrost | **Not a registry.** Its documentation has no concept of versions, artifacts or certification. It is the gateway (§10). |

Adopting the official API shape (`GET /v0.1/servers`, versions per server, a `server.json` per version) means any
registry-aware client or gateway can read it later without a custom integration. The official registry stores
metadata only and points at packages hosted elsewhere, which matches the split proposed here.

A naming note: SBAM already has a "runtime registry", which lists the libraries in the runtime image. The new one
should be called the **server registry** everywhere to avoid confusion.

### 7.2 What an entry holds

- Name and unique id, description, the Workflow it implements, owner.
- Version, protocol revision, status: `ACTIVE`, `DEPRECATED` or `REVOKED`.
- Tools with their input and output schemas, and a hash of the whole tool surface.
- Image reference **by digest**, runtime image digest, the dependency lock hash.
- Certification: status, date, certificate id, links to the test and scan evidence.
- Configuration the deployer must supply: database, secret names, external endpoints.
- Compatibility: required runtime, schema version.

### 7.3 Operations

1. **Publish** happens only as the last step of certification; there is no manual publish.
2. **Discover and inspect** through the read-only API.
3. **Deprecate and revoke.** Revocation is how a version with a vulnerable dependency is taken out of use. A
   nightly job re-runs the vulnerability check against the lock of every active version and flags hits.
4. **Reuse before build.** When a build starts, SBAM looks for an existing certified match at two levels: a whole
   server for the same workflow definition, and, more often, a single Sub-Skill. The reuse key for a Sub-Skill is
   already written (`certify/reuse.py`) but not wired in; this work wires it.

Reuse across **tenants** is a policy question, not a technical one, because every SBAM query is tenant-scoped
today. It is listed in §12 for a decision.

---

## 8. Deliverable 8: validation strategy

| Level | What runs | Where | Status |
|---|---|---|---|
| A. Component | Generated cases per step: first-pass success, success after retry, escalation, no-op, edge cases | CubeSandbox | Exists |
| B. Integration | Wiring gate; each stage run as a chain on synthetic data | Assembly, CubeSandbox | New |
| C. End to end | The assembled server, driven only through its MCP tools: start, status, result; final output checked | CubeSandbox | New (`validate_workflow`) |
| D. Reliability | Kill the process mid-run then resume; an external dependency failing then recovering; a step that escalates; cancel; unexpected input | CubeSandbox with the mock API | New |
| E. Security and quality | Existing gates per step; vulnerability scan of the lock; licence scan; image scan; secrets by name only; token audience checked | Build and CI | Partly exists |
| F. MCP compliance | Tool listing is stable; every schema is valid JSON Schema 2020-12; every result conforms to its `outputSchema`; error shapes; auth challenges | In-memory client, Inspector CLI | New |
| G. Orchestration | State survives a restart; two runs at once do not mix data; timeouts and cancellation; a long wait on an escalation | CubeSandbox | New |

**Certification criteria for a Workflow server.** All of these, on the same artifact:

- Every Sub-Skill certificate is valid and its bundle hash equals the mounted bundle.
- The wiring gate passed, and the wiring map hash is recorded.
- Levels B, C, D, F and G passed in the certifying sandbox, with egress evidence.
- No known critical vulnerability with a fix available; no blocked licence.
- The SOW is still the approved one, as the Sub-Skill checklist already requires.

**On the official MCP conformance suite.** Its latest tagged release (March 2026) predates the current
specification revision. Until it is confirmed to cover the stateless protocol, SBAM's own compliance tests are the
gate and the official suite is an extra signal.

**Observability.** The specification documents OpenTelemetry trace context in request metadata. The server emits a
trace per run and a span per step and pass, plus structured logs with the run id. Audit history is the run and
handoff tables themselves, which are append-only.

---

## 9. Deliverable 9: certification and reproducibility

### 9.1 What is certified

One identity: **the image digest**. The image is the runtime image at a pinned digest plus one layer holding the
workflow package. Building it is deterministic, so the same inputs give the same digest.

The certificate (the existing `certifications` table, level `WORKFLOW`) records:

- Server name and version; image digest; runtime image digest.
- Hash of every Sub-Skill bundle, of the wiring map, of `schema.sql`, of the runner version, of the tool surface.
- The dependency lock and its hash; the SBOM.
- For reused code: repository, commit, licence, and diff hash where adapted.
- Test evidence for levels A to G; scan results.
- Sandbox mode (only the certifying mode counts), time, and the pipeline run that certified it.

### 9.2 Signing

Hashes prove that nothing changed; they do not prove who certified it. Today nothing is signed (TD §11.5 lists
signing as future scope). The proposal:

- Sign the image digest with cosign.
- Attach three attestations to the digest: the SBOM, build provenance, and a **certification attestation** that
  carries the certificate summary.
- The consumer verifies the signature, the signer identity and the certification attestation before running.

"Certified" then becomes a property that can be checked from outside SBAM, not a row in SBAM's database.

### 9.3 Versions and recertification

Versions keep the existing rule (`MAJOR.0.PATCH`, TD §11.4): MAJOR on a new approved SOW, PATCH on a rebuild.

| What changed | What must be redone |
|---|---|
| One Sub-Skill's code, tests or ISATVON | That Sub-Skill's build and certificate; assembly; workflow levels B to G |
| Only the wiring or `schema.sql` | Assembly; workflow levels B to G |
| The runtime image or the runner | Every Sub-Skill's sandbox tests; workflow levels B to G |
| A tool's input or output schema, incompatibly | The above, and a MAJOR version |
| Deployment configuration only (endpoints, secrets) | Nothing in SBAM; the SRIM acceptance replay (§10) |

One server per workflow has a known cost: a change to one step re-certifies the whole artifact. Wiring in the reuse
key keeps that affordable, because unchanged Sub-Skills keep their certificates and only the workflow-level tests re-run.

A certified version never changes. The existing database trigger that refuses changes to frozen rows (HTTP 423)
covers the new tables too.

---

## 10. Deliverables 10 and 11: deployment, SRIM handover and reuse

Nothing exists on the SRIM side, so this section **proposes the contract**. Every point here is an assumption
until SRIM's owners confirm it.

### 10.1 What SRIM receives

A registry entry: name, version and **image digest**. Not a zip of source files. The checksummed export package
that the current plan describes (E14.5) is kept as the offline form of the same content, built from the same bytes.

### 10.2 What SRIM does

1. Pull the image by digest and verify the signature and the certification attestation. Refuse on failure.
2. Supply configuration: database connection, the secrets the manifest names, external endpoints.
3. Apply `schema.sql` to the target database with the migration command shipped in the image.
4. Start the server and connect over Streamable HTTP with client credentials.
5. List the tools and compare the tool surface hash with the registry entry.
6. **Replay the acceptance run.** The image carries the end-to-end smoke case that certified it. SRIM runs it
   against its own environment. This is the answer to "SBAM and SRIM do not share an environment": the same
   artifact plus the same test, executed where it will run.
7. Hand the workflow to the client.

### 10.3 Reuse by other projects

- **Discover** in the server registry; **connect** through Bifrost, which aggregates the deployed servers behind one
  endpoint and limits which tools each consumer key may call. SBAM pins Bifrost v2.2.6 today and uses it only for
  language models; whether that version has the MCP gateway features needs checking (§13).
- **Shared instance or one per project.** The image is the same either way; configuration and secrets are injected at
  deployment. Recommendation: **one deployment per project** by default. It keeps data, secrets and upgrade timing
  separate, and the stateless protocol makes an idle deployment cheap to scale down. A shared instance is cheaper
  but needs strict isolation of run data per tenant and forces all consumers to upgrade together.
- **Compatibility.** Consumers pin a digest. A published tool name never changes meaning. Adding an optional field
  is a PATCH; a breaking schema change is a MAJOR and the old version stays available until deprecated.

### 10.4 Cost

No controlled study of cloud cost by MCP server granularity was found. The reasoning for one server per workflow
holds: one memory footprint and one minimum replica instead of one per step, data passed in-process, and one
pipeline for auth, scanning and certification. The trade-offs are a larger blast radius, no per-step scaling, and
the union of all steps' dependencies in one image. A step with a very different resource profile is the one case
worth splitting out.

---

## 11. Deliverable 4: implementation strategy

| Phase | Delivers | Depends on | Milestone |
|---|---|---|---|
| 0. Prove today's path | `IMPORT_RESOLUTION` wired; scripted sandbox template build; worker route to the sandbox; **one Sub-Skill certified in CubeSandbox** | Nothing | First real certificate |
| 1. Contracts and decisions | ADRs for this document; manifest reuse fields; `run_id` in handoff; wiring map; run tables; tool surface contract | Phase 0 | Contracts merged |
| 2. Runner and assembly | Runner package shared with the sandbox harness; wiring gate; workflow server with run tools | Phase 1 | A workflow runs end to end locally |
| 3. Workflow validation and certification | Real `certify_macros`, `validate_workflow`, `certify_workflow`, `freeze_and_version`; reuse key wired; versions page | Phase 2 | First certified workflow server |
| 4. Artifact and registry | Deterministic image; cosign signature and attestations; server registry API and page; export package | Phase 3 | A version published with a verifiable signature |
| 5. Open-source reuse | Repo intake with licence gate; `LIBRARY` and `VENDOR` modes; then `ADAPT` with the agentic coder | Phase 1 for the first two modes; Phase 3 for `ADAPT` | A step built on a reused repository certifies |
| 6. SRIM and reuse | Handover contract agreed; verification and acceptance replay; Bifrost gateway registration; cross-project reuse | Phase 4, and SRIM existing | A client run through SRIM |

Notes:

- Phase 5's first two modes do not depend on Phases 2 to 4 and can run alongside them.
- Contracts land first, in their own small changes, as the team rules require. Most touched files are owned by the
  contracts and orchestration lane.
- The existing decisions that this changes need superseding ADRs: ADR 0010 and 0011 (the loop is run by SRIM; the
  artifact is FastMCP tools per Sub-Skill) and the plan for E14.5 (export as a zip).

---

## 12. Deliverable 12: risks, and decisions needed

### 12.1 Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Today's path has never certified for real | Everything above it is built on an unproven base | Phase 0 before anything else |
| Agents adapting repositories fail often | `ADAPT` steps never pass | Library and vendor modes first; adapters; small changes judged by tests; keep `ADAPT` last |
| Reused code brings a licence or vulnerability problem | A certified artifact has to be withdrawn | Intake gate; pinned commits; nightly re-scan; revocation in the registry |
| Third-party code does not fit the "generated code is hostile" gates | Either the gates are weakened or reuse is blocked | A separate gate profile for vendored code; the sandbox remains the real boundary |
| One step change re-certifies the whole server | Slow iteration | Reuse key; only workflow-level tests re-run |
| Sandbox templates are per runtime digest and built by hand | Each runtime change stalls testing | Script the template build in Phase 0 |
| Run data mixes between runs | Wrong results under concurrency | `run_id` everywhere; a concurrency test in level G |
| A step is not safe to re-run | Resume duplicates a side effect | Idempotency key per pass; mock-API tests for it |
| Too many tools for a model-driven caller | Wrong tool chosen | Workflow and stage tools by default; step tools behind a scope |
| The MCP specification is moving | Rework | Pin the revision per certified version; the framework negotiates older clients |
| SRIM turns out to work differently | The handover contract changes | Keep the artifact self-contained; keep the step tools so SRIM can orchestrate itself |
| Signing keys are mishandled | Signatures mean nothing | Key in a managed store; signer identity checked at verification |

### 12.2 Decisions needed from the product owner

1. **Who runs the workflow in production: the server's embedded runner (recommended), or SRIM?**
2. **May certified code be reused across tenants?** Today everything is tenant-scoped.
3. **Which licences are allowed for reused code?**
4. **Shared instance or one deployment per project** as the default for SRIM?
5. **Is the agentic coder for `ADAPT` in scope now**, or after the first workflow server is certified?
6. **Where do signing keys live, and who is the certifying identity?**

---

## 13. What could not be verified

About SBAM:

- Whether a real build stops at `IMPORT_RESOLUTION`; this is read from the code, not observed.
- Who builds the CubeSandbox templates today and how.
- How SRIM would run a server; the repository says nothing beyond default strings in the manifest.
- The content of the GitHub issues for the stubbed stages (#130, #132, #133, #148).

About outside technology:

- Which specification revisions the official Python SDK implements, and its support for the Tasks extension.
- Whether the official conformance suite covers the 2026-07-28 revision.
- Whether Bifrost v2.2.6 includes the MCP gateway features, and which are enterprise-only.
- Any measured cloud-cost comparison of MCP server granularity.
- Any direct comparison of adapting a repository against generating from scratch.
- Tool-count limits of specific clients other than the Anthropic guidance.

---

## 14. Sources

SBAM (paths under the repository root):

- Contracts: `server/contracts/sbam_contracts/isatvon14.py`, `manifest.py`, `masters.py`, `state_machines.py`, `sandbox.py`
- Build: `server/workers/sbam_workers/build/` (`manifest.py`, `dag.py`, `plan.py`, `gates/`, `schema_sql/`), `server/templates/fastmcp-loopy/1.0.1/`
- Tests and sandbox: `server/workers/sbam_workers/test/`, `sandbox/`, `compose/sandbox-runner/`
- Certification: `server/workers/sbam_workers/certify/` (`subskill.py`, `checklist.py`, `reuse.py`, `stubs.py`)
- Pipeline: `server/workers/sbam_workers/pipeline/`
- Decisions: ADR 0010, 0011, 0012, 0013, 0023, 0026, 0027; TD §8, §10, §11, §12, §15, §20; phase 14

Outside:

- MCP specification 2026-07-28: <https://modelcontextprotocol.io/specification/latest>, changelog <https://modelcontextprotocol.io/specification/2026-07-28/changelog.md>
- Tools: <https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md>; authorization: <https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization.md>
- Tasks extension: <https://modelcontextprotocol.io/extensions/tasks/overview.md>; client support matrix: <https://modelcontextprotocol.io/extensions/client-matrix.md>
- FastMCP: <https://gofastmcp.com/docs/getting-started/whats-new.md>, composition <https://gofastmcp.com/docs/servers/composition.md>, testing <https://gofastmcp.com/docs/servers/testing.md>
- Tool count guidance: <https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool>
- Durable tools pattern: <https://temporal.io/blog/building-long-running-interactive-mcp-tools-temporal>, <https://devblogs.microsoft.com/azure-sdk/long-running-mcp-tools-azure-functions/>
- MCP Registry: <https://github.com/modelcontextprotocol/registry>, <https://modelcontextprotocol.io/registry/registry-aggregators.md>
- ToolHive registry server: <https://docs.stacklok.com/toolhive/guides-registry/intro>
- Bifrost MCP: <https://docs.getbifrost.ai/mcp/overview.md>, <https://docs.getbifrost.ai/mcp/filtering>
- Artifact attestations: <https://docs.github.com/en/actions/concepts/security/artifact-attestations>
- Restate licence: <https://raw.githubusercontent.com/restatedev/restate/main/LICENSE>
- FeatureBench: <https://arxiv.org/abs/2602.10975>
