> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 4 — Project Workspace and Document Ingestion

| Epic | Milestone | Stories |
|---|---|---|
| [#5](https://github.com/solutionsDigibull/BAM/issues/5) `E4` | M1 Admin + Ingestion | 11 |


**Goal:** Let FDEs open their assigned project and upload project information safely, with every file scanned, parsed, versioned and recoverable, and start the project pipeline (SBAM creates the Workflow later, from the approved SOW).

**Owner:** Dev B (pipeline) + Dev D (UI) · **Indicative effort:** 1.5 weeks · **Complexity:** L · **Depends on:** Phase 3
**Design references:** [TD §3](../design/03-end-to-end-user-flow.md), [TD §4](../design/04-system-flow.md) (T1–T6), [TD §7](../design/07-database-design.md), [TD §8.4–8.5](../design/08-api-design.md), [TD §9.3](../design/09-frontend-design.md).

### Project workspace

- [ ] FDE project workspace: assigned projects list, project overview with pipeline timeline; no Workflow creation by the FDE.
- [ ] `ProjectPipeline` Temporal workflow started when the FDE clicks **Start processing**; project pipeline state timeline ([TD §14.1](../design/14-state-machines.md)) and dashboard endpoint.

### Upload

- [ ] Uppy (Dashboard + tus) in the FDE portal; tusd service writing to the storage volume.
- [ ] Limits enforced server-side: ≤15 MB per file, ≤20 active files per project (configurable).
- [ ] Type allowlist from the Filetype master (MVP set in [TD §5.2](../design/05-component-architecture.md): tier-1 text/structured, tier-2 office/PDF/CSV/archives, scanned PDF/images via OCR); magic-byte validation; SHA-256 duplicate detection.
- [ ] ClamAV (clamd) scan before acceptance; infected files quarantined and reported.

### Parsing

- [ ] `DocumentIngestion` Temporal workflow: scan → detect → parse → normalize (Markdown + JSON sidecar with page/sheet/cell provenance).
- [ ] Loaders per Filetype entry: PDF text, DOCX, XLSX/CSV (openpyxl/csv), MD/TXT, EML/MSG (attachments as child documents), JSON/XML/YAML/OpenAPI/Swagger/FastMCP spec (structural pre-parse), source code (.py via `ast`, others as text), ZIP/TAR.GZ (members ingested individually).
- [ ] Basic OCR: pages with no text layer and images are sent to a vision model via Bifrost alias `sbam-ocr`. Result marked `ocr=llm`.
- [ ] Parse report per document (pages, sheets, warnings, OCR pages).

### Deletion and recovery

- [ ] Soft delete with `deleted_at`; recovery within 90 days; Temporal schedule purges after 90 days (file + derivatives + Cognee data reference).
- [ ] Deleting a document after SOW approval flags the Workflow "context changed" (it never silently alters approved artifacts).

### Frontend (FDE portal)

- [ ] Project workspace (assigned projects, overview timeline, read-only list of SBAM-generated Workflows — no create action); Upload screen; Processing status screen (scan, parse, OCR, errors, retry); Recycle bin (restore within 90 days).

### Tests

- [ ] EICAR test file is rejected; oversize and over-count are rejected; spoofed extension is rejected.
- [ ] Each supported type parses its fixture; scanned PDF goes through LLM OCR.
- [ ] Delete → restore → purge lifecycle with a time-skipping Temporal test.

### Exit Criteria

- [ ] An FDE can upload up to 20 files of supported types and see each reach `PARSED` or a clear failure reason.
- [ ] No unscanned or infected file is ever parsed or sent to an LLM.
- [ ] Deleted documents are restorable for 90 days and purged afterwards.

## Design pages to read

- [TD §3. End-to-end user flow](../design/03-end-to-end-user-flow.md)
- [TD §4. System flow (transition detail)](../design/04-system-flow.md)
- [TD §5. Component architecture](../design/05-component-architecture.md)
- [TD §7. Database design](../design/07-database-design.md)
- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §14. State machines](../design/14-state-machines.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#69](https://github.com/solutionsDigibull/BAM/issues/69) | `E4.1` | Project workspace API + ProjectPipeline start + dashboard | C Build | M | P0 | M1 | [#54](https://github.com/solutionsDigibull/BAM/issues/54), [#40](https://github.com/solutionsDigibull/BAM/issues/40), [#41](https://github.com/solutionsDigibull/BAM/issues/41) |
| [#61](https://github.com/solutionsDigibull/BAM/issues/61) | `E4.2` | tusd service + hooks + upload token + limits | B Knowledge/LLM | M | P0 | M1 | [#37](https://github.com/solutionsDigibull/BAM/issues/37), [#54](https://github.com/solutionsDigibull/BAM/issues/54) |
| [#62](https://github.com/solutionsDigibull/BAM/issues/62) | `E4.3` | ClamAV service + scan activity + quarantine | B Knowledge/LLM | S | P0 | M1 | [#61](https://github.com/solutionsDigibull/BAM/issues/61), [#40](https://github.com/solutionsDigibull/BAM/issues/40) |
| [#70](https://github.com/solutionsDigibull/BAM/issues/70) | `E4.4` | Type detection (magic bytes) vs Filetype config + SHA-256 dedupe | B Knowledge/LLM | S | P0 | M1 | [#61](https://github.com/solutionsDigibull/BAM/issues/61), [#60](https://github.com/solutionsDigibull/BAM/issues/60) |
| [#71](https://github.com/solutionsDigibull/BAM/issues/71) | `E4.5` | Loaders: PDF (text), DOCX, XLSX, CSV, MD, TXT | B Knowledge/LLM | M | P0 | M1 | [#62](https://github.com/solutionsDigibull/BAM/issues/62), [#70](https://github.com/solutionsDigibull/BAM/issues/70) |
| [#72](https://github.com/solutionsDigibull/BAM/issues/72) | `E4.6` | Loaders: EML/MSG (+attachments), JSON/XML/YAML/OpenAPI/Swagger/FastMCP spec, source code | B Knowledge/LLM | M | P0 | M1 | [#71](https://github.com/solutionsDigibull/BAM/issues/71) |
| [#73](https://github.com/solutionsDigibull/BAM/issues/73) | `E4.7` | Archives (.zip, .tar.gz) decompressed into member documents | B Knowledge/LLM | S | P1 | M1 | [#72](https://github.com/solutionsDigibull/BAM/issues/72) |
| [#74](https://github.com/solutionsDigibull/BAM/issues/74) | `E4.8` | Basic OCR via LLM vision for scanned PDF and images | B Knowledge/LLM | M | P0 | M1 | [#71](https://github.com/solutionsDigibull/BAM/issues/71), [#46](https://github.com/solutionsDigibull/BAM/issues/46) |
| [#75](https://github.com/solutionsDigibull/BAM/issues/75) | `E4.9` | Soft delete, restore, 90-day purge schedule | B Knowledge/LLM | S | P1 | M1 | [#61](https://github.com/solutionsDigibull/BAM/issues/61) |
| [#76](https://github.com/solutionsDigibull/BAM/issues/76) | `E4.10` | FDE project workspace + Upload screen (Uppy) | D Frontend | M | P0 | M1 | [#51](https://github.com/solutionsDigibull/BAM/issues/51), [#69](https://github.com/solutionsDigibull/BAM/issues/69), [#61](https://github.com/solutionsDigibull/BAM/issues/61) |
| [#77](https://github.com/solutionsDigibull/BAM/issues/77) | `E4.11` | Processing status + recycle bin screen | D Frontend | S | P0 | M1 | [#76](https://github.com/solutionsDigibull/BAM/issues/76), [#75](https://github.com/solutionsDigibull/BAM/issues/75) |

---
[← Phase 3](phase-03-admin-portal-projects-and-masters.md)   [Phases index](README.md)   [Phase 5 →](phase-05-knowledge-construction.md)
