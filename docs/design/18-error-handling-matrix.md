> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §18. Error handling matrix


| #   | Failure                                                                       | Detection              | Automatic recovery                   | Resulting state                            | FDE/Admin action                        |
| --- | ----------------------------------------------------------------------------- | ---------------------- | ------------------------------------ | ------------------------------------------ | --------------------------------------- |
| E1  | Upload interrupted                                                            | tus                    | Resume                               | —                                         | None                                    |
| E2  | File > 15 MB / > 20 files / type not allowed                                  | API / tusd hook        | —                                   | Rejected (413/409/415)                     | Reduce / convert                        |
| E3  | Malware detected                                                              | ClamAV                 | Quarantine                           | `REJECTED`                               | Provide clean file                      |
| E4  | ClamAV unavailable                                                            | Scan activity          | Retry ×5                            | `SCAN_FAILED`                            | Retry later (alerted)                   |
| E5  | Parse failure                                                                 | Parser                 | Retry ×3                            | `PARSE_FAILED`                           | Re-upload / other format                |
| E6  | OCR unreadable page                                                           | OCR activity           | Retry ×3                            | Page marked unreadable                     | Provide better scan                     |
| E7  | Cognee failure                                                                | Knowledge activity     | Retry ×5                            | `KNOWLEDGE_FAILED`                       | Rebuild                                 |
| E8  | OKF Bundle incomplete                                                         | Coverage check         | —                                   | Warning or failure                         | Rebuild / remove document               |
| E9  | OpenRouter / provider outage                                                  | Bifrost                | Failover to fallback model + retries | —                                         | None (alerted)                          |
| E10 | SOW schema-invalid                                                            | Pydantic AI            | Re-ask ≤3                           | `SOW_GENERATION_FAILED`                  | Regenerate with hint                    |
| E11 | Concurrent SOW edit                                                           | ETag                   | —                                   | 409                                        | Reload and merge                        |
| E12 | Approve stale version                                                         | Hash check             | —                                   | 409                                        | Approve latest                          |
| E13 | Extraction invalid/empty                                                      | Validation             | Re-ask                               | `EXTRACTION_FAILED` / invalid Sub-Skills | Regenerate macro/sub with hint, or edit |
| E14 | Invalid grid edit                                                             | Pydantic               | —                                   | 422, Sub-Skill`INVALID`                  | Fix cell                                |
| E15 | Cyclic dependencies                                                           | Validation             | —                                   | `INVALID`                                | Fix`depends_on`                       |
| E16 | Dependency not in registry                                                    | Dependency check       | Correction may change T              | `DEPENDENCY_FAILED` → correction        | Edit T / ask platform to add library    |
| E17 | Template render error                                                         | Skeleton activity      | —                                   | `BUILD_ERROR` (platform bug)             | Platform fixes template                 |
| E18 | Syntax error / skeleton altered / non-conforming signature / forbidden import | AST gate               | Correction ×2                       | `NEEDS_FDE_REVIEW` if unresolved         | Review findings, edit ISATVON, Retry    |
| E19 | Ruff/Black/MyPy/Bandit/ClamAV findings                                        | Static gate            | Correction ×2                       | same                                       | same                                    |
| E20 | Import fails in runtime image                                                 | Import resolution      | Correction ×2                       | same                                       | Adjust T / platform adds library        |
| E21 | Test data invalid                                                             | Testgen validation     | Re-ask                               | —                                         | Edit cases                              |
| E22 | Assertion / runtime failure in sandbox                                        | Promptfoo / sandbox    | Correction ×2                       | same                                       | Review cases/ISATVON, Retry             |
| E23 | Sandbox host unavailable                                                      | Adapter                | Retry ×3                            | `TESTING_BLOCKED`                        | Retry when healthy (alerted)            |
| E24 | Egress to non-allowlisted host                                                | Sandbox network policy | —                                   | `EGRESS_BLOCKED` → correction           | Add allowlist entry if legitimate       |
| E25 | Workflow validation fails (DAG / interface / smoke)                           | Certify activity       | —                                   | `WORKFLOW_VALIDATION_FAILED`             | Fix affected Sub-Skills, Retry          |
| E26 | Hash mismatch at freeze                                                       | Freeze activity        | Abort, alert                         | stays`CERTIFIED`                         | Platform investigates, re-run           |
| E27 | Modify frozen record                                                          | Trigger / API 423      | —                                   | Unchanged, security log                    | Create a new version                    |
| E28 | Temporal unavailable                                                          | API                    | —                                   | 503                                        | Retry later                             |
| E29 | Worker crash                                                                  | Heartbeat timeout      | Activity retry (idempotent)          | —                                         | None                                    |
| E30 | Disk full                                                                     | Storage metrics        | —                                   | Writes fail (503)                          | Operator expands volume                 |
| E31 | Stored code bundle no longer matches what `assemble` recorded                 | AST gate (hash check)  | — (non-retryable)                   | `BUILD_ERROR`                              | Platform investigates, then Retry       |
| E32 | A code gate is given inputs that are not the attempt's (attempt, template, manifest or bundle) | AST gate               | — (non-retryable)                   | `BUILD_ERROR`                              | Platform fixes the wiring, then Retry   |
| E33 | A static tool or clamd gives no verdict after its retries (timeout, crash, unreachable, signatures older than 24 h) | Static gate           | 2 attempts (never a correction)     | `BUILD_ERROR`                              | Operator fixes the tool or clamd, then Retry |

Rules: failures never advance state; only transient errors are retried automatically; evidence of failed attempts is kept; every failure produces an in-app notification with the reason and the next action.

---
[← §17](17-observability-and-notifications.md)   [Design index](README.md)   [§19 →](19-testing-strategy.md)
