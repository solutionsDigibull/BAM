> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §19. Testing strategy


## 19.1 Test types

| Type            | Scope                                                                                           | Tooling                                      |
| --------------- | ----------------------------------------------------------------------------------------------- | -------------------------------------------- |
| Unit            | Services, validators, state machines, hashing, templates, AST gate                              | pytest, hypothesis                           |
| API             | Every endpoint × role × tenant; problem+json; 409/412/423                                     | pytest + httpx, schemathesis                 |
| Contract        | Pydantic ↔ JSON Schema ↔ TS drift; ISATVON/manifest fixtures                                  | snapshot tests                               |
| Temporal        | Every path with mocked activities, time-skipping, replay                                        | `temporalio.testing`                       |
| Integration     | Postgres, storage, tusd, ClamAV, Cognee, Bifrost (mock model), Promptfoo, CubeSandbox (staging) | pytest `-m integration` against the running Compose `core` stack (§19.3) |
| LLM golden sets | OCR, SOW, ISATVON, codegen, testgen, correction; regression on prompt/model change              | Promptfoo + Langfuse datasets                |
| Frontend        | Components (grid states, editors, explorer), access control                                     | Vitest + Testing Library                     |
| E2E             | Full journeys                                                                                   | Playwright                                   |
| Security        | IDOR, tokens, injection, tamper, egress, scans                                                  | pytest suites, ZAP baseline, Trivy, gitleaks |
| Performance     | 10 users, 4 parallel builds, 20 × 15 MB ingestion, 500-row grid                                | k6*(proposed)*                             |

## 19.3 Where tests get their services

There is **one** environment for development, tests and CI: the Docker Compose `core` stack (TD §20).
There is no separate test profile and no testcontainers.

| Kind | Needs services? | Marker | Command | Gets services from |
|---|---|---|---|---|
| Unit, API, contract | no | none | `make test` | fakes injected through the app factory (e.g. `create_app(settings, checks)`), in-memory stores, `httpx` mock transport |
| Temporal workflow | no | none | `make test` | `temporalio.testing.WorkflowEnvironment` (time-skipping), mocked activities |
| Integration | **yes** | `@pytest.mark.integration` | `make test-int` (runs `make up` first if needed) | the running Compose stack, reached through the published `127.0.0.1` ports from `compose/.env` |
| E2E | yes | Playwright | `make e2e` | the running Compose stack (web + api) |

Rules:

- `make test` never needs Docker and is safe to run anywhere. Integration tests are skipped automatically when
  the stack isn't up, so the skip is visible and they never fail with a confusing connection error.
- Integration tests create their own data under a unique tenant / prefix and clean up. They never assume an
  empty database and never run `make reset`.
- "Dependency down" tests (e.g. *Temporal stopped*) stop and restart the one container they need with
  `docker compose stop <svc>` / `start <svc>`, and wait for it to report healthy before finishing.
- CI runs `make up` followed by `make test-int` as the `compose-smoke` job (TD §20.4).

## 19.2 E2E scenarios

| ID  | Scenario                                                                                                                          | Expected                                                                                                                                                                                                                                     |
| --- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E01 | Admin and FDE logins                                                                                                              | Each lands in its own portal; the wrong portal is refused                                                                                                                                                                                    |
| E02 | Cross-tenant/project access                                                                                                       | 404; audit entry                                                                                                                                                                                                                             |
| E03 | Admin project + masters                                                                                                           | Consolidated masters workbook imports and exports identically (frozen structure); a structure-changed workbook is rejected; Filetype tab is read-only for FDE; master documents are routed by filetype; snapshot used in the knowledge build |
| E04 | FDE opens project and starts processing | Pipeline started; notification; no Workflow exists until SOW approval; no API/UI path lets an FDE create a Workflow |
| E05 | Upload valid mix (PDF, DOCX, XLSX, CSV, MD, TXT, EML)                                                                             | All PARSED                                                                                                                                                                                                                                   |
| E06 | Upload EICAR / oversized / 21st file                                                                                              | Rejected with reasons                                                                                                                                                                                                                        |
| E07 | Scanned PDF                                                                                                                       | OCR via LLM; text available                                                                                                                                                                                                                  |
| E08 | Delete → restore → purge                                                                                                        | Restorable for 90 days; purged after                                                                                                                                                                                                         |
| E09 | Knowledge + OKF Bundle + explorer                                                                                                 | READY, 100% coverage, read-only graph                                                                                                                                                                                                        |
| E10 | SOW one-shot generation                                                                                                           | Cited, schema-valid, mandatory sections                                                                                                                                                                                                      |
| E11 | SOW edit + export                                                                                                                 | New versions; PDF/DOCX/MD open                                                                                                                                                                                                               |
| E12 | Approval gate                                                                                                                     | Breakdown blocked before approval; starts after                                                                                                                                                                                              |
| E13 | Scope change                                                                                                                      | New SOW version → only affected Sub-Skills rebuilt → version 2.0.0; others reused                                                                                                                                                          |
| E14 | ISATVON breakdown                                                                                                                 | Macro → Sub-Skills with 7 fields + citations; Excel export matches                                                                                                                                                                          |
| E15 | Grid safeguards                                                                                                                   | Empty/malformed/500 rows render; Generate disabled while invalid                                                                                                                                                                             |
| E16 | Manifest + hybrid codegen                                                                                                         | Manifest exactly in ISATVON 1.4 shape +`sbam_build_record`; deterministic hash; action and check tools generated; skeleton intact                                                                                                          |
| E17 | Gate failures (syntax, skeleton edit, forbidden import, MyPy, check imports action, manual`while` loop, missing handoff append) | Caught; correction triggered                                                                                                                                                                                                                 |
| E18 | Correction exhaustion                                                                                                             | After 2 attempts → NEEDS_FDE_REVIEW → FDE fix → Retry → passes                                                                                                                                                                           |
| E19 | Loop harness in CubeSandbox                                                                                                       | PASS_FIRST, ESCALATION (stops at`max_iterations`), PASS_AFTER_RETRY/NO_OP pass; handoff rows appended; `budget_limit` enforced; egress to a non-allowlisted host blocked; local-docker cannot certify                                    |
| E20 | Full certification + freeze + export + patch                                                                                      | Sub → Macro → Workflow certified; 1.0.0 frozen; tamper attempts fail; patch → 1.0.1; package checksums verify                                                                                                                             |

---
[← §18](18-error-handling-matrix.md)   [Design index](README.md)   [§20 →](20-docker-and-environments.md)
