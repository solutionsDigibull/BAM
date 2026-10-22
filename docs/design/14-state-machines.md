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

`UPLOADED → SCANNING → (REJECTED | SCAN_FAILED | PARSING) → (PARSED | PARSE_FAILED | REJECTED)` (the second REJECTED is type detection: type does not match the content, unsupported, or invalid encoding), plus `DELETED` (soft, restorable for 90 days) → `PURGED`. `SCAN_FAILED` = ClamAV unavailable or signatures older than 24 h after retries; retryable. `PARSE_FAILED` is retryable too: `retry` runs the scan and the parse again.

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

## 14.8 Transitions as code

The machines above are implemented in `server/contracts/sbam_contracts/state_machines.py` (E0.6) and exported read-only for the UI to `server/contracts/schemas/state_machines.json` by `make contracts` (drift-checked). `can_transition(machine, from_state, event, actor)` is the authoritative check; a row with no *from* state creates the entity. Actors: FDE, ADMIN, SYSTEM. A restored document goes back to `UPLOADED` and is scanned and parsed again. Edit events on locked states (APPROVED SOW, CERTIFIED/FROZEN) fail with `LOCKED`.

**Project pipeline** — terminal: CANCELLED

| From | Event | To | Actor |
|---|---|---|---|
| (create) | `create_project` | IDLE | ADMIN |
| IDLE | `start_processing` | INGESTING | FDE |
| INGESTING | `ingestion_completed` | KNOWLEDGE_BUILDING | SYSTEM |
| INGESTING | `ingestion_failed` | INGESTION_FAILED | SYSTEM |
| KNOWLEDGE_BUILDING | `knowledge_ready` | SOW_GENERATING | SYSTEM |
| KNOWLEDGE_BUILDING | `knowledge_failed` | KNOWLEDGE_FAILED | SYSTEM |
| SOW_GENERATING | `sow_generated` | SOW_REVIEW | SYSTEM |
| SOW_GENERATING | `sow_generation_failed` | SOW_GENERATION_FAILED | SYSTEM |
| SOW_REVIEW | `approve_sow` | SOW_APPROVED | FDE |
| SOW_APPROVED | `workflow_created` | WORKFLOW_CREATED | SYSTEM |
| WORKFLOW_CREATED | `start_processing` | INGESTING | FDE |
| WORKFLOW_CREATED | `start_scope_change` | SOW_REVIEW | FDE |
| INGESTION_FAILED | `retry` | INGESTING | FDE |
| KNOWLEDGE_FAILED | `retry` | KNOWLEDGE_BUILDING | FDE |
| SOW_GENERATION_FAILED | `regenerate` | SOW_GENERATING | FDE |
| IDLE | `cancel` | CANCELLED | FDE |
| INGESTING | `cancel` | CANCELLED | FDE |
| KNOWLEDGE_BUILDING | `cancel` | CANCELLED | FDE |
| SOW_GENERATING | `cancel` | CANCELLED | FDE |
| SOW_REVIEW | `cancel` | CANCELLED | FDE |
| INGESTION_FAILED | `cancel` | CANCELLED | FDE |
| KNOWLEDGE_FAILED | `cancel` | CANCELLED | FDE |
| SOW_GENERATION_FAILED | `cancel` | CANCELLED | FDE |

**Workflow** — terminal: CANCELLED

| From | Event | To | Actor |
|---|---|---|---|
| (create) | `sow_approved` | CREATED | SYSTEM |
| CREATED | `start_extraction` | ISATVON_EXTRACTING | SYSTEM |
| ISATVON_EXTRACTING | `extraction_completed` | ISATVON_REVIEW | SYSTEM |
| ISATVON_EXTRACTING | `extraction_failed` | EXTRACTION_FAILED | SYSTEM |
| EXTRACTION_FAILED | `retry` | ISATVON_EXTRACTING | FDE |
| ISATVON_REVIEW | `generate` | BUILDING | FDE |
| BUILDING | `all_sub_skills_certified` | CERTIFYING | SYSTEM |
| BUILDING | `sub_skill_needs_review` | NEEDS_FDE_REVIEW | SYSTEM |
| NEEDS_FDE_REVIEW | `retry` | BUILDING | FDE |
| CERTIFYING | `workflow_certified` | CERTIFIED | SYSTEM |
| CERTIFYING | `workflow_validation_failed` | WORKFLOW_VALIDATION_FAILED | SYSTEM |
| WORKFLOW_VALIDATION_FAILED | `retry` | BUILDING | FDE |
| CERTIFIED | `freeze` | FROZEN | SYSTEM |
| FROZEN | `start_patch_version` | BUILDING | FDE |
| CREATED | `cancel` | CANCELLED | FDE |
| ISATVON_EXTRACTING | `cancel` | CANCELLED | FDE |
| ISATVON_REVIEW | `cancel` | CANCELLED | FDE |
| BUILDING | `cancel` | CANCELLED | FDE |
| CERTIFYING | `cancel` | CANCELLED | FDE |
| EXTRACTION_FAILED | `cancel` | CANCELLED | FDE |
| NEEDS_FDE_REVIEW | `cancel` | CANCELLED | FDE |
| WORKFLOW_VALIDATION_FAILED | `cancel` | CANCELLED | FDE |

