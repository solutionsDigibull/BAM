# 0030 — Project connections and secrets: an encrypted, write-only store, injected only at container start

- Status: Accepted
- Date: 2026-11-17
- Related: issue #764 (WG-16), epic #748, refines [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) (decision 8), [0026](0026-generated-code-threat-model.md), [0016](0016-audit-observability-and-nfrs.md), [0017](0017-authorization-chain.md), [0005](0005-identity-and-tenancy.md); consumers: WG-17 preview (#765), WG-18 Commit (#766)

## Context

A generated workflow calls real services (a ledger API, a helpdesk, a mail provider), so it needs keys. Until now
the rule was only that secrets come from the environment or Docker secrets and never from code. The ISATVON V
block lists secret **names** (`v_variables.secrets`, names only; the validator refuses anything that looks like a
value), the manifest copies the names, and the generated code cannot read the environment (the allowed imports
are `fastmcp`, `json`, `pydantic` and `typing`). Nothing stores a value, and nothing starts a preview or an SRIM
container yet.

[0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) decision 8 says the FDE edits only secrets, in a
Connections panel, stored encrypted per project, write-only, injected only when a container starts, never in
code, manifest, logs or export, outside the artifact hash, and that Commit is blocked until every required
value is set and tested. This ADR makes that concrete. It is the threat model that decision asked for.

**What we protect against.** (1) A value read back through the API, an export, a log line, a trace, a manifest
or an error message. (2) A stolen database or backup (the ciphertexts alone must be useless). (3) One project's
or tenant's ciphertext replayed into another's. (4) The Test connection feature used to reach internal
services (SSRF) or to leak the key to a host the FDE did not name. (5) The generated code, which is untrusted
([0026](0026-generated-code-threat-model.md)), learning a value it was not meant to have.
**What we do not protect against:** an attacker who holds both the database and the master key, or who can run
code in the API or worker process; a legitimate FDE who types a value and later wants to read it (they cannot:
they enter it again).

## Decision

1. **One tenant-scoped table, `project_connections`.** One row per (tenant, project, `name`). Columns:
   `id`, `tenant_id`, `project_id`, `name` (an environment variable name, `^[A-Z_][A-Z0-9_]{0,127}$`),
   `source` (`ISATVON` | `FDE`), `purpose`, `where_to_get`, `required`, `ciphertext` (bytea, null while no
   value is set), `key_id`, `masked_tail`, `value_version` (counts value changes), `test_config` (JSON: URL,
   method, auth scheme; never a value), `test_status`, `tested_value_version`, `last_test_at`,
   `last_test_code`, and the usual `created_at`, `updated_at`, `row_version`, `created_by`, `deleted_at`.
   Unique on (`tenant_id`, `project_id`, `name`). A CHECK keeps `ciphertext`, `key_id` and `masked_tail` all
   null or all set. Access goes through a `TenantScopedRepository` like every other table (there is no
   row-level security in this database; the repository and the 404 rule are the isolation).
   An entry that has no value and no FDE-written metadata has **no row**: the expected list is computed (see 5).
2. **Crypto: AES-256-GCM with the `cryptography` package** (today only a transitive dependency; it becomes a
   direct one). The master key is **never in code or in the database**: `SECRETS_KEYS` (settings) is **one
   keyring**: `key_id:base64(32 bytes)` pairs, comma separated, and the key that encrypts is marked with a
   leading `*` (`*k2:...,k1:...`). There is no separate "active key id" setting: one secret, and a rotation
   is one atomic edit of it. Deployed, the keyring is the Docker secret file `sbam_secrets_keys`, read through
   `SECRETS_KEYS_FILE`; in development it comes from `.env` (`compose/.env.example` holds a marked development
   placeholder). Outside development the process refuses to start with the keyring missing, malformed, with
   no key or several keys marked, a key not 32 bytes, a repeated id, or a `change-me`
   placeholder (the same fail-fast as the other secrets, `core/settings.py`). Each encryption uses a fresh
   random 96-bit nonce, stored in front of the ciphertext (`nonce || ciphertext || tag`). The **AAD** is
   `f"{tenant_id}:{project_id}:{name}"`, so a ciphertext copied to another project or name fails to
   decrypt. A subkey per tenant is derived from the master key with HKDF-SHA256 (`info = tenant_id`), so a
   per-tenant flaw does not become a master-key flaw. A rename is a decrypt and a new encrypt, never a column
   update (the AAD changes). Decryption failure is an internal error that names the secret `name` and the
   `key_id`, never a value.
