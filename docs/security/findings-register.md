# Security findings register

E15.3 (#137). Every security finding from the suites in `server/tests/security`, the scanners (Trivy,
gitleaks, Bandit, ZAP baseline) and reviews, with its severity, owner and status.

**Release rule:** no release (a production promotion, #148) while a **Critical** or **High** finding
is `open`. `accepted` means a documented exception with an end date (for Trivy:
`.trivyignore.yaml`, whose entries CI stops honouring after `expired_at`). An expired acceptance
counts as `open`.

**How to use it:** add a row when a suite, a scanner or a review finds something; never delete a row.
Change `status` to `fixed` with the PR, to `accepted` with the exception and its end date, or to
`false positive` with the reason. Severity is the scanner's, or for a review finding: Critical
(tenant data exposed or code execution), High (a control bypassed), Medium (defence in depth
missing), Low (hygiene).

Last reviewed: 2026-11-20.

## Open and accepted

| ID | Finding | Source | Severity | Owner | Status | Tracking |
|---|---|---|---|---|---|---|
| SF-01 | CVE-2023-45853 zlib1g (minizip overflow), no fixed Debian package; 7 images | Trivy | Critical | D1 | accepted until 2027-02-18 | `.trivyignore.yaml` |
| SF-02 | CVE-2025-7458 libsqlite3-0 integer overflow, no fixed package; 6 images | Trivy | Critical | D1 | accepted until 2027-02-18 | `.trivyignore.yaml` |
| SF-03 | CVE-2026-58016 libglib2.0-0 GDBus introspection, no fixed package; sbam-worker | Trivy | Critical | D1 | accepted until 2027-02-18 | `.trivyignore.yaml` |
| SF-04 | CVE-2026-6653 libxml2 crafted-XML DoS, no fixed package; skill-runtime | Trivy | Critical | D3 | accepted until 2027-02-18 | `.trivyignore.yaml` |
| SF-05 | High findings in the Debian bookworm base images: about 100 to 115 per Python image have no fixed package, 4 to 13 per image have one. The CI gate is Critical only. | Trivy (CI run 44687992262) | High | D1 | open | Rebuild on a newer base image; then gate High with the same exception file |
| SF-06 | A user holding both `sbam-admin` and `sbam-fde` gets 403 `WRONG_PORTAL` on every FDE call after switching portals, because the session keeps the admin client's token | Review of #917 | Medium | D1 | open | #927 |
| SF-07 | Bandit Medium: B108 `/tmp` paths in `build/gates/import_gate.py`, `sandbox/cube_backend.py` (2), `settings.py`; B104 bind-all in `staging/egress_proxy.py`. No High. | Bandit 1.9.4 | Medium | file owners | open | Not gating (`sast.yml` gates High) |
| SF-08 | ZAP baseline warnings: Cross-Origin-Resource-Policy header missing (90004) x3, non-storable content (10049) x8, X-Content-Type-Options missing (10021) x3. No High. | ZAP baseline (run 44630792047) | Low | D1 | open | API security headers |
| SF-09 | A sandbox run with a blocked egress attempt still passes if its cases pass (the attempt is recorded) | `test_e13_2_blocked_egress_in_a_passing_run_does_not_fail_it_but_is_kept` | Medium | D3 | open, decision | #917 |
| SF-10 | A PostgreSQL reserved word (`user`, `select`) has the shape of a table name and passes the ISATVON 1.4 rules; the schema step refuses it, so no SQL is built from it | `test_e15_3_sql_identifier_injection.py` | Low | D3 | open | Check reserved words in `isatvon14._is_table_name` too |

## Fixed

| ID | Finding | Source | Severity | Owner | Fixed by |
|---|---|---|---|---|---|
| SF-F1 | A token from the admin portal client (`azp=sbam-admin`) was accepted on the FDE API: `aud` was checked, `azp` was not | E15.3 token suite (strict xfail in #889) | High | D1 | #917 (`core/auth.py`) |
| SF-F2 | High-entropy test literals tripped gitleaks | gitleaks | Low | D3 | #905 (literals removed from history) |
| SF-11 | The export package was not in the secrets-never-leak check (skipped until #905 merged) | `test_e15_3_secrets_never_leak.py` | Medium | D3 | #917 (zip bytes and every entry checked) |