**Document version** — terminal: PURGED

| From | Event | To | Actor |
|---|---|---|---|
| (create) | `upload` | UPLOADED | FDE |
| UPLOADED | `scan_started` | SCANNING | SYSTEM |
| SCANNING | `scan_clean` | PARSING | SYSTEM |
| SCANNING | `malware_detected` | REJECTED | SYSTEM |
| SCANNING | `scan_unavailable` | SCAN_FAILED | SYSTEM |
| SCAN_FAILED | `auto_retry` | SCANNING | SYSTEM |
| SCAN_FAILED | `retry` | SCANNING | FDE |
| PARSING | `parse_completed` | PARSED | SYSTEM |
| PARSING | `parse_failed` | PARSE_FAILED | SYSTEM |
| PARSING | `type_rejected` | REJECTED | SYSTEM |
| PARSE_FAILED | `retry` | SCANNING | FDE |
| UPLOADED | `delete` | DELETED | FDE |
| PARSED | `delete` | DELETED | FDE |
| REJECTED | `delete` | DELETED | FDE |
| SCAN_FAILED | `delete` | DELETED | FDE |
| PARSE_FAILED | `delete` | DELETED | FDE |
| DELETED | `restore` | UPLOADED | FDE |
| DELETED | `purge` | PURGED | SYSTEM |

**SOW version** — terminal: SUPERSEDED

| From | Event | To | Actor |
|---|---|---|---|
| (create) | `generated` | GENERATED | SYSTEM |
| (create) | `generation_failed` | GENERATION_FAILED | SYSTEM |
| (create) | `new_version` | IN_REVIEW | FDE |
| GENERATION_FAILED | `regenerate` | GENERATED | FDE |
| GENERATED | `open_review` | IN_REVIEW | SYSTEM |
| IN_REVIEW | `edit` | IN_REVIEW | FDE |
| IN_REVIEW | `approve` | APPROVED | FDE |
| APPROVED | `superseded` | SUPERSEDED | SYSTEM |

**Sub-Skill** — terminal: SUPERSEDED

| From | Event | To | Actor |
|---|---|---|---|
| (create) | `extracted` | DRAFT | SYSTEM |
| DRAFT | `validation_passed` | VALID | SYSTEM |
| DRAFT | `validation_failed` | INVALID | SYSTEM |
| VALID | `edit` | DRAFT | FDE |
| INVALID | `edit` | DRAFT | FDE |
| VALID | `generate` | BUILDING | FDE |
| BUILDING | `tests_started` | TESTING | SYSTEM |
| BUILDING | `gate_failed` | CORRECTING | SYSTEM |
| BUILDING | `dependency_missing` | DEPENDENCY_FAILED | SYSTEM |
| BUILDING | `template_error` | BUILD_ERROR | SYSTEM |
| CORRECTING | `correction_applied` | BUILDING | SYSTEM |
| CORRECTING | `attempts_exhausted` | NEEDS_FDE_REVIEW | SYSTEM |
| NEEDS_FDE_REVIEW | `retry` | BUILDING | FDE |
| TESTING | `tests_passed` | CERTIFIED | SYSTEM |
| TESTING | `tests_failed` | CORRECTING | SYSTEM |
| TESTING | `sandbox_unavailable` | TESTING_BLOCKED | SYSTEM |
| TESTING_BLOCKED | `auto_retry` | TESTING | SYSTEM |
| TESTING_BLOCKED | `retry` | TESTING | FDE |
| DEPENDENCY_FAILED | `correction_applied` | BUILDING | SYSTEM |
| DEPENDENCY_FAILED | `retry` | BUILDING | FDE |
| BUILD_ERROR | `retry` | BUILDING | ADMIN |
| CERTIFIED | `freeze` | FROZEN | SYSTEM |
| VALID | `scope_changed` | INVALIDATED | SYSTEM |
| INVALID | `scope_changed` | INVALIDATED | SYSTEM |
| CERTIFIED | `scope_changed` | INVALIDATED | SYSTEM |
| FROZEN | `scope_changed` | INVALIDATED | SYSTEM |
| INVALIDATED | `re_extracted` | DRAFT | SYSTEM |
| FROZEN | `start_patch_version` | DRAFT | FDE |
| FROZEN | `superseded` | SUPERSEDED | SYSTEM |

**Agent (Macro Skill)** — terminal: none

| From | Event | To | Actor |
|---|---|---|---|
| (create) | `extracted` | PENDING | SYSTEM |
| PENDING | `build_started` | BUILDING | SYSTEM |
| BUILDING | `all_sub_skills_certified` | CERTIFIED | SYSTEM |
| CERTIFIED | `sub_skill_invalidated` | BUILDING | SYSTEM |
| CERTIFIED | `sub_skill_patched` | BUILDING | SYSTEM |
| CERTIFIED | `freeze` | FROZEN | SYSTEM |
| FROZEN | `start_patch_version` | BUILDING | SYSTEM |

---
[← §13](13-temporal-design.md)   [Design index](README.md)   [§15 →](15-test-data-promptfoo-and-sandbox-design.md)
