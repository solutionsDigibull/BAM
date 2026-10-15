"""Queries on the runtime registry (platform-wide, see models.py: no tenant scope by design)."""

from datetime import UTC
from enum import StrEnum

from sbam_contracts.runtime_registry import RuntimePackage, RuntimeRegistry
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from sbam_api.registry.models import RuntimeRegistryPackageRow, RuntimeRegistryRow

SYNC_LOCK_KEY = 0x5B4D_7267  # pg_advisory_xact_lock key: one registry sync at a time


class SyncOutcome(StrEnum):
    CREATED = "created"
    UNCHANGED = "unchanged"


class RegistryConflictError(ValueError):
    """The same image digest was synced before with different content: refuse, never overwrite."""


def upsert_registry(session: Session, registry: RuntimeRegistry, activate: bool) -> SyncOutcome:
    """Store `registry` (keyed by its image digest) inside the caller's transaction.

    Same digest + same content -> no-op; same digest + other content -> RegistryConflictError.
    `activate` makes it the one active registry. Concurrent syncs queue on an advisory lock.
    """
    if registry.image_digest is None:
        raise ValueError("a registry without image_digest can't be synced")
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SYNC_LOCK_KEY})
    row = session.scalars(
        select(RuntimeRegistryRow).where(RuntimeRegistryRow.image_digest == registry.image_digest)
    ).one_or_none()
    outcome = SyncOutcome.UNCHANGED
    if row is None:
        row = RuntimeRegistryRow(
            image_digest=registry.image_digest,
            python_version=registry.python_version,
            content_sha256=registry.content_sha256,
            generated_at=registry.generated_at,
        )
        session.add(row)
        session.flush()
        session.add_all(
            RuntimeRegistryPackageRow(
                registry_id=row.id,
                name=p.name,
                version=p.version,
                top_level_modules=list(p.top_level_modules),
                allowed=p.allowed,
            )
            for p in registry.packages
        )
        outcome = SyncOutcome.CREATED
    elif row.content_sha256 != registry.content_sha256:
        raise RegistryConflictError(f"{registry.image_digest} was synced with different content")
    if activate and not row.is_active:
        session.execute(
            update(RuntimeRegistryRow)
            .where(RuntimeRegistryRow.is_active.is_(True))
            .values(is_active=False)
        )
        session.flush()
        row.is_active = True
    session.flush()
    return outcome


def active_registry(session: Session) -> RuntimeRegistry | None:
    row = session.scalars(
        select(RuntimeRegistryRow).where(RuntimeRegistryRow.is_active.is_(True))
    ).one_or_none()
    if row is None:
        return None
    packages = session.scalars(
        select(RuntimeRegistryPackageRow)
        .where(RuntimeRegistryPackageRow.registry_id == row.id)
        .order_by(RuntimeRegistryPackageRow.name)
    )
    return RuntimeRegistry(
        image_digest=row.image_digest,
        python_version=row.python_version,
        packages=[
            RuntimePackage(
                name=p.name,
                version=p.version,
                top_level_modules=p.top_level_modules,
                allowed=p.allowed,
            )
            for p in packages
        ],
        generated_at=row.generated_at.replace(tzinfo=row.generated_at.tzinfo or UTC),
        content_sha256=row.content_sha256,
    )


def registry_digests(session: Session) -> list[tuple[str, bool]]:
    """(image_digest, is_active) of every stored registry, oldest first (tests and ops)."""
    rows = session.execute(
        select(RuntimeRegistryRow.image_digest, RuntimeRegistryRow.is_active).order_by(
            RuntimeRegistryRow.synced_at, RuntimeRegistryRow.id
        )
    )
    return [(digest, active) for digest, active in rows]
