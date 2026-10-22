# 0019 — SOW editor format (SBAM TipTap shape) and how a regeneration runs

- Status: Accepted
- Date: 2026-10-22
- Related: issues #86 (E6.2), #87 (E6.3), #89 (E6.5); [TD §8.7](../design/08-api-design.md), [TD §10.6](../design/10-isatvon-1-4-contract.md), [0009](0009-sow-scope-change-and-grid.md)

## Context

The FDE edits the SOW in TipTap and saves `content_tiptap`. But the version's hash, the template
check (nine sections), the citation check and the section diff all work on the structured
`SowDocument`. The design said neither how one becomes the other, nor how "Regenerate" runs while
ProjectPipeline waits for the approval (or after it ended in SOW_GENERATION_FAILED).

## Decision

- **SBAM defines the TipTap shape, and the server converts it both ways** (`sbam_api/sow/tiptap.py`;
  the shape is documented in `sbam_contracts/sow_api.py`). Each of the nine sections is a
  `sowSection{section_id}` node with its fixed heading; fields are `sowField{field}` nodes; modules
  and features are `sowItem{id, module_id}` nodes whose heading is their name; citations are
  `citation{okf_ref_id, document_version_id, location}` atoms in a `sowCitations` node. Ids,
  module ids and citations are attributes, so typing cannot change them.
- The conversion is driven by the `SowDocument` fields, so a contract change reaches the editor
  without converter code. On save, a missing, renamed, duplicated or reordered section is 422 with
  `sections.<id>`, and schema errors keep their field path (`features.0.bound.max_attempts`).
- `structured` holds plain text. Marks (bold, italic, ...) are kept in `content_tiptap` only, so a
  formatting-only change has the same sha256 and is a no-op save.
- Citations in a save must name a chunk or entity of the version's OKF bundle, with its document
  version. FDE-authored citations need a contract change (`Citation` has no such kind) and are left
  for later.
- **Regenerate** starts a new version from the latest READY knowledge version:
  - in SOW_REVIEW, the `SowRegenerate` workflow (`sow-<project>`) runs `generate_sow`; the waiting
    ProjectPipeline is untouched and the new version becomes current (a failed one is recorded
    without replacing it);
  - in SOW_GENERATION_FAILED, `regenerate` moves the project to SOW_GENERATING and ProjectPipeline
    starts again at its SOW stage (`ResumeState.refs`), so the approval is waited for as usual.
- The FDE's hint (≤ 2000 characters, control characters removed) travels in the workflow input,
  as the cancel `reason` does: it is the FDE's own short text, never document content.

## Consequences

- The client (#89) builds the editor from these node types and never edits the generated contract.
- Concurrency uses the `sows` row's `row_version` as ETag plus `base_version`; a stale one is 409
  SOW_VERSION_CONFLICT (TD §8.1's exception for SOW saves).
- `sow_versions` content is immutable in the database (trigger, migration 0020); only lifecycle
  columns (state, approval) change.
