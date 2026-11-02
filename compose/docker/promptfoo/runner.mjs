// sbam-promptfoo job runner (E13.6c, #638): `llm-rubric` grading of free-text outputs.
//
// The build worker hands this container work through a shared directory and nothing else:
//
//   /jobs/<id>/request.json   {"alias": "sbam-rubric", "cases": [{"name", "rubric", "output"}]}
//   /jobs/<id>/READY          written last: the job may be taken
//   /jobs/<id>/result.json    {"verdicts": {"<name>": true|false}, "error": null|"...", "version"}
//   /jobs/<id>/DONE           written after result.json
//
// The request names an alias and cases, never a command line or a provider: the Promptfoo config is
// built here, from a fixed template. The grading model is reached through Bifrost only (BIFROST_URL),
// so "LLM calls go through Bifrost with an alias" holds; this container holds no provider key.
// The output under grade is the Sub-Skill's own text and is data: it goes in as a variable, never
// as part of the config or the grading instructions. Node standard library only.

import { spawnSync } from "node:child_process";
import {
  existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, utimesSync, writeFileSync,
} from "node:fs";
import { join } from "node:path";

const JOBS = process.env.PROMPTFOO_JOBS ?? "/jobs";
const SCRATCH = process.env.PROMPTFOO_SCRATCH ?? "/work";
const BIFROST_URL = process.env.BIFROST_URL ?? "http://bifrost:8080/v1";
const TIMEOUT_MS = Number(process.env.PROMPTFOO_TIMEOUT ?? "240") * 1000;
const POLL_MS = Number(process.env.PROMPTFOO_POLL ?? "250");
const HEARTBEAT = process.env.PROMPTFOO_HEARTBEAT ?? "/tmp/heartbeat";
const PROMPTFOO_BIN = process.env.PROMPTFOO_BIN ?? "promptfoo";
const ALIASES = new Set(["sbam-rubric"]); // the only model this container may ask Bifrost for
const NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/;
const MAX_CASES = 20;
const MAX_TEXT = 40_000;

class Refused extends Error {}

export function checkRequest(request) {
  if (typeof request !== "object" || request === null) throw new Refused("the request is not an object");
  if (!ALIASES.has(request.alias)) throw new Refused("the alias is not allowed");
  const cases = request.cases;
  if (!Array.isArray(cases) || cases.length === 0 || cases.length > MAX_CASES) {
    throw new Refused("the case list is empty or too long");
  }
  const seen = new Set();
  for (const c of cases) {
    if (typeof c !== "object" || c === null) throw new Refused("a case is not an object");
    if (!NAME.test(c.name ?? "") || seen.has(c.name)) throw new Refused("a case name is not allowed");
    seen.add(c.name);
    for (const field of ["rubric", "output"]) {
      if (typeof c[field] !== "string" || c[field].length > MAX_TEXT) {
        throw new Refused(`a case ${field} is not a short enough string`);
      }
    }
  }
  return request;
}

// A fixed template: the echo provider returns the output as the model's answer, and `llm-rubric`
// grades it with the Bifrost alias. Nothing from the request is placed in a position that can change
// how the config is read except the case name (a plain description) and the data in `vars`.
export function configFor(request) {
  return {
    description: "sbam rubric grading",
    prompts: ["{{output}}"],
    providers: ["echo"],
    defaultTest: {
      options: {
        provider: {
          id: `openai:chat:${request.alias}`,
          config: { apiBaseUrl: BIFROST_URL, apiKey: "none", temperature: 0 },
        },
      },
    },
    tests: request.cases.map((c) => ({
      description: c.name,
      vars: { output: c.output },
      assert: [{ type: "llm-rubric", value: c.rubric }],
    })),
  };
}

