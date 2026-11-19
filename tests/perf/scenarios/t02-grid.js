// E15.4-T02 (#138): ISATVON grid load with 500 Sub-Skills. Expected: grid data ≤ 2 s (PLAN §28
// "ISATVON grid load ≤ 2 s for 500 Sub-Skills"). k6 measures the data half only; "+ render" needs a
// browser (see README "Not covered").
//
// The workbench screen loads two things in parallel when it opens
// (client/src/app/(fde)/projects/[projectId]/workflows/[workflowId]/isatvon/workbench-screen.tsx:77-79):
//   SCHEMA_URL  = "schemas/isatvon/1.4"                         isatvon/workbench-api.ts:18
//   treeUrl     = "projects/{p}/workflows/{w}/macro-skills"     isatvon/workbench-api.ts:19-20
// served by
//   GET /schemas/isatvon/{version}                        server/api/sbam_api/isatvon/router.py:78
//   GET /projects/{p}/workflows/{w}/macro-skills          server/api/sbam_api/isatvon/router.py:119
// This script fires both in parallel (http.batch), like the screen, and times the pair as
// `grid_load_ms`. The first iteration of each VU is tagged cache:cold (#138 edge case "Cold cache
// first run"); the threshold covers cold and warm loads alike.
//
// WORKFLOW_ID must be a synthetic workflow with ≥ 500 Sub-Skills (MIN_SUB_SKILLS overrides, e.g. for
// the self-test). The check `tree has N Sub-Skills` fails the run when the fixture is too small, so a
// fast result on a small grid can never pass for a 500-row result.
import http from "k6/http";
import { check } from "k6";
import exec from "k6/execution";
import { Trend } from "k6/metrics";

import { API, PROJECT_ID, WORKFLOW_ID, guard, serverErrors, token } from "../lib/config.js";
import { TARGETS, commonThresholds } from "../lib/targets.js";

guard(["PROJECT_ID", "WORKFLOW_ID"]);

const MIN_SUB_SKILLS = Number(__ENV.MIN_SUB_SKILLS || TARGETS.gridSubSkills);
const gridLoad = new Trend("grid_load_ms", true);

export const options = {
  scenarios: {
    grid: {
      executor: "per-vu-iterations",
      vus: Number(__ENV.VUS || 1),
      iterations: Number(__ENV.ITERATIONS || 20),
      maxDuration: __ENV.MAX_DURATION || "10m",
    },
  },
  thresholds: Object.assign(commonThresholds(), {
    grid_load_ms: [`p(95)<${TARGETS.gridP95Ms}`],
    [`http_req_duration{scenario:grid}`]: [`p(95)<${TARGETS.gridP95Ms}`],
    checks: ["rate==1"],
  }),
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

const countSubSkills = (tree) =>
  (tree.macros || []).reduce((n, m) => n + (m.sub_skills ? m.sub_skills.length : 0), 0);

export default function () {
  const cache = exec.vu.iterationInScenario === 0 ? "cold" : "warm";
  const headers = { Authorization: `Bearer ${token()}`, Accept: "application/json" };
  const started = Date.now();
  const [schema, tree] = http.batch([
    ["GET", `${API}/schemas/isatvon/1.4`, null, { headers, tags: { name: "GET /schemas/isatvon/{version}", cache } }],
    [
      "GET",
      `${API}/projects/${PROJECT_ID}/workflows/${WORKFLOW_ID}/macro-skills`,
      null,
      { headers, tags: { name: "GET /projects/{project_id}/workflows/{workflow_id}/macro-skills", cache } },
    ],
  ]);
  gridLoad.add(Date.now() - started, { cache });
  for (const r of [schema, tree]) if (r.status >= 500) serverErrors.add(1);

  check(schema, { "schema 200": (r) => r.status === 200 });
  check(tree, {
    "tree 200": (r) => r.status === 200,
    [`tree has ${MIN_SUB_SKILLS} Sub-Skills`]: (r) => r.status === 200 && countSubSkills(r.json()) >= MIN_SUB_SKILLS,
  });
}
