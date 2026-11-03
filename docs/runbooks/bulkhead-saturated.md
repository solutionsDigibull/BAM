# An API dependency bulkhead is saturated

Alert: **An API dependency bulkhead has been saturated for 5 minutes** (`sbam-bulkhead-saturated`, warning). Added by #609 (audit N-02b).

## Symptoms
- `GET /api/v1/health/ready` lists the bulkhead as `saturated` (the status code does not change).
- Calls that need that dependency answer 503 at once: `SCAN_UNAVAILABLE` (clamd, uploads of masters and documents), `IDP_UNAVAILABLE` (Keycloak admin calls, and a JWKS fetch on a cold cache or an unknown key id).
- Everything else keeps working: that is what a bulkhead is for.

## Impact
Only the operations behind that dependency. A bulkhead holds at most 8 request threads per dependency (`core/bulkhead.py`), so a hung dependency can no longer take the API's 40-thread pool and an unrelated `GET /me` is not queued behind it.

## Diagnosis
1. The alert label `bulkhead` names it: `clamd`, `keycloak_admin` or `keycloak_jwks`. The same value is the gauge `sbam_bulkhead_saturated{bulkhead=...}` in Grafana.
2. Check the dependency itself: `make ps` and `make logs s=clamav` (or `keycloak`); for clamd, the ClamAV signature and scan dashboards.
3. In the traces (Tempo), look at the slow spans of the failing routes: they end at the dependency's call.

## Fix
- Restore the dependency (restart it, or wait out its start-up). The bulkhead frees itself as the in-flight calls finish or time out (3 to 5 s).
- Do not restart the API for this: the cap exists so a hung dependency stays contained.

## Verification
`/health/ready` shows the bulkhead `ok` again and the alert resolves within a few minutes.

## Why a saturated bulkhead does not make a replica unready
Taking a replica out of rotation would send its traffic to the others, which share the same hung dependency and would saturate in turn. The alert is the signal; readiness stays about whether the replica can serve at all.

## Escalation
Platform team (D1). If the dependency is Keycloak, also check the identity runbook and the realm's own health.
