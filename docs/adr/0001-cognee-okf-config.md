# 0001 — Cognee and OKF bundle configuration

- Status: Accepted
- Date: 2026-10-06
- Related: issue #26 (E0.9), spike [cognee-okf.md](../spikes/cognee-okf.md), TD §5.3, C-13; consumed by #78, #80, #81, #82, #86

## Context

SBAM builds a per-project knowledge graph with Cognee and exports an OKF bundle that must preserve all ingested
information (C-13). The spike measured isolation, master-context injection, gateway routing, bundle content and size,
and failure behaviour (see the spike report for numbers and method).

## Decision

1. **Engine and stores.** Cognee 1.6.2 with its default stores (graph Ladybug, vector LanceDB, relational SQLite) on one
   Docker volume `cognee_data` mounted at `/data/cognee` (`DATA_ROOT_DIRECTORY=/data/cognee/data`,
   `SYSTEM_ROOT_DIRECTORY=/data/cognee/system`). `ENABLE_BACKEND_ACCESS_CONTROL=true`, and the worker fails its health check if it is not.
2. **Isolation = dataset + user, per project.** Dataset name `sbam_<tenant_uuid>_<project_uuid>`, derived server-side and
   matched against `^sbam_[0-9a-f-]{36}_[0-9a-f-]{36}$` before use. **Each project also gets its own Cognee user** that owns only
   that dataset, because dataset scoping alone leaked on an unscoped search (spike T01). The adapter additionally refuses any
   call that lacks a `ProjectScope`. A Cognee `DatasetNotFoundError` maps to SBAM's 404.
3. **Gateway only.** Cognee's `LLM_ENDPOINT` and `EMBEDDING_ENDPOINT` point at Bifrost. The Cognee container receives no
   OpenRouter key and no Bifrost secret (pass variables one by one, never `env_file`) and sits on a network with no internet route.
4. **Master context.** Admin masters reach Cognee as a context document built from the frozen snapshot (E3.2) through the
   E3.6 mapping, so the same input gives the same file hash. Organization entries are graph roots. A master entry with no
   Parent Entity, or an unknown one, attaches to the Organization root with a warning (a recommended addition to #59). ILLUSTRATIVE,
   RETIRED and (unless allowed) RESTRICTED entries never reach Cognee.
5. **OKF bundle** as in TD §5.3 (`bundle.json`, `entities.jsonl`, `relations.jsonl`, `chunks.jsonl`, `masters.jsonl`,
   `coverage.json`) with these refinements:
   - `chunks.jsonl` is built from the **normalized documents** (`doc.md` + `doc.json` blocks), not from Cognee's chunker.
     Joined chunk text equals `doc.md` byte for byte, independent of graph extraction.
   - Entities and relations carry `source_refs` of `{document_version_id, chunk_id, location}`. A record without a source ref is dropped and counted.
   - Entities that originate from master context carry a `master_ref` (snapshot id + entry id) as their source, so they are not dropped.
   - `created_at` is stored outside the hashed manifest so rebuilds of identical input are byte-identical. `okf_sha256` covers the manifest, and `parts` lists each file's SHA-256.
   - Statuses: `COVERED`, `CHUNKS_ONLY` (chunks, 0 entities), `FAILED`.
6. **Context sizing.** Use the full bundle when it fits the model window minus a reserve; otherwise rank chunks per SOW section.
   The first-cut ranking is embedding similarity through Bifrost with the same embedding model, but the spike showed it is
   **not sufficient with bare section titles**. #86 must use richer section queries, a length/information penalty and graph
   proximity before relying on it. The selection is recorded in `llm_calls.context_manifest`.
7. **Failure handling.** The adapter classifies Cognee errors itself. A Bifrost outage surfaces from Cognee as an HTTP 422
   `EmbeddingException`, which must be raised as a retryable `CogneeUnavailable`. `cognify` can run for minutes before
   failing (157 s observed), so the Temporal activity heartbeats from a separate task. No bundle is written unless `cognify` succeeded.

## Consequences

- #78 must create and manage one Cognee user per project, with credentials derived or stored server-side.
- #39 and #46 must keep Cognee off the internet and secret-free, enable Bifrost's config store for virtual keys, and verify the `sbam-*` alias mapping (not tested in the spike).
- #81 implements the bundle as above; #59 adds the parent-attachment rule; #21/#59 resolve the `ORG-001` mismatch and the Skills #1 exclusion reason.
- Every rebuild is a new knowledge version: LLM extraction is nondeterministic, only chunks, masters, sources and coverage are reproducible.
- **Not covered by the spike:** large documents (up to 15 MB), streamed writes, Cognee's chunker at scale, extraction quality across models, cost, and the real Admin snapshot. Retest these in #78, #80 and #81.
