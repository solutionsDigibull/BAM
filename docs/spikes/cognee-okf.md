# Spike E0.9: Cognee default stores + OKF bundle

Issue [#26](https://github.com/solutionsDigibull/BAM/issues/26) · ADR [0001](../adr/0001-cognee-okf-config.md) · Prototype: [`spikes/cognee/`](../../spikes/cognee/README.md)

## Question

Can Cognee, run with its default stores, give SBAM (1) per-project isolation, (2) master-context injection with
Organization as the root, (3) all LLM and embedding traffic through Bifrost → OpenRouter, and (4) an OKF bundle that
preserves full content and provenance? How large is the bundle compared with a model context window?

**Verdict: GO, with conditions** (see [Recommendation](#recommendation)). Isolation by dataset alone is not enough.

## Setup

| Item | Value |
|---|---|
| Cognee | 1.6.2 (Python 3.12 image), access control on (`ENABLE_BACKEND_ACCESS_CONTROL=true`) |
| Default stores | graph **Ladybug**, vector **LanceDB**, relational **SQLite**, all under one volume (`/data/cognee`, 16 MB after the runs). One folder per owner and one `.lance.db` per dataset |
| Gateway | Bifrost `maximhq/bifrost:v2.2.6`, provider `openrouter`, key read from an env var, config store off |
| Models (spike choice) | chat `openrouter/openai/gpt-4o-mini`, embeddings `openrouter/openai/text-embedding-3-small` (1536 dimensions) |
| Network | runner on an `internal: true` Docker network with Bifrost as its only reachable host; Bifrost also on a normal network |
| Data | synthetic only. Project A: 4 documents (934 characters), marker `ZEBRA-7731`. Project B: 3 documents, marker `QUOKKA-4419`, includes Arabic/CJK text. Master context: the repo workbook (`docs/reference/…Consolidated.xlsx`) through a mock snapshot source |

Cognee settings: `LLM_PROVIDER=custom`, `LLM_ENDPOINT`/`EMBEDDING_ENDPOINT` = Bifrost `/v1`, `EMBEDDING_PROVIDER=openai_compatible`.
The model name needs a LiteLLM prefix (`openai/<id>` for the chat model).

## Results by test case

All results come from [`spikes/cognee/out/results.json`](../../spikes/cognee/out/results.json), produced by `spike.tests`,
and were reproduced on three full rebuilds (the committed bundle is from the third).

| ID | Result | Evidence and method |
|---|---|---|
| `E0.9-T01` Isolation | **Pass** | Searching project A's dataset for B's marker: 0 hits; B for A's marker: 0 hits; own markers found (1 and 2 hits). 0 graph nodes in B reference an A document id. **Caveat below** |
| `E0.9-T01` (stronger) | **Pass** | One Cognee user per project: user B reading A's dataset gets `DatasetNotFoundError` (and the reverse). An unscoped search as user B for A's marker: 0 hits |
| `E0.9-T02` Full content | **Pass** | 4 of 4 documents: joined `chunks.jsonl` text equals `doc.md` byte for byte (934 of 934 characters, compared by `verify_bundle` and again by the report script) |
| `E0.9-T03` Provenance | **Pass** | 22 of 22 entities and 18 of 18 relations carry at least one `source_ref` with `document_version_id` and a location (page+line or sheet+cell). All 22 entities have a block-level ref; 3 entities also have a whole-document fallback ref (the LLM normalized the name so it did not match the text) |
| `E0.9-T04` Master injection | **Pass, with data findings** | Graph has an `Organization ORG-EMS` entity; 16 of 16 included master nodes exist as entities; 16 non-structural edges touch the root; ILLUSTRATIVE rows (`ORG-CORE`, `RULE-CORE`, `SK-CORE`) never reached the graph. Details below |
| `E0.9-T05` Size vs context | **Partly answered** | Measured on the one fixture only: the chunk text of project A is about 300 tokens. How big a real corpus gets, and so when ranking is needed, was **not measured** (the first version of this report extrapolated it; that is removed, see below) |
| `E0.9-T06` Gateway-only egress | **Pass (by topology)** | Runner is attached only to the `internal` network. From the runner: `openrouter.ai:443` and `1.1.1.1:443` are blocked, `bifrost:8080` is reachable. The runner environment holds no OpenRouter or Bifrost-secret variable (checked by name). Two full builds succeeded through Bifrost, so all traffic used it. This is a structural guarantee, not a packet capture |
| `E0.9-T07` Bifrost down | **Pass, with findings** | See below |

### Isolation caveat (important)

With one Cognee user owning both datasets, `search(query)` **without** a `datasets` argument returned the other project's
marker (1 hit). Dataset scoping only works if every call passes it. One Cognee user per project closes this: the
cross-user and unscoped cases both return nothing. Cognee answers `DatasetNotFoundError` for a dataset the user cannot
access, which maps onto SBAM's "cross-tenant returns 404" rule.

### Master injection (T04)

Master rows come from the workbook: 23 entries, 19 included, 4 excluded (3 ILLUSTRATIVE, 1 RESTRICTED). The mapping is
deterministic (identical `masters.jsonl` hash across runs). Findings for #21 and #59:

1. **`ORG-001` has no matching Organization.** Both populated RAG Reference cells declare `Parent Entity: ORG-001`, but the
   Organization codes are `BU-AERO-01`, `ORG-EMS`, `ORG-CAB`, `ORG-CORE`. Treated per #59 (unknown parent attaches to the root with a warning).
2. **15 of 16 included non-Organization entries declare no parent.** Only one RAG Reference parent exists in the data that
   is included, so without a rule the graph would be disconnected from the root. The spike attached them to `ORG-EMS`. Recommendation for #59: make that the rule.
3. **Skills #1 is excluded as RESTRICTED, but #59-T05 expects ILLUSTRATIVE.** Same outcome, different reason. One of them needs correcting.
4. **First row of each tab (BAM 0.2 sample) has no status.** The spike defaults it to PROPOSED, per MSC.
5. **Entities that come from master context have no document source.** E5.3 says a graph node without a source ref is dropped, so
   19 master-derived entities were dropped from `entities.jsonl` (they remain in `masters.jsonl`). Recommendation: let those
   entities carry a `master_ref` (snapshot id + entry id) as their source.

### Bundle size vs context (T05)

Only figures that were measured on this run are reported, each with its method. Nothing here is scaled up to a larger
corpus: the first version of this report multiplied a tokens-per-character ratio from 934 characters to "20 files × 15 MB"
and read the context-window verdict from that. That was an extrapolation from one tiny fixture and it is withdrawn.

| Figure | Value | Method |
|---|---|---|
| Chunk text of project A | 934 characters, about 300 tokens (0.318 tokens per character) | `tiktoken cl100k_base` on the text of the four documents, counted by the report script. One fixture, English text |
| `chunks.jsonl` of project A | 1,088 tokens | `tiktoken cl100k_base` on the whole file. It includes the JSON fields of every chunk record (ids, offsets, source refs), not only the text, which is why it is larger than the text alone |
| `masters.jsonl` of this workbook | 2,730 tokens | `tiktoken cl100k_base` on the whole file |
| Graph (`entities.jsonl` + `relations.jsonl`) of project A | 5,872 tokens | `tiktoken cl100k_base` on both files, one run. Not used for any sizing: on tiny fixtures it is dominated by a fixed cost per record (about 147 tokens per record), so no ratio to text size can be read from it, and the LLM extraction is nondeterministic (finding 5 below) |
| The whole bundle | 9,845 tokens (5,872 graph + 2,730 masters + 1,088 chunks) | Sum of the three counts above |

What follows from this, and what does not. The masters are a constant cost for this workbook (2,730 tokens), so they do not
grow with the documents. The chunk text is the only part whose growth with the documents could be measured, and it needs a
real corpus: a pack of realistic RFQ documents run through the same script. The `sbam-sow` model is not chosen, so no context
window is assumed either. Until that measurement exists the question "when is ranking needed" is open, and the ranking path
below is kept in the design as a safeguard, not as a sized requirement.

**Ranking path exercised** with a forced 100-token budget (the corpus is about 300 tokens): 89 to 100 tokens selected per
section, recorded in `results.json`. Quality finding: cosine similarity between a bare section title and a chunk favours short
headings and the `00 00 00` boilerplate over substantive text. The mechanism works, but #86 needs richer section queries
(title + description), a length/information penalty and graph proximity before it is trustworthy.

### Bifrost down mid-build (T07)

Stopping Bifrost during `cognify`: the call failed after **157 s** with `EmbeddingException ... (Status code: 422)`; pipeline
status `DATASET_PROCESSING_ERRORED`; **no bundle was written**; Bifrost recovered healthy after restart.

- Cognee reports an outage as a **422**, which reads like a client error. The adapter (#78) must classify it as a retryable `CogneeUnavailable`.
- Failure took 157 s because of internal retries. That exceeds the **2-minute heartbeat** in #80, so the activity must heartbeat from a separate task while Cognee works.

## Other findings

| # | Finding | Impact |
|---|---|---|
| 1 | `env_file` in compose copied `OPENROUTER_API_KEY` into the Cognee container; caught during the spike and fixed by passing variables one by one | The product Compose (#39) must never use `env_file` for the Cognee/worker service |
| 2 | Bifrost virtual keys and governance need the **config store** enabled. The spike ran without it (error `governance config is nil`) | #46 must enable the store to use virtual keys |
| 3 | **`sbam-*` aliases not tested.** Bifrost's docs only describe `provider/model` names and routing rules | #46 must verify alias mapping before #85 depends on it |
| 4 | Cognee tries to load a Hugging Face tokenizer for the embedding model name and logs `HFValidationError` (too many `/` segments); embeddings still work | Chunk sizing for large documents uses a fallback tokenizer; verify in #78 |
| 5 | LLM extraction is nondeterministic: across full rebuilds `chunks.jsonl`, `masters.jsonl`, sources and coverage statuses were identical, while `entities.jsonl` and `relations.jsonl` differed (project A: 19, 19 and 22 entities in three builds) | Each rebuild is a new knowledge version (as designed); only chunks are reproducible |
| 6 | A document with zero extracted entities (`boilerplate_numbers`) still has full chunks and is `CHUNKS_ONLY` | Edge case confirmed |
| 7 | Bundle `created_at` is excluded from the hashed manifest in the prototype (it would break same-input determinism) | Keep `created_at` outside the hash in the real bundle |
| 8 | Host port 8080 is commonly taken; the spike publishes Bifrost on 18080 | Documentation only |

### Mock vs Admin export (E0.18-T02, #203)

Method: the repo workbook (`docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx`) was
imported for a dev tenant through the masters import service (`import_workbook`, `upsert`, all six
tabs, 0 errors) and exported again through the masters export service (`export`), both run inside
the API container. The export (31,458 bytes) was compared with the workbook by
`python -m spike.diff_masters` and read by `python -m spike.masters --workbook`.

| What | Mock workbook | Admin export |
|---|---|---|
| Entries read by the spike | 23 | 23 |
| Included in the master context / excluded / warnings | 19 / 4 / 16 | 19 / 4 / 16 |
| Tabs and column headers that differ | none | none |
| Entries only on one side | none | none |
| Payload cells that differ | none (as parsed JSON) | none |

The first diff reported 2 differences (`RAG Reference` of `DOM-PCBA-01` and `PRJ-RFQ-2026-001`).
They had the same keys and values and differed only in key order, because the export writes the
keys sorted and the workbook keeps its column order. The diff now compares JSON cells as parsed
JSON, so that is not reported. The order does change `masters.jsonl` (its sha256 is different), so a
spike result that depends on the text of a `RAG Reference` cell can differ in wording between the two.

**What this shows and what it does not.** It shows the Admin import and export keep every field of
this workbook. It does **not** show that the spike passes on a tenant's own data: the export came
from the same workbook, so there is no field the real Admin holds and the workbook lacks.

### The spike on the Admin export (E0.18-T01, #203)

Method: `spike.masters --workbook` on the export, then `spike.build` and `spike.tests`, in the
spike's own stack (runner, Bifrost, OpenRouter). Both steps exited 0. The measured results are in
[`results.real-admin-export.json`](../../spikes/cognee/out/results.real-admin-export.json); the
committed bundle and `results.json` are still the mock run's.

| Check | Mock run (above) | Admin export |
|---|---|---|
| T01 isolation | Pass | Pass: A sees B's marker 0 times, B sees A's 0; own markers 1 and 2; 0 graph nodes in B with an A document id. An unscoped search for B's marker still hits once (the caveat above stands) |
| T01 stronger (one user per project) | Pass | Pass: `DatasetNotFoundError` both ways; unscoped search 0 hits |
| T03 provenance | 22 of 22 entities, 18 of 18 relations | 20 of 20 entities, 19 of 19 relations carry a valid `source_ref`; 0 use the whole-document fallback |
| T04 master injection | 16 of 16 master nodes, 16 edges to the root | 16 of 16, 16 non-structural edges touch the root; no ILLUSTRATIVE row appears |
| T05 sizes (`tiktoken cl100k_base`, one fixture) | chunks 1,088; masters 2,730; graph 5,872; bundle 9,845 tokens | chunks 1,088; masters 2,730; graph 5,932; bundle 9,905 tokens |

Every check passes. What differs is the number of entities and relations the model extracts
(22 and 18 in the mock report, 20 and 19 here), which moves the graph by 60 tokens. The input
differs only in the key order of two `RAG Reference` cells, one of them included in the context, but
I did not separate that from the normal run-to-run variation of an LLM extraction. T02, T06 and T07
were not re-run on the export (T07 needs Bifrost stopped by hand).

The first attempt failed with a 401 "User not found" from OpenRouter. The cause was in the setup,
not the spike: the spike stack's Bifrost had a different OpenRouter key from the main stack's (the
SHA-256 prefixes of the two values differed), and the key was replaced in `spikes/cognee/.env`.

**Still not shown:** that the spike passes on a tenant's own data. This export came from the same
workbook, so it proves the Admin import/export path, not a real tenant's content.

## Limits of this spike

- Fixtures are tiny (project A is 934 characters). **Large documents (up to 15 MB), streamed writes and Cognee's chunker at scale were not tested.**
- Only one chat model and one embedding model were tried. Extraction quality was sanity-checked, not evaluated.
- Costs and latency were not measured beyond the observed ~30 s per project build for 3–4 tiny documents.
- Master data came from a workbook mock, then from an Admin export of the same workbook (diffed: no differences; the spike passes on both, see above). It has not been run on a tenant's own data.
- T06 is proven by network topology, not a packet capture.

## Recommendation

**GO.** Adopt Cognee 1.6.2 with default stores, with these conditions (recorded in ADR 0001):

1. One Cognee user **and** one dataset per project, plus an adapter guard that rejects any call without a project scope (#78).
2. Cognee gets only Bifrost credentials and sits on a network with no internet route (#39, #46, #78).
3. Classify Cognee failures in the adapter, and heartbeat from a separate task during `cognify` (#78, #80).
4. OKF bundle as prototyped, with `master_ref` sources for master-derived entities and `created_at` outside the hash (#81).
5. Ranking fallback needs richer queries and graph proximity before use (#86).
6. Resolve the master-data issues above in #21 and #59.

## Reproduce

See [`spikes/cognee/README.md`](../../spikes/cognee/README.md): `docker compose up -d --build`, then
`python -m spike.gen_fixtures`, `spike.build`, `spike.tests` inside the runner. T07 is run from the host by stopping Bifrost during `spike.t07`.
