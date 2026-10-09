// Spike E0.12 (#30): wrapper around the Promptfoo CLI, as the test worker would call it (TD §15.1).
//
//   node run_suite.mjs [suite.yaml]
//
// 1. Hash the suite file (suite_sha256) BEFORE the run, so the stored result proves which suite ran.
// 2. Refuse suites with fewer than 3 cases (TD §15.1: >= 3 loop scenarios), without running Promptfoo.
// 3. Run `promptfoo eval` with no cache, no shared DB and no telemetry; results go to out/results.json.
// 4. Map results.json to a `test_runs`-shaped row (TD §7) in out/test_run.json and print a summary.
//
// Exit codes: 0 all passed · 1 an assertion failed · 2 suite invalid · 3 Promptfoo itself failed.
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const MIN_CASES = 3;
const here = dirname(fileURLToPath(import.meta.url));
const suitePath = resolve(process.argv[2] ?? join(here, "..", "suite.yaml"));
const workDir = dirname(suitePath); // file:// vars in the suite resolve from here
const outDir = join(workDir, "out");
const resultsPath = join(outDir, "results.json");
const testRunPath = join(outDir, "test_run.json");

// promptfoo's "exports" hides package.json from require.resolve, so read it from our own node_modules.
const pfPkgPath = join(here, "node_modules", "promptfoo", "package.json");
const pfPkg = JSON.parse(readFileSync(pfPkgPath, "utf8"));
const pfBin = join(dirname(pfPkgPath), typeof pfPkg.bin === "string" ? pfPkg.bin : pfPkg.bin.promptfoo);

function finish(row, code) {
  mkdirSync(outDir, { recursive: true });
  writeFileSync(testRunPath, JSON.stringify(row, null, 2) + "\n");
  console.log(`status=${row.status} total=${row.total} passed=${row.passed} failed=${row.failed} ` +
    `duration_ms=${row.duration_ms} suite_sha256=${row.suite_sha256}`);
  for (const r of row.results.filter((r) => !r.pass)) {
    console.log(`  FAIL ${r.case}`);
    for (const a of r.failed_assertions) console.log(`       - ${a.metric ?? a.type}: ${a.reason}`);
    if (r.error && r.failed_assertions.length === 0) console.log(`       - error: ${r.error}`);
  }
  process.exit(code);
}

// 1. Hash first: the bytes we hash are the bytes Promptfoo reads.
const suiteBytes = readFileSync(suitePath);
const base = {
  suite_sha256: createHash("sha256").update(suiteBytes).digest("hex"),
  promptfoo_version: pfPkg.version,
  total: 0, passed: 0, failed: 0, errors: 0, duration_ms: 0,
  results: [],
  report_key: null, // the product stores the full report in StorageBackend; the spike keeps it in out/
};

// 2. Suite gate.
const tests = parse(suiteBytes.toString("utf8"))?.tests;
if (!Array.isArray(tests) || tests.length < MIN_CASES) {
  const n = Array.isArray(tests) ? tests.length : 0;
  finish({ ...base, status: "INVALID_SUITE", reason: `suite has ${n} test case(s); at least ${MIN_CASES} required` }, 2);
}

// 3. Run Promptfoo. Its own exit code is non-zero on any failed assertion, so we judge by results.json.
rmSync(resultsPath, { force: true });
const started = Date.now();
const run = spawnSync(
  process.execPath,
  [pfBin, "eval", "-c", suitePath, "--output", resultsPath, "--no-cache", "--no-write", "--no-table", "--no-progress-bar"],
  {
    cwd: workDir,
    encoding: "utf8",
    env: { ...process.env, PROMPTFOO_DISABLE_TELEMETRY: "1", PROMPTFOO_DISABLE_UPDATE: "1", PROMPTFOO_DISABLE_SHARING: "1" },
  },
);
const duration_ms = Date.now() - started;
if (!existsSync(resultsPath)) {
  const tail = `${run.stdout ?? ""}${run.stderr ?? ""}`.trim().split("\n").slice(-5).join(" | ");
  finish({ ...base, duration_ms, status: "ERROR", reason: `promptfoo produced no results (exit ${run.status}): ${tail}` }, 3);
}

// 4. Map to the test_runs shape.
const report = JSON.parse(readFileSync(resultsPath, "utf8")).results;
const results = report.results
  .map((x) => ({
    case: x.testCase?.description ?? `test ${x.testIdx}`,
    pass: x.success === true,
    error: x.error ?? null,
    failed_assertions: (x.gradingResult?.componentResults ?? [])
      .filter((c) => !c.pass)
      // first line only: a thrown assertion's reason carries a full stack trace
      .map((c) => ({ metric: c.assertion?.metric ?? null, type: c.assertion?.type ?? null, reason: String(c.reason ?? "").split("\n")[0] })),
  }))
  .sort((a, b) => a.case.localeCompare(b.case));
const passed = results.filter((r) => r.pass).length;
const errors = report.stats?.errors ?? 0;
const row = { ...base, total: results.length, passed, failed: results.length - passed, errors, duration_ms, results };
finish({ ...row, status: row.failed === 0 ? "PASSED" : "FAILED" }, row.failed === 0 ? 0 : 1);
