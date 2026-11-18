"""The one place a stored value is decrypted for use (ADR 0030, decision 8).

`resolve_env` returns `{NAME: SecretStr}` for a container start (the preview, WG-17; SRIM at Commit,
WG-18). No route calls it and no response model can carry what it returns. The caller puts the
mapping in the container's environment and nowhere else: not a file, not the manifest, not a build
argument, not the trace.

- **Declared names only.** A name is declared when the Workflow's Sub-Skills name it (read live from
  the explicit `workflow_id` of the build or preview, never "the newest") or the FDE added it as
  an entry of the project. Any other name asked for is skipped: nothing is over-provisioned.
- **Partial, never an error for a gap.** Only entries that have a value and are tested (or that
  the FDE marked `test_mode = NONE`) are returned, so a preview still starts and the app shows the
  missing ones (`commit_blockers` lists them).
- **Audited.** One `CONNECTION_SECRET_USED` row per name returned: the name, who, which run and what
  for, and when (the row's timestamp). Never the value, never the masked tail. The log gets counts.
"""

import logging
import uuid
from collections.abc import Collection

from pydantic import SecretStr
from sbam_contracts.connections import ConnectionSource, ConnectionStatus
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.connections.crypto import KeyRing
from sbam_api.connections.repository import ConnectionRepository
from sbam_api.connections.service import ENTITY, expected_for_workflow, project_of, status_of
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext

log = logging.getLogger(__name__)

SECRET_USED = "CONNECTION_SECRET_USED"  # noqa: S105 - an audit action name, not a secret


def resolve_env(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    *,
    keyring: KeyRing,
    actor_id: str,
    purpose: str,
    run_id: uuid.UUID | str,
    names: Collection[str] | None = None,
) -> dict[str, SecretStr]:
    """`names=None`: every declared, set and tested entry. A `DecryptionError` (names the secret and
    the key id, never a value) stops the call before anything is audited."""
    scan = expected_for_workflow(session, ctx, workflow_id)
    rows = {row.name: row for row in ConnectionRepository(session, ctx).all_rows()}
    declared = {e.name for e in scan.names} | {
        n for n, r in rows.items() if r.source != ConnectionSource.ISATVON.value
    }
    wanted = declared if names is None else declared & set(names)
    project_id = project_of(ctx)
    out: dict[str, SecretStr] = {}
    used: list[tuple[uuid.UUID, str]] = []
    for name in sorted(wanted):
        row = rows.get(name)
        if row is None or status_of(row) is not ConnectionStatus.TESTED:
            continue
        sealed = row.sealed()
        if sealed is None:
            continue
        out[name] = keyring.open(ctx.tenant_id, project_id, name, sealed)
        used.append((row.id, name))
    for row_id, name in used:
        record(
            session,
            tenant_id=ctx.tenant_id,
            actor_id=actor_id,
            action=SECRET_USED,
            entity_type=ENTITY,
            entity_id=str(row_id),
            after={"name": name, "purpose": purpose, "run_id": str(run_id)},
            correlation_id=correlation_id_var.get() or "unknown",
        )
    session.commit()
    skipped = len(set(names) - set(out)) if names is not None else len(declared) - len(out)
    log.info("connections resolved: %d returned, %d not (purpose=%s)", len(out), skipped, purpose)
    return out
