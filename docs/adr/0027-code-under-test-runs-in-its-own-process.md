# 0027 — The code under test runs in a child process of its own; the runner never imports it

- Status: Accepted
- Date: 2026-11-03
- Related: issue #625, refines [0023](0023-sandbox-adapter.md) (decision 7, the in-VM runner), [0012](0012-testing-sandbox-and-certification.md), [0026](0026-generated-code-threat-model.md)

## Context

The in-VM runner imported the generated bundle into its own process, next to the loop harness that records
each case's trace and the code that writes `outputs.json`. Documents reach the code generator through
prompts, so adversarial code in a bundle is a realistic threat, and code in the runner's process can patch
`run_case`, return a canned trace, replace `write_outputs` or write the outputs file itself. The platform
recomputes each verdict from the trace, but the trace came from the process under test, so that check
caught mistakes, not forgery. A bundle that passes its tests passes LOOP_TESTS and can be certified and
frozen.

The sandbox container (and, in a certifying run, the CubeSandbox VM) runs the runner as one unprivileged
user (10001) with every capability dropped. A second user for the code under test would need a capability
the container does not have (`CAP_SETUID`) or a runner that starts as root; neither is acceptable to add
silently, and the VM is the trust boundary for anything that is root inside it (out of scope here).

## Decision

1. **Two processes, one trusted.** The runner (`run_cases.py`) holds the harness, the database connection it
   judges with and the outputs file. The bundle's FastMCP server runs in a **child process per case**
   (`child_server.py`), started with its own session, a minimal environment, `/dev/null` for stdin, stdout
   and stderr, and the memory cap applied after the libraries are loaded. The runner never imports the
   bundle.
2. **The channel is two inherited pipes**, one JSON object per line, size-capped. The child answers
   `parameters` and `call`; the first line it writes is a handshake that says whether the bundle imported.
   The runner reads a reply as "this is what the tool answered" and nothing more: a malformed, oversized or
   missing reply is the case's failure.
3. **The runner reads state itself.** Fresh state comes from the literal `QUERIES = {name: "SELECT ... %s"}`
   the assembler writes (read with `ast.literal_eval`, never executed), run on the runner's own connection
   with a statement timeout and the same one-`%s` SELECT check `state.read` applies. A child that lies about
   state cannot change what the harness decides on.
4. **Without a second uid, the runner closes the other doors:**
   - it marks itself non-dumpable (`PR_SET_DUMPABLE 0`): no ptrace, no `/proc/<pid>/mem` for a same-user
     child, and `process_vm_writev` is refused;
   - it is a child subreaper, so a process the code under test orphans comes back to it;
   - before `outputs.json` is written, every process below the runner that is not the database's tree is
     frozen, killed and reaped until none is left (a late writer cannot outlive that point), and `out/` is
     removed and made again, so a planted file, directory or symlink is gone;
   - `/opt/sbam` (the runner, `child_server.py`, the harness and the agent) is root-owned and not writable
     by the sandbox user (the base image owned `/opt/sbam/harness` as 10001).
5. **A file left by a runner that did not exit cleanly is not evidence.** Same user means the bundle can
   kill the runner after writing a perfect `outputs.json`. `classify` treats the outputs as absent unless
   the runner exited 0 and was not cut off; the run ends as `INFRA_ERROR` / `RUNNER_FAILED`. The bundle
   cannot make the runner exit 0 with anything but the runner's own file.
6. **A tool that hangs or overruns ends its case by killing its process group**, replacing the old
   "abandon the thread" rule: nothing is left behind to wake up and touch state. Timeouts and OOM keep
   their categories (`MemoryError` is marked on the server side as before; a process that dies is a
   `RUNTIME_ERROR`).

## Consequences

- An attack in `tests/integration/test_e13_2_forgery.py` (patching the harness, opening the runner's memory
  or the harness files, a forged outputs file with a daemon that keeps rewriting it, killing the runner,
  replacing `out/`) never ends as a pass. These tests need Docker and run on a developer's machine until
  the container tests run in CI (#629).
- **Not covered, and why.** (a) The code under test still reads `request.json` (it holds the expected
  outcomes) and can open the case database as the same database user: separating them needs a second uid or
  a restricted database role, which is a follow-up if the VM template (#122) gives the runner a privilege to
  drop. Synthetic data only is the mitigation today. (b) A process that is root in the VM is the VM's
  boundary. (c) Hash-pinning `outputs.json` with a key the VM cannot read is a possible second layer once the
  host exists.
- The runner's CPU and memory figures now include its children (`RUSAGE_CHILDREN`), so the code under test
  still counts toward them.
