"""Re-encrypt every stored value under the active key (ADR 0030, decision 3).

    python -m sbam_api.connections.rekey [--tenant UUID] [--batch N] [--dry-run]

To rotate: add the new key to the keyring and mark it active (`*`), deploy (old rows still decrypt),
run this, and when `remaining` is 0 remove the old key. Each row is decrypted under the key it
names,
sealed again under the active key (same tenant, project and name, so the same AAD) and audited as
`CONNECTION_REKEYED` with the two key ids. The value is not changed, so its `value_version` and its
test result stay. Idempotent: a row already under the active key is not touched, so a second run
moves nothing. A row that cannot be decrypted (its key is gone) is reported by name and key id and
left alone. Nothing here logs or prints a value.
"""

import argparse
import logging
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.connections.crypto import DecryptionError, KeyRing
from sbam_api.connections.repository import ConnectionRepository, scopes_not_under
from sbam_api.connections.service import ENTITY
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext

log = logging.getLogger(__name__)

REKEYED = "CONNECTION_REKEYED"
BATCH = 100


@dataclass
class RekeyReport:
    moved: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (name, key id): never a value
    remaining: int = 0  # still under another key after the run (failed rows, or a dry run)


def rekey(
    session: Session,
    keyring: KeyRing,
    *,
    tenant_id: uuid.UUID | None = None,
    batch: int = BATCH,
    dry_run: bool = False,
    actor_id: str = "SYSTEM",
) -> RekeyReport:
    report = RekeyReport()
    active = keyring.active_id
    scopes = [
        (tenant, project)
        for tenant, project in scopes_not_under(session, active)
        if tenant_id is None or tenant == tenant_id
    ]
    for tenant, project in scopes:
        repo = ConnectionRepository(session, TenantContext(tenant, project))
        after: uuid.UUID | None = None
        while rows := repo.not_under(active, limit=batch, after=after):
            after = rows[-1].id
            for row in rows:
                if dry_run:
                    report.remaining += 1
                    continue
                current = row.sealed()
                if current is None:
                    continue
                old = current.key_id
                try:
                    plain = keyring.open(tenant, row.project_id, row.name, current)
                except DecryptionError:
                    report.failed.append((row.name, old))
                    report.remaining += 1
                    continue
                sealed = keyring.seal(tenant, row.project_id, row.name, plain)
                row.ciphertext, row.key_id = sealed.ciphertext, sealed.key_id
                repo.update(row, row.row_version)
                record(
                    session,
                    tenant_id=tenant,
                    actor_id=actor_id,
                    action=REKEYED,
                    entity_type=ENTITY,
                    entity_id=str(row.id),
                    after={"name": row.name, "from_key_id": old, "to_key_id": active},
                    correlation_id=correlation_id_var.get() or "rekey",
                )
                report.moved += 1
            session.commit()
    log.info(
        "rekey: moved=%d failed=%d remaining=%d", report.moved, len(report.failed), report.remaining
    )
    return report


def main(
    argv: list[str] | None = None,
    *,
    open_session: Callable[[], Session] | None = None,
) -> int:
    """0: nothing failed (or a dry run counted). 1: a row could not be decrypted (its key is gone).
    2: no usable keyring. `open_session` is for tests; the command opens `DATABASE_URL`."""
    import os

    from sbam_api.connections.crypto import SecretsKeyError
    from sbam_api.connections.keys import keyring_from_settings
    from sbam_api.core.settings import SettingsError, load_settings
    from sbam_api.db.session import make_engine

    parser = argparse.ArgumentParser(prog="sbam-connections-rekey", description=__doc__)
    parser.add_argument("--tenant", type=uuid.UUID, default=None)
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument(
        "--dry-run", action="store_true", help="count the rows to move, change nothing"
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        keyring = keyring_from_settings(settings)
    except (SecretsKeyError, SettingsError) as exc:  # names a position or a key id, never a key
        print(f"rekey: {exc}", file=sys.stderr)
        return 2
    if open_session is None:
        engine = make_engine(os.environ.get("DATABASE_URL", settings.database_url))

        def open_session() -> Session:
            return Session(engine)

    with open_session() as session:
        report = rekey(
            session, keyring, tenant_id=args.tenant, batch=args.batch, dry_run=args.dry_run
        )
    print(f"moved={report.moved} failed={len(report.failed)} remaining={report.remaining}")
    for name, key_id in report.failed:
        print(f"cannot decrypt {name} (key {key_id})", file=sys.stderr)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
