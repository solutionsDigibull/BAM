"""E4.9 against the Compose stack: the PurgeDeleted Schedule exists on the real Temporal server.

The time-skipping test server in tests/temporal has no Schedule service, so this is the only place
the real `CreateSchedule` / describe round trip is checked. It only reads and converges the schedule
the ingest worker already created; it never deletes it.
"""

import asyncio

import pytest
from sbam_api.temporal.client import connect
from sbam_contracts.documents import PURGE_CRON, PURGE_DELETED, PURGE_SCHEDULE_ID
from sbam_workers.ingest.purge_schedule import ensure_purge_schedule
from temporalio.client import ScheduleActionStartWorkflow

from .conftest import port

pytestmark = pytest.mark.integration

TEMPORAL = f"127.0.0.1:{port('TEMPORAL_PORT', 7233)}"


def test_e4_9_the_purge_schedule_is_daily_at_0200_utc() -> None:
    async def scenario() -> None:
        client = await connect(TEMPORAL, "sbam")
        await ensure_purge_schedule(client)  # what every ingest worker does on start; idempotent
        await ensure_purge_schedule(client)
        description = await client.get_schedule_handle(PURGE_SCHEDULE_ID).describe()
        schedule = description.schedule
        assert schedule.spec.cron_expressions == [PURGE_CRON]
        action = schedule.action
        assert isinstance(action, ScheduleActionStartWorkflow)
        assert action.workflow == PURGE_DELETED and action.task_queue == "sbam-ingest"

    asyncio.run(scenario())
