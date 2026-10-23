# Runbooks

Operational procedures, written by issue E16.6. Planned runbooks: stuck Temporal workflow · CubeSandbox host down · OpenRouter/LLM outage · ClamAV signature failure · disk full · restore from backup · rollback a deployment.

- [Prerequisites: access and hosts](prerequisites.md) (E0.14): GHCR, OpenRouter key, KVM host, dev VM, Netbird, plus the team access checklist.
- [Deploy and roll back a release](deploy.md) (E16.4): `scripts/deploy.sh`, the release folders and log, the rollback drill.

Runbook structure: **Symptoms** · **Impact** · **Diagnosis** (commands, dashboards) · **Fix** · **Verification** · **Escalation**.
