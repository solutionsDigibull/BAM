# Spike E0.13: Keycloak Organizations + Auth.js + FastAPI JWT

Prototype for issue #31. Write-up: [`docs/spikes/keycloak-auth.md`](../../docs/spikes/keycloak-auth.md) · ADR:
[`docs/adr/0002-keycloak-tenancy.md`](../../docs/adr/0002-keycloak-tenancy.md).

- `docker-compose.yml`: Keycloak 26.8.0 on `localhost:8180`, imports `realm/bullm-realm.json`.
- `api/`: FastAPI `get_principal()` (RS256-only JWKS validation, tenant claim). Routes `/me`, `/tenants-demo/{id}`.
- The web side is the real client: `client/src/auth.ts` and the existing `/login-admin` / `/login-fde` pages.

Local only: admin `admin`/`admin`, every test user's password is `password`.

| User | Org(s) | Role |
|---|---|---|
| `fde-a` | tenant-a | sbam-fde |
| `fde-b` | tenant-b | sbam-fde |
| `fde-ab` | tenant-a, tenant-b | sbam-fde |
| `admin-a` | tenant-a | sbam-admin |
| `root` | — | super-admin |

## Run

```bash
# 1. Keycloak
cd spikes/identity && docker compose up -d --wait

# 2. API (another terminal)
cd spikes/identity/api && uv run uvicorn app:app --port 8100

# 3. Client with Keycloak sign-in (another terminal; Git Bash)
cd client
KEYCLOAK_ISSUER=http://localhost:8180/realms/bullm SBAM_API_URL=http://localhost:8100 \
AUTH_SECRET=$(openssl rand -base64 32) AUTH_TRUST_HOST=true pnpm dev
```

Open http://localhost:3000/login-fde, sign in as `fde-a`, then call `/api/proxy/me` from the browser.

## Tests

```bash
cd spikes/identity/api && uv run pytest        # T01–T05, T07, T08 (+ edge cases), no Keycloak needed
uv run ruff check . && uv run black --check . && uv run mypy
```

Manual steps for T01, T05, T06 and T08 and their results are in the write-up.

`docker compose down` removes everything (no volumes).
