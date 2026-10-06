#!/usr/bin/env node
// PreToolUse hook for Edit/Write/MultiEdit/NotebookEdit: blocks edits to files that must never be hand-edited.
//   - client/src/contracts/**  generated from server/contracts by `make contracts` (CI fails on drift)
//   - .env / .env.* (except .env.example)  secrets never go through the agent
//   - Alembic migrations already on upstream/main  (edit-in-place breaks other developers' databases)
import { execFileSync } from "node:child_process";
import path from "node:path";

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
let input;
try { input = JSON.parse(raw); } catch { process.exit(0); }
const file = input?.tool_input?.file_path || input?.tool_input?.notebook_path || "";
if (!file) process.exit(0);
const root = process.env.CLAUDE_PROJECT_DIR || input?.cwd || process.cwd();
const rel = path.relative(root, path.resolve(root, file)).split(path.sep).join("/");
const block = (msg) => { process.stderr.write(`Blocked by .claude/hooks/guard-files.mjs: ${msg}\n`); process.exit(2); };

if (/(^|\/)client\/src\/contracts\//.test(rel)) block(`${rel} is generated. Change the Pydantic model in server/contracts and run 'make contracts'.`);
const base = path.posix.basename(rel);
if (/^\.env(\..+)?$/.test(base) && base !== ".env.example") block(`${rel} holds secrets. Edit it yourself outside the agent; document new variables in compose/.env.example.`);
if (/(^|\/)migrations\/versions\/[^/]+\.py$/.test(rel)) {
  try {
    execFileSync("git", ["-C", root, "cat-file", "-e", `upstream/main:${rel}`], { stdio: "ignore" });
    block(`${rel} is already on main. Never edit a merged migration; add a new revision instead.`);
  } catch { /* not on main yet: your own new migration, editing is fine */ }
}
process.exit(0);
