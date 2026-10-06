> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# PLAN — Team Allocation and Parallel Development Plan

## PLAN §24. Team Allocation and Parallel Development Plan


### 24.1 Streams (4 developers)

| Dev         | Stream                               | Owns                                                                                                                                                                                           |
| ----------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A** | Platform, DevOps, identity, security | Repo, Docker/Compose, CI, Keycloak, tenancy/RBAC, storage, projects/masters API, runtime image, CubeSandbox host, Netbird, observability, environments, backup, security tests                 |
| **B** | Knowledge and LLM                    | Ingestion/parsing/OCR, Cognee, OKF, Bifrost/OpenRouter layer, SOW generation/export/approval/impact, ISATVON extraction and APIs, LLM golden sets                                              |
| **C** | Build pipeline                       | Contracts (ISATVON/manifest), Temporal pipeline, manifest builder, templates, hybrid codegen, correction, AST/static gates, test data, Promptfoo, sandbox adapter, certification/freeze/export |
| **D** | Frontend                             | Next.js shell, Admin portal, all FDE screens (upload, processing, explorer, SOW, grid, manifests, code, build/test, certification, versions, notifications), E2E suite                         |

### 24.2 Indicative timeline *(estimate, 4 developers in parallel)*

```text
Week:            1    2    3    4    5    6    7    8    9    10   11   12   13   14   15
Phase 0          ███
Dev A  P1/P2/P3       ████ ████ ███                                  (then: runtime image, sandbox host,
                                                                       observability, environments)
Dev A  sandbox host + Netbird            ████ ████                     P16 ████ ████
Dev B  P4 ingest            ████ ██
Dev B  P5 knowledge                  ██ ████ ██
Dev B  P6/P7 SOW + approval                    ████ ███
Dev B  P8 ISATVON extraction                            ████ ████
Dev C  contracts + Temporal skeleton  ███ ████ ███
Dev C  P9 manifest + P11 codegen (fixtures first)  ████ ████ ████
Dev C  P12 gates + P13 test/sandbox                            ████ ████ ████
Dev C  P14 certification                                                 ████ ███
Dev D  shell + Admin portal       ████ ████
Dev D  FDE screens (behind each API)        ████ ████ ████ ████ ████ ████
Dev D  P15 E2E                                                       ████ ████ ███
Go-live readiness                                                               ████
```

Indicative MVP duration: **about 15 weeks** with 4 developers, provided the Phase 0 inputs arrive in week 1 and the KVM host is ready by week 4. Track C starts code generation against fixture manifests (from the ISATVON example) before extraction is finished, which removes the biggest serial dependency.

### 24.3 Critical path

```text
Phase 0 inputs (ISATVON example, masters schema, SOW template)
  → Contracts (SBAM-001/002) → Temporal skeleton (012, 040)
  → Ingestion (020/021) → Cognee + OKF (024/025) → SOW (029) → Approval (032)
  → ISATVON extraction (034/035) → Manifest (038)
  → Codegen (042/043) → Gates (047/048) → Test data + Promptfoo + CubeSandbox (050–053)
  → Certification + freeze + export (055–057) → E2E (060) → UAT (065)
```

### 24.4 Integration points

| IP   | When        | Demo                                                                                                               |
| ---- | ----------- | ------------------------------------------------------------------------------------------------------------------ |
| IP-1 | End week 4 | Admin creates a project with masters; FDE opens the project and uploads; documents PARSED |
| IP-2 | End week 7  | Knowledge graph + OKF Bundle → SOW generated, edited, approved                                                    |
| IP-3 | End week 10 | ISATVON breakdown in the grid → manifests → code generated and passing AST/static gates (fixture-driven earlier) |
| IP-4 | End week 13 | Full build on CubeSandbox with Promptfoo → Sub/Macro/Workflow certified → frozen → exported                     |
