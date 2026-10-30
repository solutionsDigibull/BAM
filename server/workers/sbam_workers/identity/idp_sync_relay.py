"""`RelayIdpSync` (A-13, #443): bring Keycloak in line with `users` rows saved but not synced.

A role change or deactivation commits first and Keycloak is told afterwards
(`sbam_api.identity.idp_sync`). If Keycloak was down in between, the row carries
`idp_sync_pending_at`. This Temporal Schedule (every minute, created by an ingest worker on start,
like the other schedules) converges the ones older than a minute. Converging is idempotent. Without
`KEYCLOAK_ADMIN_CLIENT_SECRET` the activity does nothing (and says so in the log).
"""

import logging
from datetime import datetime, timedelta

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel
    from sbam_api.identity.idp_sync import converge_pending
    from sbam_api.identity.keycloak_admin import KeycloakUsers
    from sbam_contracts.runs import TaskQueue

    from sbam_workers import mirror
    from sbam_workers.registry import activity_for, startup_for, workflow_for
    from sbam_workers.settings import WorkerSettings

RELAY_WORKFLOW = "RelayIdpSync"
RELAY_SCHEDULE_ID = "relay-idp-sync"
RELAY_EVERY = timedelta(minutes=1)
log = logging.getLogger("sbam_workers.identity.idp_sync_relay")


class RelayResult(BaseModel):
    synced: int = 0
    failed: int = 0
    skipped: str | None = None


def users_from(settings: WorkerSettings) -> KeycloakUsers | None:
    secret = settings.keycloak_admin_client_secret
    if secret is None or settings.keycloak_internal_url is None:
        return None
    realm_url = str(settings.keycloak_internal_url).rstrip("/")
    return KeycloakUsers(realm_url, settings.keycloak_admin_client_id, secret.get_secret_value())


@activity_for(TaskQueue.INGEST)
@activity.defn(name="relay_idp_sync")
def relay_idp_sync(as_of: datetime) -> RelayResult:
    settings = WorkerSettings()  # type: ignore[call-arg]
    users = users_from(settings)
    if users is None:
        log.info("keycloak sync not relayed: KEYCLOAK_ADMIN_CLIENT_SECRET / URL not set")
        return RelayResult(skipped="not configured")
    synced, failed = converge_pending(mirror.sessions(), users, as_of)
    if synced or failed:
        log.info("keycloak sync relayed: synced=%s failed=%s", synced, failed)
    return RelayResult(synced=synced, failed=failed)


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=RELAY_WORKFLOW)
class RelayIdpSync:
    @workflow.run
    async def run(self) -> RelayResult:
        result: RelayResult = await workflow.execute_activity(
            relay_idp_sync,
            workflow.now(),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=5)),
        )
        return result  # a user Keycloak could not take is retried by the next tick


def relay_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            RELAY_WORKFLOW, id=RELAY_SCHEDULE_ID, task_queue=TaskQueue.INGEST.value
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=RELAY_EVERY)]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,  # a slow run is never doubled
            catchup_window=timedelta(minutes=5),  # after an outage, one run, not a backlog
        ),
    )


@startup_for(TaskQueue.INGEST)
async def ensure_relay_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition (idempotent)."""
    wanted = relay_schedule()
    try:
        await client.create_schedule(RELAY_SCHEDULE_ID, wanted)
        log.info("created schedule %s (every %s)", RELAY_SCHEDULE_ID, RELAY_EVERY)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(RELAY_SCHEDULE_ID).update(converge)
