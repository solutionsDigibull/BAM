"""SOW versions (E6.2): writing the version a generation produced.

`record_generation` is what the `generate_sow` activity calls once the model has answered (or
given up). It is idempotent per (run, knowledge version): a retried activity gets the version it
wrote the first time, never a second one.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sbam_contracts.state_machines import Actor, MachineName
from sqlalchemy.orm import Session

from sbam_api.core.conventions import not_found
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.projects.repository import ProjectRepository
from sbam_api.sow.models import Sow, SowSource, SowVersion
from sbam_api.sow.repository import SowRepository, SowVersionRepository
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID, apply_create


def canonical_sha256(structured: dict[str, Any]) -> str:
    """Of the SowDocument as canonical JSON (sorted keys, no spaces), so equal SOWs hash equal."""
    text = json.dumps(structured, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Generation:
    run_id: uuid.UUID
    knowledge_version_id: uuid.UUID
    structured: dict[str, Any] | None  # the validated SowDocument; None when generation failed
    llm_call_id: uuid.UUID | None
    error_reason: str | None = None  # a code, never document text
    parent_version_id: uuid.UUID | None = None  # default: the version that was current


def record_generation(
    session: Session, ctx: TenantContext, gen: Generation, *, correlation_id: str
) -> SowVersion:
    """The run's version, created GENERATED (or GENERATION_FAILED) on the first call; the project's
    `sows` row points at it unless it failed and an earlier version exists. Does not commit."""
    if ctx.project_id is None:
        raise ValueError("record_generation needs a project in the context")
    versions = SowVersionRepository(session, ctx)
    mine: SowVersion | None = versions.by_run(gen.run_id, gen.knowledge_version_id)
    if mine is not None:
        return mine
    if (
        ProjectRepository(session, TenantContext(ctx.tenant_id)).get_for_update(ctx.project_id)
        is None
    ):
        raise not_found()
    sows = SowRepository(session, ctx)
    sow = sows.of_project(for_update=True)
    if sow is None:
        sow = sows.add(Sow(id=uuid7()))
    failed = gen.structured is None
    version = SowVersion(
        id=uuid7(),
        sow_id=sow.id,
        version=versions.next_number(sow.id),
        source=SowSource.GENERATED.value,
        run_id=gen.run_id,
        knowledge_version_id=gen.knowledge_version_id,
        parent_version_id=gen.parent_version_id or sow.current_version_id,
        llm_call_id=gen.llm_call_id,
        structured=gen.structured,
        sha256=None if failed else canonical_sha256(gen.structured or {}),
        error_reason=gen.error_reason,
    )
    event = "generation_failed" if failed else "generated"
    apply_create(
        session,
        ctx,
        version,
        event=event,
        actor=Actor.SYSTEM,
        actor_id=SYSTEM_ACTOR_ID,
        correlation_id=correlation_id,
        idempotency_key=f"{gen.run_id}:{gen.knowledge_version_id}:{MachineName.SOW_VERSION.value}",
    )
    if not failed or sow.current_version_id is None:
        sow.current_version_id = version.id
    return version
