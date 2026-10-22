> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §5. Component architecture


## 5.1 Overview

| Component                       | Responsibility                                            | Inputs → Outputs                      | Dependencies                                                      | Failure modes                      | Owner   |
| ------------------------------- | --------------------------------------------------------- | -------------------------------------- | ----------------------------------------------------------------- | ---------------------------------- | ------- |
| `sbam-web` (Next.js + Refine) | Admin and FDE portals                                     | REST, tus → UI                        | API, Keycloak, tusd                                               | Token expiry, API errors → states | D       |
| `sbam-api` (FastAPI)          | Authoritative rules, authz, persistence, Temporal signals | HTTP → JSON, signals                  | Postgres, storage, Temporal, Keycloak                             | 409/422/503                        | A, B, C |
| `tusd`                        | Resumable uploads                                         | tus → files on storage volume + hooks | API hooks                                                         | Resume                             | A       |
| Workers (Temporal)              | All long-running work                                     | Activities → evidence                 | Temporal, Postgres, storage, Bifrost, Cognee, ClamAV, CubeSandbox | Retries, failure states            | B, C    |
| Keycloak                        | Identity, tenants, roles                                  | OIDC                                   | Postgres                                                          | Login unavailable                  | A       |
| Temporal                        | Durable orchestration                                     | —                                     | Postgres                                                          | Workers resume                     | C       |
| Bifrost                         | LLM gateway → OpenRouter                                 | OpenAI-compatible API                  | OpenRouter                                                        | Failover between models            | B       |
| Cognee                          | Knowledge graph                                           | Documents → graph                     | Default stores (volume), Bifrost                                  | Rebuildable                        | B       |
| ClamAV (clamd)                  | Malware scan                                              | Streams → verdict                     | Signature updates (freshclam)                                     | Stale signatures → alert          | A       |
| CubeSandbox host                | MicroVM execution                                         | Bundle + dataset → outputs            | Linux/KVM, Netbird                                                | Infra errors → retry              | A, C    |
| Promptfoo                       | Assertions                                                | Outputs + suite → results             | Node runtime                                                      | Suite invalid                      | C       |
| Observability                   | OTEL collector, Grafana LGTM, Langfuse, PostHog           | Telemetry                              | —                                                                | Non-blocking                       | A       |

## 5.2 Ingestion pipeline (Dev B)

- tusd `pre-create` hook → API checks token, project, file count, size and extension. `post-finish` hook → API registers the version and signals the `DocumentIngestion` Temporal workflow.
- **The Filetype master drives ingestion.** The 43 frozen Filetype entries (format, RAG tier, purpose, RAG ingestion method) each map to a loader. A type is accepted only if SBAM has an `mvp_enabled` loader for it. The flag lives in platform configuration, not in the frozen tab. MVP loaders:

| Filetype master entries                                                                                                 | RAG tier | MVP loader                                                                                                                              | MVP                            |
| ----------------------------------------------------------------------------------------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| `.md`, `.txt`                                                                                                       | 1        | Cognee TextLoader (headings/structure preserved)                                                                                        | ✔                             |
| `.eml`, `.msg`                                                                                                      | 1        | Email parser: headers, body, attachments as child documents (also scanned)                                                              | ✔                             |
| `.json`, `.xml`, `.yaml`, OpenAPI, Swagger, FastMCP Spec                                                          | 1        | Structural pre-parse → text + key paths, then Cognee                                                                                   | ✔                             |
| `.py`, `.sql`, `.js`, `.go`, `.cpp`                                                                           | 1        | Source as text with symbol extraction (Python`ast`; others plain text in MVP; tree-sitter later)                                      | ✔ (basic)                     |
| `.csv`, `.xlsx`                                                                                                     | 2        | `csv` / `openpyxl` with sheet/cell provenance                                                                                       | ✔                             |
| `.docx`                                                                                                               | 2        | `python-docx` / Cognee UnstructuredLoader                                                                                             | ✔                             |
| `.pdf` (text-based)                                                                                                   | 2        | `pypdf`/`pdfplumber` (text + tables)                                                                                                | ✔                             |
| `.zip`, `.tar.gz`                                                                                                   | 2        | Decompress at the gateway; each member is ingested as its own document (counts toward the 20-file limit; size limit applies to members) | ✔ (P1)                        |
| `.pdf` (scanned), `.png`, `.jpg`, `.jpeg`, `.tiff`                                                            | 3        | Rasterize → LLM vision OCR via Bifrost`sbam-ocr`                                                                                     | ✔ (basic OCR)                 |
| `.parquet`, Google Docs, Google Sheets, Web URL / HTML                                                                | 2/1      | —                                                                                                                                      | Future                         |
| `.dwg`, `.dxf`, `.step`, `.stp`, `.iges`, Gerber RS-274X/X2/X3, ODB++, IPC-2581, `.mp3`, `.wav`, `.mp4` | 3        | —                                                                                                                                      | Future (CAD/PCB/media parsers) |

