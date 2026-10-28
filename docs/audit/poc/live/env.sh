export DATABASE_URL=postgresql://sbam:auditpw@127.0.0.1:55999/sbam
export STORAGE_ROOT=/tmp/audit-poc/live/storage
export TEMPORAL_HOST=127.0.0.1:59999
export KEYCLOAK_ISSUER=http://kc.audit.local/realms/bullm
export KEYCLOAK_AUDIENCE=sbam-api
export KEYCLOAK_JWKS_URL=http://127.0.0.1:55998/certs
export TUSD_HOOK_SECRET=audit-hook-secret-0123456789abcdef
export STORAGE_SIGNING_KEY=audit-storage-signing-key-0123456789abcdef0123
export SBAM_ENV=development LLM_PROVIDER=mock ENABLE_PING=false LOG_LEVEL=WARNING
