# Spike WG-2 (#750): the Bifrost MCP gateway as the registry for certified workflow servers

## Question

The workflow design registers each certified workflow MCP server in Bifrost, and SRIM fetches it from there.
Bifrost is only the model gateway in this repo today. Can it register an MCP server at run time, expose its
tools to a consumer such as SRIM, restrict what each consumer sees, hide a preview-only entry, supersede an
old version, and keep its state across a container recreation? Which hosting option does it support: SBAM
hosts the server and registers its address, or SBAM registers a recipe that something else starts?

## Setup

| Item | Value |
|---|---|
| Bifrost | `maximhq/bifrost:v2.2.6` at the digest `compose/compose.yaml` pins (the same image the stack runs) |
| Where | A throwaway container on its own Docker network and port (`18081`), so the SBAM stack was not touched |
| Auth | `BIFROST_SETUP_TOKEN` set to a generated token (see "Setup token" below) |
| Toy server | FastMCP over streamable HTTP with three tools shaped like a workflow: `MS01_1__action`, `MS01_1__check`, `MS01_2__action` |
| Docs read | docs.getbifrost.ai: MCP gateway, connecting to servers, virtual keys |

## Findings

| ID | Scenario | Result |
|---|---|---|
| F1 | Register a server over HTTP: `POST /api/mcp/client` with `connection_type: http` | Works. Bifrost connects, lists the tools with their JSON schemas, and reports the client as `healthy`. |
| F2 | Client name rules | The name may not contain spaces or hyphens (the docs say ASCII, no hyphens). Bifrost derives an endpoint slug (`wf_toy` becomes `wf-toy`). Tool names a consumer sees are `<client name>-<tool name>`, for example `wf_toy-MS01_1__action`. Workflow server names must therefore use underscores. |
| F3 | Consumer endpoint | Bifrost is itself an MCP server at `POST /mcp` (JSON-RPC) and `GET /mcp` (SSE). A consumer connects to one URL and sees every tool it is allowed to see. |
| F4 | Consumer auth | `/mcp` answers 401 without a **virtual key** (`Authorization: Bearer <vk>`, `x-bf-vk` or `x-api-key`). A client registered with `allow_on_all_virtual_keys: false` (the default) is invisible to every key that is not granted it. |
| F5 | Scoped access | A virtual key created with `mcp_configs: [{mcp_client_name, tools_to_execute}]` sees only those tools. With a key limited to the two `MS01_1` tools, `tools/list` returned exactly those two. Calling `MS01_2__action` with that key was refused: `MCP tool ... is not allowed for virtual key`. |
| F6 | A tool call through the gateway | `tools/call` for `wf_toy-MS01_1__action` with `customer_country: IN` returned `{"status":"Supported"}`. The full chain works: consumer, Bifrost, registered server. |
| F7 | A preview-only entry hidden from SRIM | A second entry `wf_toy_preview` granted only to a different virtual key was listed and callable for that key. The SRIM-style key got `not allowed` for it. A preview entry can therefore be registered for the FDE only and never seen by SRIM. |
| F8 | Supersede an old version | `PUT /api/mcp/client/{id}` with `disabled: true` turns an entry off (`state: disabled`), keeps it listed and survives a restart. This matches the "supersede, never revoke" rule (C-31). |
| F9 | Remove an entry | `DELETE /api/mcp/client/{id}` works. The first call returned `500 database is locked` while other writes were running; the retry succeeded. Callers must retry on this error. |
| F10 | Persistence | Registrations live in `/app/data/config.db` (SQLite). A recreated container **without** a volume lost all of them. With a named volume on `/app/data` they survived recreation. The SBAM stack's Bifrost mounts only `config.json` read-only into `/app/data`, so `config.db` sits in the container layer today and would be lost on `docker compose down` or an image update. |
| F11 | Health reporting | The `state` field reports `healthy`, `disabled` and so on, but it lagged: eight seconds after the toy server was stopped it still said `healthy`. SBAM must run its own health check on a registered server rather than trust this field alone. |
| F12 | The command (stdio) transport | Bifrost starts the command inside its own container. That image has no Python, Node, `uv` or Docker, so it cannot start our generated Python servers. Only HTTP fits a container per workflow. |
| F13 | Management API auth | With `BIFROST_SETUP_TOKEN` set, `/api` is locked (401) until the token is sent in `X-Bifrost-Setup-Token` or dashboard auth is enabled. Without a token and without dashboard auth, `/api` answers 403. The consumer endpoint `/mcp` is separate and uses virtual keys. |
| F14 | OpenAPI connection type | Bifrost can also register an OpenAPI document and serve one tool per operation. Not needed for generated servers, but it could expose existing REST services as tools later. |

