// k6-free check of the scenarios' init context (#138), for machines without k6:
//   node tests/perf/selftest/init_check.mjs
// It stubs the k6/* modules, imports every scenario with test env, and asserts that
//   - each module graph resolves and its init code runs,
//   - options.thresholds carries the encoded targets (so a real k6 run would fail on breach),
//   - the production guard refuses BASE_URL values containing "prod".
// It does NOT run load or evaluate thresholds; only `k6 run` (see run_t06.sh) proves the exit code.
import assert from "node:assert/strict";
import { register } from "node:module";
import { pathToFileURL } from "node:url";

const stub = `
const fn = () => ({});
export default { get: fn, post: fn, patch: fn, batch: () => [], CookieJar: class {},
  randomBytes: () => new ArrayBuffer(8), sha256: () => "", hexEncode: () => "", b64encode: () => "",
  test: { abort() {} }, vu: {}, scenario: {} };
export const check = () => true, sleep = () => {};
export class Counter { add() {} } export class Trend { add() {} } export class Rate { add() {} }
`;
const hooks = `
export async function resolve(spec, ctx, next) {
  if (spec === "k6" || spec.startsWith("k6/")) return { url: "data:text/javascript," + encodeURIComponent(${JSON.stringify(stub)}), shortCircuit: true };
  return next(spec, ctx);
}`;
register("data:text/javascript," + encodeURIComponent(hooks));

const here = new URL(".", import.meta.url);
const load = async (file, env) => {
  globalThis.__ENV = env;
  return import(new URL(`../scenarios/${file}`, here).href + `?${Math.random()}`);
};
const ENV = {
  BASE_URL: "http://127.0.0.1:8765",
  TOKEN: "selftest",
  PROJECT_ID: "00000000-0000-4000-8000-000000000001",
  WORKFLOW_ID: "00000000-0000-4000-8000-000000000002",
};

const t01 = await load("t01-interactive.js", ENV);
assert.deepEqual(t01.options.thresholds["http_req_duration{scenario:interactive}"], ["p(95)<500"]);
assert.deepEqual(t01.options.thresholds.http_req_failed, ["rate<0.01"]);
assert.equal(t01.options.scenarios.interactive.vus, 10);
assert.equal(t01.options.scenarios.interactive.duration, "15m");
console.log("init_check: OK t01-interactive.js", JSON.stringify(t01.options.thresholds));

const t02 = await load("t02-grid.js", ENV);
assert.deepEqual(t02.options.thresholds.grid_load_ms, ["p(95)<2000"]);
assert.deepEqual(t02.options.thresholds["http_req_duration{scenario:grid}"], ["p(95)<2000"]);
console.log("init_check: OK t02-grid.js", JSON.stringify(t02.options.thresholds));

const t05 = await load("t05-ingestion.js", ENV);
assert.deepEqual(t05.options.thresholds.ingest_documents_parsed, ["rate==1"]);
assert.equal(t05.options.scenarios.upload.vus, 20);
console.log("init_check: OK t05-ingestion.js", JSON.stringify(t05.options.thresholds));

await load("t03-t04-parallel-builds.js", ENV);
console.log("init_check: OK t03-t04-parallel-builds.js (placeholder)");

for (const BASE_URL of ["https://bam-prod.example.com", "https://PROD.example.com"]) {
  await assert.rejects(load("t01-interactive.js", { ...ENV, BASE_URL }), /looks like production/);
}
await assert.rejects(load("t02-grid.js", { ...ENV, BASE_URL: "" }), /BASE_URL is required/);
await assert.rejects(load("t01-interactive.js", { ...ENV, TOKEN: "" }), /Set TOKEN/);
console.log("init_check: OK guard refuses prod-looking BASE_URL, missing BASE_URL, missing auth");
