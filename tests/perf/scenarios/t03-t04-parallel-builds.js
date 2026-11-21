// E15.4-T03 / E15.4-T04 (#138): PLACEHOLDER. Not runnable yet; it refuses to run.
//
// T03 "4 parallel builds": 4 Sub-Skills Generate at once. Expected: each sandbox + Promptfoo
//     ≤ 10 min, gates ≤ 5 min (PLAN §28; TARGETS.sandboxPromptfooSeconds / gatesSeconds).
// T04 "5th build queued": 5 concurrent. Expected: the 5th waits in the queue and does not fail
//     (#138 safety check "Builds limited to 4 parallel (Temporal limit), 5th queues, not fails").
// #138 update 2026-11-18 also asks for Temporal runner (loop limits) timings: TARGETS
//     .temporalRunnerLoopSeconds is null until the owner sets a number.
//
// Why a placeholder: both need a Temporal worker fleet plus the certifying CubeSandbox host
// (AGENTS.md: "CubeSandbox is not available on laptops"; the local-docker profile never certifies),
// and a synthetic workflow whose Sub-Skills are ready to generate. Neither exists in a perf
// environment yet.
//
// What the real script will do, with the routes that exist today:
//   - start:  POST /projects/{p}/workflows/{w}/generate (server/api/sbam_api/isatvon/router.py:257;
//             client generateUrl, isatvon/workbench-api.ts:29) or POST .../sub-skills/{sub_id}/regenerate
//             (isatvon/router.py:241; client regenerateUrl, workbench-api.ts:30), each with an
//             Idempotency-Key; 202 + RunAccepted.
//   - follow: GET /projects/{p}/runs/{run_id}  (server/api/sbam_api/temporal/router.py:43) and
//             GET /projects/{p}/workflows/{w}/builds (builds/router.py:47),
//             GET .../sub-skills/{sub_id}/gates (builds/router.py:55).
//   - T04 asserts the 5th run reports a queued state while 4 run, then completes; never FAILED.
// Open questions before writing it: which run field says "queued" (Run contract), and how the
// 4-slot limit is exposed (worker max_concurrent_activities vs. a Temporal task-queue rate limit).
import { TARGETS } from "../lib/targets.js";

export const options = { vus: 1, iterations: 1 };

export default function () {
  throw new Error(
    `E15.4-T03/T04 are placeholders: they need a Temporal + CubeSandbox host. Targets: ` +
      `gates ≤ ${TARGETS.gatesSeconds}s, sandbox+Promptfoo ≤ ${TARGETS.sandboxPromptfooSeconds}s, ` +
      `${TARGETS.parallelBuilds} parallel builds. See tests/perf/README.md.`,
  );
}
