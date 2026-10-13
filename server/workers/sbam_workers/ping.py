"""The sample `ping` activity, served on every queue (E1.7). It heartbeats like real long activities
do, so a worker that dies mid-activity is detected by the heartbeat timeout and the activity is
retried on another worker (E1.7-T05).
"""

import asyncio
import logging

from sbam_api.core.logging import correlation_id_var, project_id_var, tenant_id_var
from sbam_contracts.runs import PingResult, RunInput, TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for

PING_STEPS = 3
PING_STEP_SECONDS = 0.1
log = logging.getLogger("sbam_workers.ping")


@activity_for(*TaskQueue)
@activity.defn(name="ping")
async def ping(inp: RunInput) -> PingResult:
    correlation_id_var.set(inp.correlation_id)
    tenant_id_var.set(str(inp.tenant_id))
    project_id_var.set(str(inp.project_id))
    info = activity.info()
    for step in range(PING_STEPS):
        activity.heartbeat(step)
        await asyncio.sleep(PING_STEP_SECONDS)
    log.info("ping answered on %s (attempt %d)", info.task_queue, info.attempt)
    return PingResult(task_queue=TaskQueue(info.task_queue), attempt=info.attempt)
