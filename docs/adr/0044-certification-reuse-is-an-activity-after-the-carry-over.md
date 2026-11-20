# 0044 — Certification reuse is its own activity right after the carry-over, not a pipeline stage

- Status: Accepted
- Date: 2026-11-21
- Related: issue #131 (E14.3), PRs #655 (reuse key and decision), #969 (`reused_from_id`); the D3 wiring question of 2026-11-03

## Context

`carry_over_workflow` (E7.2) copies an untouched, built Sub-Skill into the next Workflow version as CERTIFIED
with `reused_from_id`, but it wrote no certificate or manifest and compared nothing, so the rule "any difference
in any part of the reuse key forces a rebuild" was not enforced. `reuse.decide` existed and nothing called it.
The open question was whether reused Sub-Skills should run the build stages (manifest, dataset, suite) and then a
reuse activity, and who wires it into the pipeline.

## Decision

1. **Own activity, same stage.** `reuse_certifications` (queue `sbam-certify`, `certify/reuse_step.py`) runs right
   after `carry_over_workflow` in the stage that creates the Workflow, behind `workflow.patched("e14-3-reuse-certifications")`.
   No stage is added to ProjectPipeline; runs recorded before the marker replay unchanged.
2. **Compare, then carry.** For each carried Sub-Skill it computes the manifest the build would write now, the suite
   it would be tested with (its own newest test data, else the source's) and calls `reuse.decide` against the source's
   certified manifest. It re-verifies the source first: the stored bundle's files hash to the certified
   `bundle_sha256`, the manifest is the frozen one the certificate names, the sandbox run was on the certifying host
   (a local-docker run never certifies, so never reuses) and the test run matches. Any mismatch refuses.
3. **Reused:** a frozen APPROVED manifest of the new Workflow (version kept, `certification.reused_from` set), a code
   attempt pointing at the same stored bundle, and a certificate with `certifications.reused_from_id` (composite
   foreign key on tenant, project, id). **Refused:** the Sub-Skill goes CERTIFIED to INVALIDATED to DRAFT to VALID through the
   state machine and is built like any other; a tampered source also leaves a `FROZEN_HASH_VERIFICATION_FAILED` audit row.
4. **Audit:** one `CERTIFICATION_REUSED` or `CERTIFICATION_REUSE_REFUSED` row per Sub-Skill with the compared part hashes (ids
   and hashes only).

## Consequences

- A patch version (`new-version` on a frozen Sub-Skill) re-opens the same rows, so the other Sub-Skills keep their
  certificates and never pass through this step. A platform runtime bump between patch versions is not checked there.
- The copy has no test data of its own; until the FDE adds some it is compared with the source's dataset.
- The read marker for the certification tree (#134) is `ManifestView.reused_from`; the UI is #134's.
