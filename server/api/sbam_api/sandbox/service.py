"""Reading what a sandbox run left behind (E13.2).

A run is found through the tenant- and project-scoped repository, and must belong to the Workflow in
the URL through its code attempt. Another tenant's run, an unassigned project's, another Workflow's,
an unknown id and a run with nothing stored yet are all the same 404: nothing says which.
"""

import uuid

from sqlalchemy.orm import Session

from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.core.conventions import not_found
from sbam_api.db.repository import TenantContext
from sbam_api.sandbox.repository import SandboxRunRepository


def logs_key(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, run_id: uuid.UUID
) -> str:
    """The storage key of the run's logs, or 404."""
    run = SandboxRunRepository(session, ctx).get(run_id)
    if run is None:
        raise not_found()
    attempt = CodeAttemptRepository(session, ctx).get(run.code_attempt_id)
    if attempt is None or attempt.workflow_id != workflow_id or not run.logs_key:
        raise not_found()
    return run.logs_key
