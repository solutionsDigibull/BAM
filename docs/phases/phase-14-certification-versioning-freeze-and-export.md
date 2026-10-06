> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 14 — Certification, Versioning, Freeze and Export

| Epic | Milestone | Stories |
|---|---|---|
| [#15](https://github.com/solutionsDigibull/BAM/issues/15) `E14` | M4 Test + Certify | 6 |


**Goal:** Certify automatically at three levels (Sub-Skill, Macro Skill, Workflow), version and freeze the result, and produce the exportable manifest package for SRIM.

**Owner:** Dev C (backend) + Dev D (UI) · **Indicative effort:** 1.5 weeks · **Complexity:** L · **Depends on:** Phase 13
**Design references:** [TD §11](../design/11-manifest-design.md), [TD §14.4–14.6](../design/14-state-machines.md).

### Certification

- [ ] Sub-Skill auto-certification: evidence checklist (Skill Manifest valid, libraries resolved, all AST checks, static/security, schema SQL, ≥3 loop scenarios pass in certifying CubeSandbox) on one code hash → `CERTIFIED`; manifest `status` and `governance.lifecycle.state` set to `APPROVED`, `immutable_after_approval: true`.
- [ ] Macro Skill certification: all its Sub-Skills certified + intra-macro dependency check (each Sub-Skill's input schema is satisfied by upstream outputs or declared sources).
- [ ] Workflow validation and certification: all Macro Skills certified, complete Xyflow DAG, no dangling dependencies, workflow-level smoke run chaining Sub-Skills on synthetic data *(proposed)*, Workflow manifest assembled and hashed.
- [ ] Reuse: unchanged Sub-Skills carry their certification into the new version. The reuse key is the hash of `isatvon_definition` + `contracts` + template version/hash + runtime digest + test-suite hash ([TD §11.4](../design/11-manifest-design.md)), not the full manifest hash, which includes version and timestamps.

### Versioning and freeze

- [ ] MAJOR.0.PATCH versioning ([TD §11.4](../design/11-manifest-design.md)): major on new approved SOW version; patch on Sub-Skill-level rebuilds under the same SOW.
- [ ] Freeze: DB immutability trigger on certified records; storage files marked read-only/immutable (S3 Object Lock later); read-back hash verification.
- [ ] No revocation; changes always create a new version.

### Export (MVP handover)

- [ ] Export package: Workflow manifest + Skill Manifests (ISA14 format) + code bundles + `loop_spec.json` files + `schema.sql` + evidence index + checksums (zip), downloadable by the FDE.
- [ ] SRIM handover API kept as a documented interface stub only **[FUTURE]**.

### Frontend

- [ ] Certification view: tree Workflow → Macro → Sub-Skill with status, evidence links, version and hash; Version History with diff and lineage; Export button.

### Tests

- [ ] Partial pass (one Sub-Skill fails) leaves its Macro and the Workflow uncertified.
- [ ] A change to one Sub-Skill after freeze creates version 1.0.1 with the others reused.
- [ ] Attempts to modify frozen records via API, SQL or file system fail.

### Exit Criteria

- [ ] A Workflow is certified only when every Macro Skill and Sub-Skill is certified on current hashes.
- [ ] Frozen versions are immutable, and recomputing any frozen manifest hash reproduces it.
- [ ] The FDE can download a complete, checksum-verified export package.

## Design pages to read

- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §11. Manifest design](../design/11-manifest-design.md)
- [TD §13. Temporal design](../design/13-temporal-design.md)
- [TD §14. State machines](../design/14-state-machines.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#129](https://github.com/solutionsDigibull/BAM/issues/129) | `E14.1` | Sub-Skill certification checklist + manifest APPROVED | C Build | M | P0 | M4 | [#127](https://github.com/solutionsDigibull/BAM/issues/127), [#123](https://github.com/solutionsDigibull/BAM/issues/123), [#119](https://github.com/solutionsDigibull/BAM/issues/119), [#118](https://github.com/solutionsDigibull/BAM/issues/118) |
| [#130](https://github.com/solutionsDigibull/BAM/issues/130) | `E14.2` | Macro + Workflow validation and certification | C Build | M | P0 | M4 | [#129](https://github.com/solutionsDigibull/BAM/issues/129), [#105](https://github.com/solutionsDigibull/BAM/issues/105) |
| [#131](https://github.com/solutionsDigibull/BAM/issues/131) | `E14.3` | Certification reuse for unchanged Sub-Skills | C Build | S | P0 | M4 | [#129](https://github.com/solutionsDigibull/BAM/issues/129) |
| [#132](https://github.com/solutionsDigibull/BAM/issues/132) | `E14.4` | Versioning MAJOR.0.PATCH + freeze + immutability triggers | C Build | M | P0 | M4 | [#130](https://github.com/solutionsDigibull/BAM/issues/130) |
| [#133](https://github.com/solutionsDigibull/BAM/issues/133) | `E14.5` | Export package (manifests, code, loop specs, schema.sql, evidence, checksums) | A Platform | S | P0 | M4 | [#132](https://github.com/solutionsDigibull/BAM/issues/132) |
| [#134](https://github.com/solutionsDigibull/BAM/issues/134) | `E14.6` | Certification tree, versions/lineage and export UI | D Frontend | M | P0 | M4 | [#132](https://github.com/solutionsDigibull/BAM/issues/132), [#133](https://github.com/solutionsDigibull/BAM/issues/133) |

---
[← Phase 13](phase-13-test-data-promptfoo-and-cubesandbox-execution.md)   [Phases index](README.md)   [Phase 15 →](phase-15-end-to-end-testing-security-and.md)