3. **Key rotation.** `key_id` is stored per row. To rotate: add the new key to the keyring and move the `*`
   to it, deploy (old rows still decrypt), run the re-encrypt command (`python -m
   sbam_api.connections.rekey`: decrypt with the row's key, encrypt with the active one, in small batches,
   idempotent, each row audited as `CONNECTION_REKEYED` with the two key ids), and when no row names the old
   id any more, remove it. A lost key means the values are gone and each must be entered again (they are
   not recoverable by design). The runbook goes in `docs/runbooks/`.
4. **Write-only is the core rule.** No route, no export, no log and no error ever returns a stored value.
   A response has the name, purpose, status, `masked_tail`, timestamps and who, and nothing else. The
   masked tail is the last 4 characters only when the value is at least 16 characters, the last 2 when it is
   at least 8, and empty otherwise (a short secret is not shown at all). The request model holds the value as
   `SecretStr`, so an accidental `repr` or log prints `**********`. The response models have no `value`,
   `secret` or `ciphertext` field; a contract test walks every generated response schema of these routes and
   fails on any such property, and a runtime test saves a value and scans every response body of every route
   for it (WG16-T01). Validation errors return the field path and a fixed message, never the input (the
   handler already uses `msg` only, and the model's validators never put the value in a message).
5. **What is expected, and what is required.** The expected names are the union of `v_variables.secrets` of
   the Workflow's current Sub-Skills (read live, so nothing is cached that can go stale). Each is **required**,
   with its purpose taken from the Sub-Skill that names it (id and name) and "where to get it" empty until the
   FDE writes it. The scan does not read the generated code (it cannot read the environment) and cannot yet
   read repositories (the fetcher, WG-7, is not built); when it does, a name found in a repository's own
   `.env.example` joins as **optional**, `source = REPO`. The FDE may also add an entry of their own
   (`source = FDE`, optional unless they mark it required). The ISATVON contract does not change in this
   issue: giving each secret a description and a required flag would change the V block, the extraction
   prompt and every golden set, and is a decision for the owner (see Consequences).
6. **On save the value is cleaned, then checked.** Leading and trailing spaces, tabs and line breaks are
   removed, then one pair of matching wrapping quotes (`"` or `'`) is removed, then spaces are trimmed again;
   a value with a NUL byte or longer than 8 KiB is refused. An interior line break is kept (a PEM key needs
   it). The answer lists which clean-ups ran (`trimmed`, `unquoted`), never the value. A value that is empty
   after cleaning is refused with `EMPTY_VALUE` for a required name and is stored for an optional one only
   as "cleared". A name that is not valid as a variable name is refused (422). A name that is valid but not
   expected is accepted only as an FDE entry and is flagged `unexpected_name`; if it is within two edits of an
   expected name, or differs from one only in case, the answer says `did_you_mean`.
7. **Test connection is a real call to the service the FDE names, and it reports what happened.** The FDE
   gives, in the entry's `test_config`, an `https` URL, a method (`GET` or `HEAD`) and how the key is sent
   (`BEARER`, `BASIC`, `HEADER` with a name, or `QUERY` with a parameter name). A secret with no remote service
   (a signing key) can be marked `test_mode = NONE`. That is an **explicit FDE action, never a default**: it is
   audited (its own `CONNECTION_TESTMODE_SET` row, from and to), and it counts as tested only because the FDE has
   said, on the record, that the value has no service to test. The call is made by the API process behind an
   **SSRF guard**, built as its own module (`connections/ssrf.py`) with a test for every rule:
   - **https only**; a URL with credentials, a fragment-borne trick or a non-default scheme is refused. The
     value goes in one request header or parameter and nowhere else (not the URL path, not a log).
   - **The host is resolved here and every resolved address must be public.** Refused ranges: loopback
     (127.0.0.0/8, ::1), private (10/8, 172.16/12, 192.168/16), link-local including 169.254.169.254 and
     fe80::/10, carrier-grade NAT 100.64/10, multicast, reserved and unspecified (0.0.0.0, ::), IPv6 unique
     local fc00::/7, and any IPv4-mapped form (`::ffff:a.b.c.d`, judged by the embedded IPv4 address).
   - **Pinned connection.** The request connects to the validated address, sets the TLS server name and the
     `Host` header to the original host name, and checks that the connected peer address equals the pinned
     address. The name is not resolved a second time, so a resolver that answers a public address first and
     a private one next (DNS rebinding) cannot redirect the call.
   - **No redirects**: a 3xx is a result (`REDIRECT`), never followed. A 10 s limit. The response body is
     never read, kept or logged, only the status class.
   - **Results**: `OK`, `UNAUTHORIZED` (HTTP 401 or 403: "the service rejected this key"), `NOT_FOUND`,
     `RATE_LIMITED`, `SERVER_ERROR`, `REDIRECT`, `UNREACHABLE`, `TIMEOUT`, `TLS_ERROR`, `BLOCKED_HOST`. Only
     `OK` marks the value tested (any 4xx other than 401, 403, 404, 410 and 429 is no pass and is reported as
     `UNREACHABLE`). The status is bound to `value_version`: any change of the value or of
     `test_config` makes it `SET` again and needs a new test (WG16-T03).
   - 10 tests a minute per user; every test is audited. Tests mock the transport and the resolver; no test makes
     a real call.
   The API process is where this runs for now. **When the egress worker of WG-14 exists, Test connection
   should move there** (a process with an egress policy of its own) as defence in depth: the guard is the
   control today, and the network is the second one then.
8. **A value is used in exactly one place.** One function, `connections.inject.resolve_env(session, ctx,
   workflow_id, *, keyring, actor_id, purpose, run_id, names=None)`, decrypts for a container start and
   returns `{NAME: SecretStr}` for the launcher; no route calls it. The `workflow_id` is the one of the build
   or preview, never "the newest". The map is **partial**: only declared names (named by that Workflow's
   Sub-Skills, or entries the FDE added to the project) that have a value and are tested (or marked
   `test_mode = NONE`); a gap is never an error, so a preview still starts (`commit_blockers` lists the gaps).
   It writes one `CONNECTION_SECRET_USED` audit row per name returned (the name, who, which run and what for,
   and when; never the value and never the masked tail), skips a name the Workflow does not declare, decrypts
   everything before it audits anything, and logs only counts. The caller (the preview launcher, WG-17, and the promotion at Commit, WG-18) puts
   the dict in the container's environment and nowhere else: not a file in the image, not the manifest, not a
   build argument, not the trace. The sandbox that certifies code keeps its mocks and never receives a value
   ([0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) decision 9, `sandbox/executor.py`).
9. **Never in manifests, export, logs or traces.** The manifest keeps names only (its Rule 10 refuses anything
   that looks like a value). `.env.example` is produced by one pure function from the expected list: each
   line is a name with its purpose as a comment, and `NAME=` with nothing after it. The export package
   (not built yet) calls it. The existing log redaction (`core/logging.redact`) is a second line of defence,
   not the first: it only knows key words, so the first rule is that no code path logs a value. Tracebacks are
   already never written. The audit rows hold the name, the action, the key id and the status, not the tail
   and not the value. WG16-T06 saves a value with an unremarkable name, drives every route, a failing test, a
   rekey and an injection, and scans the captured logs and audit rows for it.
10. **Commit and recertification.** The Commit rule is a function, `connections.service.commit_blockers(
    session, ctx, workflow_id)`, that returns every required name that is missing, set but not tested, or whose
    last test failed, with the reason; it is also read by `GET .../connections/readiness`. **Commit itself does
    not exist yet** (WG-18, #766, builds `WorkflowCommit`; today the pipeline's `freeze` stage runs on its own
    after certification), so this issue delivers the gate and its tests, and #766 calls it and refuses with a
    clear reason. The Connections panel is built with Commit (#766); this issue is the API, the contracts and
    the generated types. A secret change touches no Sub-Skill, manifest, hash or version: values are not part of the
    artifact hash, so changing one needs no recertification and no new version. SRIM gets the same values
    unless the FDE replaces them at Commit; a replaced value is tested before it is accepted (WG-18).
11. **Access.** Routes live under `/projects/{project_id}/...` and use the assigned-FDE guard
    (`active_project`): another tenant's project, an unassigned FDE or a missing project all answer **404**,
    never 403; a role that is not an FDE gets the portal answer the other FDE routes give; a write needs the
    project not archived. Admins cannot read or write a project's connections (least privilege; they never
    needed values). Every route has a row in `rbac_matrix.yaml` and an IDOR test.
12. **Routes.** `GET /projects/{p}/connections` (the merged list: expected names and stored entries, no
    values); `PUT /projects/{p}/connections/{name}/value` (set or replace: body `{value}`; `If-Match` on the
    row version; answers the entry and the clean-up report); `DELETE /projects/{p}/connections/{name}/value`
    (clear); `PUT /projects/{p}/connections/{name}` (an FDE entry's purpose, where to get it, required flag,
    `test_config`); `POST /projects/{p}/connections/{name}/test`; `GET
    /projects/{p}/workflows/{w}/connections/readiness`; `GET /projects/{p}/workflows/{w}/connections/env-example`
    (`.env.example`, names and purposes only). Every write is audited (`CONNECTION_VALUE_SET`,
    `CONNECTION_VALUE_CLEARED`, `CONNECTION_UPDATED`, `CONNECTION_TESTMODE_SET`, `CONNECTION_TESTED`).

### Defaults, pending owner answer

| Item | Default | Where it is used |
|---|---|---|
| Who can enter or test | the assigned FDE only; Admins have no access | decision 11 |
| A secret with no remote service | only an explicit FDE action sets `test_mode = NONE`; audited; counts as tested | decision 7 |
| Test connection runs in | the API process behind the SSRF guard; it should move to the WG-14 egress worker when that exists | decision 7 |
| Required vs optional | every name in the ISATVON V block is required; the FDE's own entries are optional unless marked | decision 5 |
| Test rate limit | 10 per minute per user | decision 7 |
| Value size limit | 8 KiB | decision 6 |

A different answer from the owner becomes a new ADR that supersedes this one for that item.

## Consequences

- **New dependency and settings.** `cryptography` becomes a direct dependency of `sbam-api`; `SECRETS_KEYS`
  joins `compose/.env.example` (a clearly marked development key; the placeholder is refused outside
  development) and, deployed, is the Docker secret `sbam_secrets_keys` (`SECRETS_KEYS_FILE`), listed in
  `compose.deploy.yaml` and `scripts/check-secrets.sh` like every other secret.
- **One migration** (reversible) creates `project_connections`. Downgrade drops the table and its values; the
  values cannot be recovered, which is the point of a secret store.
- **The Test connection button is an outbound call from our network** to an address a user typed. The SSRF
  guard is the control; production must also allow the API's egress only where it should go, and the call
  should move to the WG-14 egress worker when it exists.
- **Not decided here, for the owner.** Whether the ISATVON V block should carry `{name, purpose, required,
  where_to_get}` per secret instead of names (it would make the scan precise but changes a frozen contract and
  the prompts). Decided: no contract change now; a secret with no remote service is vouched for explicitly by the
  FDE; Admins have no access and see no count.
- **What the next issues must do.** WG-17 (#765) calls `resolve_env` to start a preview and shows the secret
  names, never values, in its trace; WG-18 (#766) calls `commit_blockers`, shows each secret masked in the
  Commit dialog, and tests a replaced value before accepting it; the export package calls the `.env.example`
  function. WG-7 (#755) may feed repository-found names to the scan.
- **Issues:** WG-16 secrets (#764), related WG-17 (#765), WG-18 (#766).

## Sources

- The design document of revision 2 (15 November 2026), issues #748 and #764, and the owner's confirmations
  of 15 and 16 November 2026.
- [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) decision 8, [0026](0026-generated-code-threat-model.md),
  `core/logging.py` (redaction), `contracts/isatvon14.py` (`SECRET_NAME`, `SECRET_VALUE`, Rule 10).
