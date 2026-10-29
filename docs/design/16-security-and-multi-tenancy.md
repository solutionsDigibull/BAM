> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §16. Security and multi-tenancy


| Area              | Design                                                                                                                                                                                                                      |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity          | Keycloak realm`bullm`. Tenants as Keycloak Organizations (O-05). Clients `sbam-admin`, `sbam-fde` (OIDC + PKCE). Roles `super-admin`, `sbam-admin`, `sbam-fde`. Deployed realms (rendered by `render_realm.py`): password policy `length(12)`, not one of the last 5, not the username or email, not on a common-password list; TOTP required for `sbam-admin` and `super-admin` (`--mfa admins`, default), for everyone with `--mfa all` (the FDE policy flag), or off. The committed dev realm keeps both off so the seed users work. Failed logins, password and credential changes and admin changes are recorded as Keycloak events.                     |
| Token handling    | Auth.js server session; access token forwarded by the Next.js proxy; FastAPI validates signature, audience, expiry and`tenant_id`.                                                                                        |
| Authorization     | Token → tenant → portal role → project assignment → state guard. Cross-tenant or unassigned access returns 404.                                                                                                         |
| Isolation         | `tenant_id`/`project_id` on every row; storage paths `/<tenant>/<project>/<workflow>/…`; Cognee dataset per project; LLM prompts built only from one project's data; sandbox VMs per run.                            |
| Uploads           | Allowlist + magic bytes, ≤15 MB, ClamAV before any parsing or LLM use; parsers run with resource limits.                                                                                                                   |
| Generated code    | Protected skeleton, import allowlist, forbidden calls, MyPy strict, Bandit, ClamAV; executed only in CubeSandbox (or local non-certifying container).                                                                       |
| Prompt injection  | Documents are delimited as data; the LLM never triggers state changes; outputs are schema-validated; platform fields (status, provenance) can't be set by LLM output; corrections are recorded and visible.                 |
| Secrets           | `.env` and Docker secrets locally; secret files on VMs (AWS Secrets Manager later); OpenRouter key only in Bifrost; no secrets in ISATVON, manifests or code (DATABASE sources use connection *names*). gitleaks in CI. |
| Data sent to LLMs | Allowed to external providers via OpenRouter (QA O2). Bifrost logs are access-controlled.                                                                                                                                   |
| Immutability      | DB triggers on frozen rows; frozen files read-only (S3 Object Lock later); hash verification on read and nightly.                                                                                                           |
| Audit             | Normal append-only`audit_log` (QA P2): logins, role changes, master edits, uploads, deletes/restores, SOW edits/approvals, ISATVON edits, generate/retry, certification events, exports.                                  |
| Supply chain      | Pinned images by digest, Trivy scans, SBOM (Syft), Renovate/Dependabot.                                                                                                                                                     |
| Network           | Only the reverse proxy is public; Temporal UI, Grafana, Langfuse, PostHog, Keycloak admin and the sandbox host are reachable only over Netbird.                                                                             |

---
[← §15](15-test-data-promptfoo-and-sandbox-design.md)   [Design index](README.md)   [§17 →](17-observability-and-notifications.md)
