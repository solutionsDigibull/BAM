# LLM cost control, phase 2: what is done and how the rest is split (#421)

Design note, not an ADR. Written 2026-11-19. It splits the remaining work of issue #421 so each part can be
filed, built and verified on its own.

## State of #421

| § | Topic | State | Where |
|---|---|---|---|
| 1 | Per-document map/reduce for the SOW | **Open, deferred** | see below |
| 2 | An explicit Regenerate bypasses the cache | **Done** | `SowGenerateInput.fresh` (#595, #596); `sow/regenerate.py` sets `fresh=True` and the worker passes it to `llm.run` (#597) |
| 3 | Incremental Cognee rebuild | **Open, deferred** | see below |
| 4 | Retention of cache entries and their `llm_calls` rows | **Done** | ADR [0015](../adr/0015-llm-gateway.md) retention section (#662) |
| 5 | Cache poisoning and invalidation | **Done** | admin invalidation of cached answers (#665) |

§1 and §3 are large and cannot be verified without real model and Cognee calls, which CI and this team's
rules forbid (mock providers only). They are deferred and should each become **its own issue**. No behaviour
changes with this note.

## §1 — Per-document map/reduce SOW generation (new issue)

Today `sow/generate.py` builds one budget-fitted context from the whole knowledge bundle and makes one
`llm.run` call, so adding one document changes the cache key and regenerates the whole SOW.

Scope:
- A **map** step: one cached call per document that produces a digest, keyed by content hash plus prompt
  version (a new prompt under `llm/prompts/`), so an unchanged document is a cache hit.
- A **reduce** step: a cheaper call over the digests that produces the `SowDocument`.
- Digests carry the source references they were made from, so the reduce output can cite them.
- Eval and golden cases for map and reduce; mock-provider unit tests; a cost comparison on a recorded corpus.
- A switch (setting or prompt version) to fall back to the single-call path, so rollout is reversible.
- Keep the budget caps and the `llm_calls` provenance for each map call.

Main risk: **citation integrity (ADR [0001](../adr/0001-cognee-okf-config.md) and
[0008](../adr/0008-knowledge-cognee-okf.md))**. The bundle keeps all ingested information, and a digest must
never replace source text for the citation check (`check_citations` must still resolve against the original
documents). A lossy digest can drop a fact or a quote, and the SOW then cites something the reduce step never
saw. Quality also cannot be judged without real model runs.

## §3 — Incremental Cognee rebuild (new issue)

Today `knowledge/build.py` adds each document, prunes the project's dataset to the build's scope (#450), then
runs a full `cognify`, which takes minutes and re-processes every document.

Scope:
- First a **spike**: read what `cognee==1.6.5` already skips on a repeated `cognify` (per-data processing
  state) and write down the findings before any change.
- Then either cache per-document graph output, or run `cognify` on the delta only (documents added or changed
  since the last READY version).
- Keep the existing steps around it: pin the masters snapshot, the coverage report, and the rule that a build
  failing before anything is added never strips the graph the READY version uses.
- Tests with the fake Cognee backend for the delta selection; one recorded real run on a staging stack to
  compare node and edge counts against a full rebuild.

Main risk: **graph consistency after prune and add**. Pruning removes graph nodes only a removed document
sourced; a delta cognify must not leave orphaned nodes, duplicate entities, or edges to pruned sources, and
the OKF bundle (E5.3) is built from the graph, so an inconsistency reaches the SOW. It is only verifiable with
a real Cognee and model, so it cannot be proved in CI.

## Next step

File two issues from §1 and §3 above (labels as for the other LLM cost-control work) and close #421 with a
link to both and to the commits in the table.