## Answers

| Question | Answer |
|---|---|
| Can Bifrost register an MCP server at run time? | Yes, over HTTP, through its management API. |
| Can SRIM consume the registered tools? | Yes, through `/mcp` with a virtual key scoped to the entries and tools it may use. |
| Hosting: SBAM hosts, or SBAM registers a recipe? | Bifrost registers an **address** (HTTP or SSE). It cannot start our servers. The container must be running somewhere reachable. A "launch recipe" therefore has to be executed by SRIM or operations first, and only then registered. Whoever starts the container must register its address. |
| Can a preview entry be hidden from SRIM? | Yes, with a separate entry granted only to the FDE's virtual key. |
| Superseding a version? | Yes, by disabling the old entry (it stays listed). |
| Is it production-ready as it is configured here? | Not yet. See "Needed before production". |

## Recommendation

Use Bifrost as the registry and as the MCP gateway for SRIM, over **HTTP**. Keep a small table of registered
workflows in SBAM as the source of truth (which version, which digest, certification id), and treat Bifrost
as the place consumers fetch from. SBAM can rebuild every entry from its table, so a lost `config.db` is a
recoverable fault, not a data loss.

Naming: register each workflow as `wf_<short id>_v<major>_<patch>` (underscores only). A new version is a new
entry; the previous entry is disabled, never deleted.

Access: one virtual key per consumer. SRIM's key is granted only certified production entries. The FDE's
preview key is granted only that project's preview entry. Neither key can see the other's entries.

ADR: the decision is recorded in the ADR of WG-3 (#751), which follows this spike.

## Needed before production

1. **A data volume on `/app/data`** (or Bifrost's Postgres store) so registrations survive recreation (F10).
2. **Dashboard authentication enabled**, and the setup token kept only for first set-up. The token is a secret:
   an environment variable or Docker secret, never committed, never printed. Locally it goes in your own
   `.env`; production uses a Docker secret.
3. **Virtual key enforcement** on for the MCP endpoint (it is, for `/mcp`), with one key per consumer.
4. **TLS** in front of Bifrost, and a network policy so only SRIM, the workers and the FDE preview route can
   reach `/mcp` and the management API.
5. **Retry on `database is locked`** in the registration code, and a periodic reconcile that compares SBAM's
   table with Bifrost's list (F9, F11).
6. **Our own health check** for each registered server (F11).
7. **Rotation** of the setup token and the virtual keys.

## What this spike did not test

- Per-user OAuth or header modes (not needed: consumers authenticate with a virtual key).
- Code mode and agent mode (Bifrost runs the model loop there; SBAM and SRIM do not use it).
- Many registered servers at once, and the cost of a large tool list.
- The SSE transport (HTTP was enough).
- Inference auth (`disable_auth_on_inference`). Setting the setup token on the **running stack's** Bifrost was
  deliberately not tried, because it locks `/api` and the effect on the model calls was not verified. WG-12
  must verify that LLM calls keep working before wiring the token into `compose/compose.yaml`.
- `compose/.env.example` was not changed. A documented, commented placeholder `BIFROST_SETUP_TOKEN=` belongs
  there when WG-12 wires the compose service; editing that file was not possible in this session.

## Reproduce

1. Start a throwaway Bifrost: `docker run -d -p 127.0.0.1:18081:8080 -e BIFROST_SETUP_TOKEN=<generated> <the pinned image>`.
2. Start any FastMCP server with `transport="http"` on the same Docker network.
3. Register it: `POST /api/mcp/client` with header `X-Bifrost-Setup-Token`, body
   `{"name":"wf_toy","connection_type":"http","connection_string":"http://<host>:8000/mcp","auth_type":"none","tools_to_execute":["*"]}`.
4. Create a key: `POST /api/governance/virtual-keys` with `mcp_configs: [{"mcp_client_name":"wf_toy","tools_to_execute":[...]}]`.
5. Call it: `POST /mcp` with `Authorization: Bearer <virtual key>`, methods `initialize`, `tools/list`, `tools/call`.

## Consequences for the other issues

| Issue | Effect |
|---|---|
| WG-3 (ADR) | Record Bifrost as the registry over HTTP, names with underscores, supersede by disabling. |
| WG-4 (contracts) | The manifest's gateway value becomes Bifrost; a server entry stores the registry name and version. |
| WG-12 (register) | Needs the volume, retry on the lock error, a reconcile job, and the hosting decision (the address must exist before registering). |
| WG-17 (preview) | A preview-only entry and key per FDE session, removed with the preview. |
| WG-18 (Commit) | Registers the entry, grants the SRIM key, disables the superseded entry. |
