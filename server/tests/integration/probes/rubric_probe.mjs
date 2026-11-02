// Runs inside the sbam-promptfoo container (as its own user): submits grading jobs through the shared
// job directory exactly like the build worker does, and prints one JSON line with the results.
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";

const JOBS = process.env.PROMPTFOO_JOBS ?? "/jobs";

async function submit(request) {
  const job = `${JOBS}/${randomBytes(8).toString("hex")}`;
  mkdirSync(job);
  writeFileSync(`${job}/request.json`, JSON.stringify(request));
  writeFileSync(`${job}/READY`, "");
  const deadline = Date.now() + 200_000;
  while (!existsSync(`${job}/DONE`)) {
    if (Date.now() > deadline) return { timeout: true };
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  const result = JSON.parse(readFileSync(`${job}/result.json`, "utf8"));
  rmSync(job, { recursive: true, force: true });
  return result;
}

const graded = await submit({
  alias: "sbam-rubric",
  cases: [
    { name: "polite", rubric: "The text is polite.", output: "Thank you for waiting." },
    { name: "rude", rubric: "The text is polite.", output: "MOCK_RUBRIC_FAIL go away" },
  ],
});
const refused = await submit({
  alias: "sbam-codegen",
  cases: [{ name: "a", rubric: "r", output: "o" }],
});
console.log(JSON.stringify({ graded, refused }));
