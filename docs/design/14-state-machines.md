> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §14. State machines


## 14.1 Project pipeline and Workflow

**(a) Project pipeline** (driven by the FDE, executed by `ProjectPipeline`):

```text
IDLE → INGESTING → KNOWLEDGE_BUILDING → SOW_GENERATING → SOW_REVIEW → SOW_APPROVED → WORKFLOW_CREATED
 failure states: INGESTION_FAILED · KNOWLEDGE_FAILED · SOW_GENERATION_FAILED · CANCELLED
 scope change: new SOW version → SOW_REVIEW → SOW_APPROVED → new Workflow version (next MAJOR)
```

| State | Entered when | Exits via | Actor |
|---|---|---|---|
| IDLE | Project created by Admin | FDE uploads + Start processing → INGESTING | FDE |
| INGESTING | Processing started | all settled → KNOWLEDGE_BUILDING / INGESTION_FAILED | System |
| KNOWLEDGE_BUILDING | Ingestion done | READY → SOW_GENERATING / KNOWLEDGE_FAILED | System |
| SOW_GENERATING | Knowledge ready | SOW v1 → SOW_REVIEW / SOW_GENERATION_FAILED | System |
| SOW_REVIEW | SOW generated or new version | FDE approves → SOW_APPROVED | FDE |
| SOW_APPROVED → WORKFLOW_CREATED | Approval signal | SBAM creates the Workflow for that SOW version | System |

**(b) Workflow** (created by SBAM; never by the FDE):

```text
CREATED → ISATVON_EXTRACTING → ISATVON_REVIEW → BUILDING → CERTIFYING → CERTIFIED → FROZEN
 failure states: EXTRACTION_FAILED · NEEDS_FDE_REVIEW (any Sub-Skill) · WORKFLOW_VALIDATION_FAILED · CANCELLED
 after FROZEN: Sub-Skill patch → BUILDING (next PATCH)   |   new approved SOW version → new Workflow version (next MAJOR)
```

| State | Entered when | Exits via | Actor |
|---|---|---|---|
| CREATED → ISATVON_EXTRACTING | SBAM created the Workflow after SOW approval | Extraction done → ISATVON_REVIEW / EXTRACTION_FAILED | System |
| ISATVON_REVIEW | Breakdown ready | FDE clicks Generate (all valid) → BUILDING | FDE |
| BUILDING | Generate | all Sub-Skills certified → CERTIFYING; any needs review → NEEDS_FDE_REVIEW (others continue) | System |
| CERTIFYING | All Sub-Skills certified | Macros + Workflow certified → CERTIFIED / WORKFLOW_VALIDATION_FAILED | System |
| CERTIFIED → FROZEN | Certified | Freeze + version completed | System |

## 14.2 Document version

`UPLOADED → SCANNING → (REJECTED | SCAN_FAILED | PARSING) → (PARSED | PARSE_FAILED)`, plus `DELETED` (soft, restorable for 90 days) → `PURGED`. `SCAN_FAILED` = ClamAV unavailable or signatures older than 24 h after retries; retryable.

## 14.3 SOW version

`GENERATED → IN_REVIEW → APPROVED (locked)`. On scope change, a new version goes `IN_REVIEW` → `APPROVED`, and the previous approved version becomes `SUPERSEDED`. A generation failure ends in `GENERATION_FAILED` → regenerate → `GENERATED`.

## 14.4 Sub-Skill

```text
DRAFT → (VALID | INVALID) ──Generate──► BUILDING
BUILDING: MANIFEST → CODEGEN → AST → STATIC → TESTING (loop harness + Promptfoo in CubeSandbox)
   any gate fails → CORRECTING (attempt 1..2) → back to failing stage
   attempts exhausted → NEEDS_FDE_REVIEW ──FDE fix + Retry──► BUILDING
   sandbox infrastructure down → TESTING_BLOCKED ──auto retry / Retry──► TESTING
   library missing from runtime registry → DEPENDENCY_FAILED ──correction / FDE edit──► BUILDING
   template render error (platform defect) → BUILD_ERROR ──platform fix + Retry──► BUILDING
TESTING passes → CERTIFIED (manifest governance.lifecycle.state = APPROVED) ──Workflow freeze──► FROZEN
scope change touching this Sub-Skill → INVALIDATED → DRAFT (re-extracted) → …
unchanged on new version → keeps CERTIFIED (reused)
```

Mapping to ISATVON/manifest status values: SBAM `DRAFT`/`VALID`/`INVALID` → `skill_metadata.status = DRAFT`; `BUILDING`/`CORRECTING`/`TESTING` → `VALIDATING`; `CERTIFIED`/`FROZEN` → `APPROVED`; superseded → `SUPERSEDED`.

## 14.5 Macro Skill

`PENDING → BUILDING → CERTIFIED` when every Sub-Skill is CERTIFIED and the intra-macro input/output links resolve. It returns to `BUILDING` if any Sub-Skill is invalidated or patched, and becomes `FROZEN` with the Workflow.

## 14.6 Workflow certification checks

1. Every Macro Skill is CERTIFIED.
2. DAG complete: every required input of every skill is provided by a declared source (S) or an upstream skill output; no cycles.
3. Interface compatibility: upstream `output_schema` fields satisfy downstream `input_schema` types.
4. Workflow `schema.sql` applies cleanly and contains every table in every skill's `v_variables.tables`.
5. Workflow smoke run *(proposed)*: run the skill chain in DAG order through the loop harness on synthetic data.
6. Workflow manifest assembled, hashed and schema-validated.

## 14.7 Transition rules

All transitions go through `sbam.workflows.state_machine` in a DB transaction (row_version check + audit row + notification). Activities call the same service; the UI never sets states directly.

---
[← §13](13-temporal-design.md)   [Design index](README.md)   [§15 →](15-test-data-promptfoo-and-sandbox-design.md)
