# 0040 — Imports are a suggestion, not a restriction: a short deny list replaces the allowlist and the import evidence

- Status: Accepted
- Date: 2026-11-20
- Related: issue #922 (WG-35), epic #748, refines [0026](0026-generated-code-threat-model.md) and [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) (decisions 2 and 3), touches [0033](0033-ui-bundle-is-part-of-the-certified-workflow.md); the live run of 2026-11-20 (#914)

## Context

ADR 0026 made the import allowlist (the template's `base_imports` plus the top-level modules of the resolved
libraries, `import_allowlist()`) a hard rule in three places: the bodies pre-check, the corrector and the AST gate.
ADR 0028 added `IMPORT_RESOLUTION` (the import gate, #761) to the evidence certification needs.

The live run of 2026-11-20 (real model, real RFQ data) parked all four Sub-Skills in `NEEDS_FDE_REVIEW`: the model
used the standard library `datetime`, the pre-check refused it (`'datetime' is not an allowed import`) and the
corrector could not repair it, because the right fix was a different list, not different code. Almost any real
workflow needs dates, decimals and regular expressions. The owner decided on 2026-11-20 that imports must not be a
restriction.

## Decision

1. **The allowed list is a hint.** `allowed_imports` stays in the `codegen-bodies` and `correct-code` prompts, which
   now say it is a suggestion (`codegen-bodies` 1.4.0, `correct-code` 1.3.0; earlier versions are untouched). The
   model may import any module that is not on the deny list. Standard library: free. Third-party: free.
2. **A short deny list is the only import rule.** `FORBIDDEN_MODULES` in `codegen/bodies.py` holds the modules that
   can leave the sandbox or reach the host, the network or another process: `pickle`, `marshal`, `shelve`, `ctypes`,
   `importlib`, `builtins`, `runpy`, `pkgutil`, `os`, `sys`, `shutil`, `multiprocessing`, `signal`, `pty`, `socket`,
   `socketserver`, `ssl`, `ftplib`, `smtplib`, `telnetlib`, `xmlrpc`, plus the submodules `urllib.request`,
   `http.client` and `http.server` (`urllib.parse` stays free). `subprocess` stays denied unless a declared library
   brings it, as before. One function, `denied_import()`, serves the pre-check and the AST gate, so the two cannot
   drift. The `FORBIDDEN_CALL`, `DYNAMIC_ACCESS` and escape-attribute rules (`eval`, `exec`, `__import__`,
   `os.system`, dunder walks, `json.codecs.sys`) are unchanged. A relative import outside `models`, `state` and
   `state_handoff` is still `IMPORT_NOT_ALLOWED`: that is a layout rule, not an import list.
3. **`IMPORT_NOT_ALLOWED` for a merely unsuggested module is gone.** The corrector therefore never loops on one:
   there is no finding to repair. The enum value stays because stored attempts and findings carry it.
4. **`IMPORT_RESOLUTION` is no longer certification evidence.** New builds do not run the `import_gate` stage (the
   pipeline skips it under `workflow.patched("wg35-no-import-gate")`; a history recorded before the patch still
   replays it, and the activity stays registered for that). `compliance_gates` no longer waits for an import row.
   `REQUIRED_GATES` and the sandbox load no longer ask for it. The `GateName.IMPORT_RESOLUTION` value stays, with the
   database check constraint, so rows and frozen manifests that hold one still read and validate with their hashes
   unchanged; a row on record is ignored by certification and shown by the workbench as information only.
5. **The open-source checks stay exactly as they are:** `REPO_LICENCE`, `DEPENDENCY_VULN`, the repository malware
   scan (ClamAV) and `MCP_COMPLIANCE`. Declared dependencies (what the Sub-Skill or the vendored repository
   declares) are still scanned for vulnerabilities and licences.
6. **The sandbox stays the boundary** (ADR 0026, decision 3). `local-docker` runs still never certify.

## Residual risk, accepted by the owner

- **An undeclared third-party import is not scanned.** A package the model imports but nobody declared gets no
  `DEPENDENCY_VULN` and no `REPO_LICENCE` evidence, because SBAM no longer resolves imports into dependencies.
- **It cannot reach a certified image by that route.** The image holds only declared dependencies, so code that
  imports an undeclared package fails at runtime in the sandbox tests: a failing test the corrector can read, never
  a security finding and never a certified Workflow.
- A syntax or import mistake that the import gate used to catch before the tests now surfaces as a failing test.
- The deny list is a cheap filter, not a security control (ADR 0026): an escape it misses is still contained by the
  sandbox.

## Consequences

- Real workflows can use `datetime`, `decimal`, `re` and the rest of the standard library, and a declared library's
  own modules, without a list edit.
- The workbench evidence panel shows four rows (three per Sub-Skill, one per Workflow) instead of five.
- Prompt hashes changed; the codegen mock fixtures were regenerated, and four synthetic golden correction items that
  imported `requests`, `numpy`, `pandas` and `yaml` (expecting `IMPORT_NOT_ALLOWED`) became denied-module items.
- If the owner later wants undeclared imports resolved again, that is a new ADR that re-adds an evidence gate; the
  enum value and the stage code are still there.
