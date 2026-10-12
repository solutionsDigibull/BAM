# 0007 — Document ingestion

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-07, C-08, C-09, C-10, C-11, C-53, [TD §4](../design/04-system-flow.md), [TD §5](../design/05-component-architecture.md)

## Context

FDEs upload the client's documents. Limits, accepted types, scanning, OCR and deletion all need fixed rules before the ingestion pipeline is built.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-07 | Upload limits | ≤15 MB per file, ≤20 files per project (configurable) | QA D3 |
| C-08 | File types | PDF, DOCX, XLSX/CSV, MD, TXT, EML, plus other easily extractable text formats | QA D1 |
| C-09 | Malware / security scan | ClamAV for uploads and generated artifacts | QA D4, J2 |
| C-10 | OCR | MVP: basic extraction plus LLM vision via Bifrost/OpenRouter for scanned pages; better OCR later | QA D5 |
| C-11 | Deletion | Soft delete with 90-day recovery, then purge | QA D6 |
| C-53 | Ingestion types | The 43 frozen Filetype entries drive accepted types and loaders (loader flag in platform config, not in the tab). MVP = tier-1 text/structured types, tier-2 office/PDF/CSV/archives, basic LLM OCR for scanned PDF and images; CAD/PCB/media/Google/Web = future ([TD §5.2](../design/05-component-architecture.md)). The Filetype table is private: FDEs cannot edit it. | MSC, QA D1 |

## Consequences

- Uploads go through tusd into storage (`StorageBackend`, #37), are scanned by ClamAV, and are parsed by type in Temporal activities.
- The frozen Filetype table drives which files are accepted; soft-deleted documents can be recovered for 90 days, then they are purged.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §4](../design/04-system-flow.md), [TD §5](../design/05-component-architecture.md)
