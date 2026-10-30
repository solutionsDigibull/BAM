"""`ImportKeycloakEvents` (A-18b, #541): copy Keycloak's security events into `audit_log`.

A Temporal Schedule (every 5 minutes, created by an ingest worker on start, like the other
schedules) starts the workflow, which hands `workflow.now()` to the activity. The work is
`sbam_api.identity.keycloak_events.import_keycloak_events`: idempotent, bounded, and free of
anything a person might have typed into a sign-in form. Without `KEYCLOAK_ADMIN_CLIENT_SECRET` the
activity does nothing (and says so once per run in the log), like tenant creation without it.
A source that could not be read fails the run so it is visible; the next run reads the same window.
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
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel
    from sbam_api.identity.keycloak_events import KeycloakEvents, import_keycloak_events
    from sbam_contracts.runs import TaskQueue

    from sbam_workers import mirror
    from sbam_workers.registry import activity_for, startup_for, workflow_for
    from sbam_workers.settings import WorkerSettings

IMPORT_WORKFLOW = "ImportKeycloakEvents"
IMPORT_SCHEDULE_ID = "import-keycloak-events"
IMPORT_EVERY = timedelta(minutes=5)
log = logging.getLogger("sbam_workers.identity.keycloak_events")


class ImportOutcome(BaseModel):
    read: int = 0
    written: int = 0
    already: int = 0
    suppressed: int = 0
    skipped: str | None = None


def feed_from(settings: WorkerSettings) -> KeycloakEvents | None:
    secret = settings.keycloak_admin_client_secret
    if secret is None or settings.keycloak_internal_url is None:
        return None
    realm_url = str(settings.keycloak_internal_url).rstrip("/")
    return KeycloakEvents(realm_url, settings.keycloak_admin_client_id, secret.get_secret_value())


@activity_for(TaskQueue.INGEST)
@activity.defn(name="import_keycloak_events")
def import_events(as_of: datetime) -> ImportOutcome:
    settings = WorkerSettings()  # type: ignore[call-arg]
    feed = feed_from(settings)
    if feed is None:
        log.info("keycloak events not imported: KEYCLOAK_ADMIN_CLIENT_SECRET / URL not set")
        return ImportOutcome(skipped="not configured")
    correlation = f"keycloak-events-{activity.info().workflow_run_id}"
    with mirror.sessions()() as session:
        done = import_keycloak_events(session, feed, as_of, correlation)
    if done.failed_sources:
        raise ApplicationError(
            f"Keycloak events not readable: {', '.join(done.failed_sources)}",
            type="KeycloakEventsUnreadable",
        )
    return ImportOutcome(
        read=done.read, written=done.written, already=done.already, suppressed=done.suppressed
    )


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=IMPORT_WORKFLOW)
class ImportKeycloakEvents:
    @workflow.run
    async def run(self) -> ImportOutcome:
        outcome: ImportOutcome = await workflow.execute_activity(
            import_events,
            workflow.now(),
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=10)),
        )
        return outcome


def import_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            IMPORT_WORKFLOW, id=IMPORT_SCHEDULE_ID, task_queue=TaskQueue.INGEST.value
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=IMPORT_EVERY)]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,  # a slow run is never doubled
            catchup_window=timedelta(minutes=10),  # the window is read from the last row anyway
        ),
    )


@startup_for(TaskQueue.INGEST)
async def ensure_import_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition (idempotent)."""
    wanted = import_schedule()
    try:
        await client.create_schedule(IMPORT_SCHEDULE_ID, wanted)
        log.info("created schedule %s (every %s)", IMPORT_SCHEDULE_ID, IMPORT_EVERY)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(IMPORT_SCHEDULE_ID).update(converge)