- Normalized derivative: `doc.md` (Markdown) + `doc.json` (blocks with `page`/`sheet`/`cell`/`line` provenance, tables as arrays).
- OCR: pages without text or image files are rendered to PNG and sent to the `sbam-ocr` vision alias, with an extraction prompt to return Markdown per page.

## 5.3 OKF Bundle (Dev B)

Requirement C-13: the bundle must preserve all ingested information. Structure *(proposed; align with the OKF specification during the Phase 0 spike)*:

```text
okf-bundle/
  bundle.json         id, project_id, workflow_id, knowledge_version, created_at, okf_version,
                      sources[] (document_version_id, sha256, title, type), master_snapshot_ref, sha256 of all parts
  entities.jsonl      id, type, name, attributes, source_refs[] (document_version_id + location)
  relations.jsonl     id, type, from, to, attributes, source_refs[]
  chunks.jsonl        id, document_version_id, location, text (full normalized content, no truncation)
  masters.jsonl       master entries used as context (type, key, version, payload)
  coverage.json       per document: chunks, entities, status
```

- `chunks.jsonl` holds the **full** normalized text of every document, so nothing is lost even if graph extraction misses facts.
- The SOW stage uses `bundle.json` + entities/relations + chunks. If the total exceeds the model context, chunks are ranked per SOW section (graph proximity + embedding similarity) and the selection is recorded in `llm_calls.context_manifest`.

## 5.4 LLM layer (Dev B)

| Alias              | Used for                       | Output type                                       | Notes                  |
| ------------------ | ------------------------------ | ------------------------------------------------- | ---------------------- |
| `sbam-ocr`       | OCR of scanned pages/images    | Markdown per page                                 | Vision-capable model   |
| `sbam-sow`       | One-shot SOW                   | `SowDocument`                                   | Large context model    |
| `sbam-isatvon`   | Macro and Sub-Skill extraction | `list[MacroSkill]`, `list[SubSkill]`          | Strict schema          |
| `sbam-codegen`   | Function bodies                | `CodeBodies`                                    | Code-specialised model |
| `sbam-testgen`   | Test scenarios + data          | `TestSuiteSpec`                                 | —                     |
| `sbam-corrector` | Correction                     | `CorrectionPatch` (ISATVON patch and/or bodies) | Receives findings only |
| `sbam-knowledge` | Cognee graph extraction        | Cognee's `KnowledgeGraph`                       | Cognee only (worker-knowledge), not the SBAM client |
| `sbam-embed`     | Cognee embeddings              | 1536-dim vectors (`/v1/embeddings`)             | Cognee only; fallback is the same model (vector size) |

Model choice per alias is configured in Bifrost (OpenRouter models, "depending on situation", C-41), with a fallback model per alias. Every call writes `llm_calls` and a Langfuse trace (prompt version, model, tokens, latency, cost). Prompts are versioned files in `services/workers/sbam_workers/llm/prompts/`.

---
[← §4](04-system-flow.md)   [Design index](README.md)   [§6 →](06-repository-structure.md)
