# Runbooks

Operational procedures, written by issue E16.6. Planned runbooks: stuck Temporal workflow · CubeSandbox host down · OpenRouter/LLM outage · ClamAV signature failure · disk full · restore from backup · rollback a deployment.

- [Prerequisites: access and hosts](prerequisites.md) (E0.14): GHCR, OpenRouter key, KVM host, dev VM, Netbird, plus the team access checklist.
- [Deploy and roll back a release](deploy.md) (E16.4): `scripts/deploy.sh`, the release folders and log, the rollback drill.
- [Sandbox: the CubeSandbox host and the local runner](sandbox-host.md) (E13.2): configuring the test worker's sandbox, what the host must provide, why testing is blocked, the orphan reaper.
- [Backups and restore](backup-restore.md) (E16.5): nightly encrypted, append-only backups, the backup host, failed backups, the restore drill.

Runbook structure: **Symptoms** · **Impact** · **Diagnosis** (commands, dashboards) · **Fix** · **Verification** · **Escalation**.
