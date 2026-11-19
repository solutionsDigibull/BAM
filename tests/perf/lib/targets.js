// E15.4 (#138) performance targets: the ONE place the numbers live. Every scenario builds its
// `options.thresholds` from here, so a breach makes `k6 run` exit non-zero (exit code 99).
//
// Sources:
//   PLAN §28 "Release acceptance targets" (docs/plan/06-mvp-dod-delivery-release.md, lines 176-181)
//   Issue #138, "Inputs" table row "Targets" and test cases E15.4-T01..T06.
//
// These are release targets, not measured baselines. Before a breach is trusted, measure a baseline
// on the prod-like deploy VM (same image digests as the release, obs profile on). See README.md.

export const TARGETS = {
  // PLAN §28: "API p95: ≤ 500 ms for interactive endpoints". #138 T01: "p95 ≤ 500 ms".
  interactiveP95Ms: 500,
  // #138 T01 "error rate < 1%"; safety check "Error rate < 1% during load".
  errorRate: 0.01,
  // #138 safety check "No 5xx or tenant leakage under concurrency": zero 5xx responses.
  max5xx: 0,
  // PLAN §28: "ISATVON grid load ≤ 2 s for 500 Sub-Skills". #138 T02: "Grid data + render ≤ 2 s".
  gridP95Ms: 2000,
  // PLAN §28 / #138 T02: the grid is measured with 500 Sub-Skills.
  gridSubSkills: 500,
  // PLAN §28: "≥ 10 concurrent users (MVP)". #138 T01: "10 VUs 15 min mixed endpoints".
  interactiveVus: 10,
  interactiveDuration: "15m",
  // PLAN §28: "SOW draft: ≤ 5 min after the knowledge version is READY (20 files × 15 MB maximum)".
  // #138 T05: "20 × 15 MB files; All PARSED". The 2026-11-18 update says the SOW timing predates
  // the workflow pivot, so T05 encodes "all PARSED" and records knowledge READY time only.
  ingestFiles: 20,
  ingestFileBytes: 15 * 1024 * 1024, // MAX_FILE_BYTES, server/contracts/sbam_contracts/documents.py:51
  ingestSettleSeconds: 30 * 60, // how long T05 waits for PARSED before failing (not a target)
  sowAfterReadySeconds: 5 * 60, // NOT encoded yet: see README "Not covered"
  // PLAN §28: "Validation gates: ≤ 5 min per Sub-Skill"; "Sandbox + Promptfoo: ≤ 10 min per
  // Sub-Skill"; "≥ 4 parallel Sub-Skill builds". #138 T03/T04. Placeholders only (need the sandbox host).
  gatesSeconds: 5 * 60,
  sandboxPromptfooSeconds: 10 * 60,
  parallelBuilds: 4,
  // #138 "Update 2026-11-18": add timings for the Temporal runner (loop limits) and the workbench
  // screens. No numbers are agreed yet; they stay null until the owner sets them, and nothing
  // encodes them. Do not invent a value here.
  temporalRunnerLoopSeconds: null,
  workbenchScreenP95Ms: null,
};

/** Thresholds shared by every API scenario: <1% failed requests and no 5xx at all. */
export function commonThresholds() {
  return {
    http_req_failed: [`rate<${TARGETS.errorRate}`],
    server_errors: [`count<=${TARGETS.max5xx}`],
  };
}
