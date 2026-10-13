"""Load a runtime registry JSON into the DB (E12.1).

    python -m sbam_api.registry.sync FILE [--activate]

CI runs it after building skill-runtime (with the image digest filled in); deploys run it against
their database. All of it is one transaction, retried on connection errors, so a failed sync leaves
nothing behind (E12.1-T06). Exit codes: 0 synced/unchanged, 1 database unavailable after retries
or conflicting content for the same digest, 2 invalid file or settings.
"""

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError
from sbam_contracts.runtime_registry import RuntimeRegistry
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.registry.repository import RegistryConflictError, SyncOutcome, upsert_registry

log = logging.getLogger("sbam_api.registry")


def sync(
    sessions: sessionmaker[Session],
    registry: RuntimeRegistry,
    *,
    activate: bool,
    attempts: int = 3,
    delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> SyncOutcome:
    for attempt in range(1, attempts + 1):
        try:
            with sessions() as session:
                outcome = upsert_registry(session, registry, activate)
                session.commit()
                return outcome
        except OperationalError:
            if attempt == attempts:
                raise
            log.warning("registry sync: database unavailable, retrying (%d/%d)", attempt, attempts)
            sleep(delay * attempt)
    raise AssertionError("unreachable")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sbam-registry-sync", description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path, help="runtime_registry.json with image_digest set")
    parser.add_argument("--activate", action="store_true", help="make it the active registry")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args(argv)
    if not args.database_url:
        print("sbam-registry-sync: DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        registry = RuntimeRegistry.model_validate_json(args.file.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        print(f"sbam-registry-sync: invalid registry file: {exc}", file=sys.stderr)
        return 2
    if registry.image_digest is None:
        print("sbam-registry-sync: the registry has no image_digest", file=sys.stderr)
        return 2
    sessions = make_sessionmaker(make_engine(args.database_url))
    try:
        outcome = sync(sessions, registry, activate=args.activate, attempts=args.attempts)
    except OperationalError:
        print("sbam-registry-sync: database unavailable; nothing was written", file=sys.stderr)
        return 1
    except RegistryConflictError as exc:
        print(f"sbam-registry-sync: {exc}", file=sys.stderr)
        return 1
    active = " (active)" if args.activate else ""
    print(f"{registry.image_digest}: {outcome.value}{active}, {len(registry.packages)} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
