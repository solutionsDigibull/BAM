#!/usr/bin/env node
// PreToolUse hook for Bash/PowerShell: blocks commands that break the team's hard rules (AGENTS.md).
// AGENTS.md is advisory; this hook enforces it. Exit 2 = block, and stderr is shown to Claude.
// Node instead of bash+jq so it runs the same on Windows, macOS and Linux (Node 22 is a repo prerequisite).
//
// Blocks: pushing to a protected branch (explicit refspec, or plain `git push` while on it), force pushes
// (--force/-f/+refspec; --force-with-lease is allowed), --no-verify, `gh issue close`, `gh pr merge --admin`.
import { execFileSync } from "node:child_process";

const PROTECTED = (process.env.PROTECTED_BRANCHES || "main master").split(/\s+/).filter(Boolean);

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
let input;
try { input = JSON.parse(raw); } catch { process.exit(0); }
const cmd = input?.tool_input?.command ?? "";
const cwd = input?.cwd || process.cwd();
if (!cmd) process.exit(0);

const block = (msg) => { process.stderr.write(`Blocked by .claude/hooks/guard-git.mjs: ${msg}\n`); process.exit(2); };
const currentBranch = () => {
  try { return execFileSync("git", ["-C", cwd, "branch", "--show-current"], { encoding: "utf8" }).trim(); }
  catch { return ""; }
};

// Shell-like split that keeps quoted strings ("C:/Program Files/GitHub CLI/gh.exe") as one token.
const tokens = (s) => [...s.matchAll(/"((?:\\.|[^"\\])*)"|'([^']*)'|(\S+)/g)].map((m) => m[1] ?? m[2] ?? m[3]);

// Check each sub-command of a chain separately (&&, ||, ;, |, newlines).
for (const seg of cmd.split(/&&|\|\||;|\||\n/)) {
  const w = tokens(seg.trim());
  while (w.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(w[0])) w.shift(); // leading VAR=value
  if (w[0] === "&") w.shift(); // PowerShell call operator
  const exe = (w[0] || "").replace(/\.exe$/i, "").split(/[\\/]/).pop();

  if (exe === "git" && w.includes("push")) {
    const args = w.slice(w.indexOf("push") + 1);
    const positional = [];
    for (const t of args) {
      if (t === "--force" || t === "-f" || t.startsWith("--force=")) block("force push is not allowed. Use 'git push --force-with-lease' on your own branch.");
      if (t === "--no-verify") block("--no-verify skips the repo's git hooks. Fix the failing check instead.");
      if (!t.startsWith("-")) positional.push(t);
    }
    const refspecs = positional.slice(1); // first positional is the remote
    for (const r of refspecs) {
      if (r.startsWith("+")) block(`force push via '${r}' is not allowed. Use --force-with-lease on your own branch.`);
      const dst = r.split(":").pop().replace(/^refs\/heads\//, "");
      if (PROTECTED.includes(dst)) block(`pushing to protected branch '${dst}'. Push your '<issue>-<slug>' branch to your fork and open a PR.`);
    }
    if (refspecs.length === 0) {
      const b = currentBranch();
      if (b && PROTECTED.includes(b)) block(`you are on protected branch '${b}'. Run 'git switch -c <issue>-<slug> upstream/main' and push that.`);
    }
  }

  if (exe === "git" && w[1] === "commit") {
    for (const t of w.slice(2)) {
      if (["-m", "-F"].includes(t) || t.startsWith("--message") || t.startsWith("--file")) break;
      if (t === "--no-verify" || t === "-n") block("--no-verify skips the repo's git hooks. Fix the failing check instead.");
    }
  }

  if (exe === "gh" && w[1] === "issue" && w[2] === "close") block("don't close issues by hand. Put 'Closes #N' in the PR body; merging closes the issue.");
  if (exe === "gh" && w[1] === "pr" && w[2] === "merge" && w.includes("--admin")) block("admin merges bypass branch protection. Get the required reviews and checks instead.");
}
process.exit(0);