export function verdictsOf(report, names) {
  const rows = report?.results?.results;
  if (!Array.isArray(rows)) throw new Error("promptfoo wrote no results");
  const verdicts = {};
  for (const row of rows) {
    const name = row?.testCase?.description;
    if (!names.includes(name) || typeof row.success !== "boolean") continue;
    // Only a met or an unmet rubric is a verdict. When Bifrost or the model cannot answer, Promptfoo
    // still writes the row with `success: false` and `failureReason: 1` (an assertion failure), and
    // puts "API call error: ..." in the grading reason, so that prefix is what tells the
    // infrastructure's failure apart. `failureReason: 2` is an error outright. Neither may ever read
    // as "the rubric was not met", which would blame the code.
    const reason = `${row.error ?? ""} ${row.gradingResult?.reason ?? ""}`.trim();
    if (row.success) verdicts[name] = true;
    else if (row.failureReason === 1 && !/^API call error/.test(reason)) verdicts[name] = false;
    else throw new Error("the grader could not grade a case");
  }
  for (const name of names) {
    if (!(name in verdicts)) throw new Error("promptfoo gave no verdict for a case");
  }
  return verdicts;
}

function version() {
  const done = spawnSync(PROMPTFOO_BIN, ["--version"], {
    encoding: "utf8",
    timeout: 30_000,
    env: { PATH: process.env.PATH, HOME: SCRATCH, PROMPTFOO_DISABLE_TELEMETRY: "1", PROMPTFOO_DISABLE_UPDATE: "1" },
  });
  return (done.stdout ?? "").trim().slice(0, 40);
}

function grade(request) {
  const work = join(SCRATCH, `run-${process.pid}-${Date.now()}`);
  mkdirSync(work, { recursive: true });
  try {
    const config = join(work, "promptfooconfig.json");
    const out = join(work, "results.json");
    writeFileSync(config, JSON.stringify(configFor(request)));
    const done = spawnSync(
      PROMPTFOO_BIN,
      ["eval", "-c", config, "--output", out, "--no-cache", "--no-progress-bar", "--max-concurrency", "2"],
      {
        cwd: work,
        encoding: "utf8",
        timeout: TIMEOUT_MS,
        // an empty environment but the two settings Promptfoo needs: no inherited secrets
        env: { PATH: process.env.PATH, HOME: work, PROMPTFOO_DISABLE_TELEMETRY: "1", PROMPTFOO_DISABLE_UPDATE: "1" },
      },
    );
    if (done.error) throw new Error(done.error.code === "ETIMEDOUT" ? "promptfoo timed out" : "promptfoo could not start");
    // `eval` exits 100 when an assertion failed: that is a verdict, not a crash. Anything else
    // without a results file is the infrastructure's failure.
    if (!existsSync(out)) throw new Error(`promptfoo exited with ${done.status} and wrote no results`);
    return verdictsOf(JSON.parse(readFileSync(out, "utf8")), request.cases.map((c) => c.name));
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

function writeResult(job, result) {
  writeFileSync(join(job, "result.json"), JSON.stringify(result));
  writeFileSync(join(job, "DONE"), "");
}

function take(job) {
  let result;
  try {
    const request = checkRequest(JSON.parse(readFileSync(join(job, "request.json"), "utf8")));
    result = { verdicts: grade(request), error: null, version: version() };
  } catch (error) {
    // a message of fixed shape: never the output, the rubric or the model's reasoning
    const message = error instanceof Refused ? `refused: ${error.message}` : String(error?.message ?? "failed");
    result = { verdicts: {}, error: message.slice(0, 120), version: "" };
  }
  writeResult(job, result);
}

function pass() {
  const now = new Date();
  try {
    utimesSync(HEARTBEAT, now, now);
  } catch {
    writeFileSync(HEARTBEAT, "");
  }
  for (const id of readdirSync(JOBS)) {
    const job = join(JOBS, id);
    if (!statSync(job).isDirectory()) continue;
    if (existsSync(join(job, "READY")) && !existsSync(join(job, "DONE"))) take(job);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  for (;;) {
    try {
      pass();
    } catch {
      // a job directory removed mid-pass: the next pass starts clean
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}
