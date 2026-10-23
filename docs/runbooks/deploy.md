# Deploy and roll back a release (E16.4)

Every environment (dev, staging, prod VM) is deployed by `scripts/deploy.sh`. The deploy workflow runs it over SSH through Netbird; an operator can also run it by hand. A release is one set of image digests (`compose/release.env.example`) plus the `compose/` folder of the same commit. Promotion deploys the same files to the next environment.

## On the VM

| Path | What |
|---|---|
| `/opt/sbam/releases/<id>/` | one folder per release: `compose/`, `release.env`, `migrate.log` |
| `/opt/sbam/releases/current` | the id of the release that is running |
| `/opt/sbam/releases/log.jsonl` | one JSON line per attempt: release, env, result, previous release, actor, times, images |
| `/etc/sbam/<env>.env` | the environment's settings (`compose/.env.example`, placeholders for secrets) |
| `/etc/sbam/secrets/` | Docker secret files (`scripts/check-secrets.sh`) |

The Compose project is always `sbam` (`name:` in `compose.yaml`), so every release uses the same data volumes.

## Run it

```bash
sudo SBAM_PROFILES=obs SBAM_DEPLOY_ACTOR=<who> SBAM_ALERT_WEBHOOK_URL=<operator webhook> \
  scripts/deploy.sh --env staging --id v1.4.0 --release release-v1.4.0.env --bundle <checkout of v1.4.0>
```

What it does, in order:
1. **Checks the digests.** Every `SBAM_*_IMAGE` must be `image@sha256:<64 hex>` and not the template's placeholder. If these digests are already running, it does nothing and exits 0. A re-run never recreates a container.
2. **Pulls, then runs `api-migrate`** with the new images. If the migration fails, it stops with exit 1. Nothing else has changed and the old version keeps serving.
3. **Runs `up -d --wait`.** Workers get SIGTERM: they stop polling and give running activities 120 s to finish (`bootstrap.DRAIN`). After `stop_grace_period: 150s`, Docker kills the container. A cancelled activity is retried by the new worker, since activities are idempotent.
4. **Watches the health for 5 min**, checking every 10 s (`WATCH_CHECKS` × `WATCH_INTERVAL`). A bad check is any service unhealthy or restarting, or any container that exited non-zero. Three bad checks in a row mean the release failed; one flap doesn't.
5. **Records the result.**
   - **Healthy:** `current` moves to the new release and the attempt is recorded as `deployed`.
   - **Failed:** it redeploys the previous release with that release's own compose files, records `rolled_back`, posts to `SBAM_ALERT_WEBHOOK_URL` and exits 2.

It never runs `down`, `-v`, a volume command or an Alembic downgrade. Data volumes and frozen artifacts are never touched.

**Migrations must be expand/contract.** A release only adds columns and tables, and its code still works with the previous schema. Drops come in a later release, once no running version reads them. That is what lets a rollback swap only the app images.

## Rollback drill (E16.4-T03)

On staging, deploy a release whose API cannot become ready, for example one with `SBAM_API_IMAGE` pointing at an image whose `/health/ready` returns 503. Expected:
- `deploy.sh` exits 2 within 10 min;
- `cat /opt/sbam/releases/current` is still the previous id;
- `docker compose ps` is healthy;
- the last line of `log.jsonl` is `"result": "rolled_back"`;
- the alert arrived.

Record the run in the release PR.

## Roll back by hand

Deploy the previous release again: `scripts/deploy.sh --id <previous id> --release /opt/sbam/releases/<previous id>/release.env --bundle /opt/sbam/releases/<previous id>`. It takes the same path, and its digests are already pulled.

If `log.jsonl` shows `rollback_failed`, the previous release is unhealthy too. Check `docker compose ps` and the service logs, then escalate to the platform on-call.
