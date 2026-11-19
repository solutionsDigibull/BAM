// E15.4-T01 (#138): interactive API. 10 VUs for 15 min on a mix of the read endpoints the FDE portal
// calls. Expected: p95 ≤ 500 ms, error rate < 1%, no 5xx (PLAN §28; #138 T01 and safety checks).
//
// Every route below is a real FastAPI route, mounted under /api/v1 (server/api/sbam_api/main.py:147):
//   GET /me                                        server/api/sbam_api/identity/router.py:207
//   GET /projects                                  server/api/sbam_api/projects/router.py:152
//   GET /notifications                             server/api/sbam_api/notifications/router.py:66
//   GET /registry                                  server/api/sbam_api/registry/router.py:36
//   GET /projects/{project_id}/goal                server/api/sbam_api/projects/router.py:133
//   GET /projects/{project_id}/documents           server/api/sbam_api/ingest/router.py:103
//   GET /projects/{project_id}/knowledge           server/api/sbam_api/knowledge/router.py:97
//   GET /projects/{project_id}/pipeline            server/api/sbam_api/pipeline/router.py:58
//   GET /projects/{project_id}/sow                 server/api/sbam_api/sow/router.py:69
//   GET /projects/{project_id}/workflows           server/api/sbam_api/workflows/router.py:32
//   GET /projects/{p}/workflows/{w}/builds         server/api/sbam_api/builds/router.py:47
// GET /preflight is left out on purpose: it probes external providers (preflight/router.py:48).
//
// Run: k6 run -e BASE_URL=... -e PROJECT_ID=... -e WORKFLOW_ID=... (+ auth, see lib/config.js)
//      tests/perf/scenarios/t01-interactive.js
// VUS and DURATION override the spec's 10 VUs / 15 min (the T06 self-test uses a short run).
import { check, sleep } from "k6";

import { PROJECT_ID, WORKFLOW_ID, apiGet, guard } from "../lib/config.js";
import { TARGETS, commonThresholds } from "../lib/targets.js";

guard(["PROJECT_ID", "WORKFLOW_ID"]);

const P = `/projects/${PROJECT_ID}`;
const W = `${P}/workflows/${WORKFLOW_ID}`;

// [path, route template, weight]. Weights lean on the project screens an FDE keeps open.
export const ENDPOINTS = [
  ["/me", "/me", 2],
  ["/projects", "/projects", 2],
  ["/notifications", "/notifications", 2],
  ["/registry", "/registry", 1],
  [`${P}/goal`, "/projects/{project_id}/goal", 1],
  [`${P}/documents`, "/projects/{project_id}/documents", 3],
  [`${P}/knowledge`, "/projects/{project_id}/knowledge", 2],
  [`${P}/pipeline`, "/projects/{project_id}/pipeline", 3],
  [`${P}/sow`, "/projects/{project_id}/sow", 2],
  [`${P}/workflows`, "/projects/{project_id}/workflows", 2],
  [`${W}/builds`, "/projects/{project_id}/workflows/{workflow_id}/builds", 2],
];
const BAG = ENDPOINTS.flatMap(([path, route, weight]) => Array(weight).fill([path, route]));

export const options = {
  scenarios: {
    interactive: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || TARGETS.interactiveVus),
      duration: __ENV.DURATION || TARGETS.interactiveDuration,
    },
  },
  thresholds: Object.assign(commonThresholds(), {
    [`http_req_duration{scenario:interactive}`]: [`p(95)<${TARGETS.interactiveP95Ms}`],
    // The project-scoped reads must answer 200 for the assigned FDE; a 404 here would mean the
    // project/workflow ids or the assignment are wrong (or tenant scoping misfired under load).
    checks: ["rate>0.99"],
  }),
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  const [path, route] = BAG[Math.floor(Math.random() * BAG.length)];
  const res = apiGet(path, route);
  check(res, { "status is 200": (r) => r.status === 200 });
  sleep(Number(__ENV.THINK_S || 1));
}
