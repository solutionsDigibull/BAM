"""Re-hash stored artifacts against the sha256 recorded when they were stored (E16.5-T03).

    python -m sbam_api.artifacts.verify [--all]

The restore drill runs it inside sbam-api after a restore: every frozen artifact (or, with --all,
every artifact) is read back through StorageBackend and hashed. Prints one JSON report and exits 1
when any object is missing or its bytes differ, so a drill with a single mismatch fails. Keys only,
never contents.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable
from typing import Any

from sbam_api.artifacts.models import Artifact
from sbam_api.artifacts.repository import every_artifact
from sbam_api.core.settings import load_settings
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.storage.base import StorageBackend, chunks
from sbam_api.storage.factory import make_backend


def verify(artifacts: Iterable[Artifact], storage: StorageBackend) -> dict[str, Any]:
    checked, mismatched, missing = 0, [], []
    for artifact in artifacts:
        checked += 1
        if not storage.exists(artifact.storage_key):
            missing.append(artifact.storage_key)
            continue
        digest = hashlib.sha256()
        with storage.open(artifact.storage_key) as data:
            for block in chunks(data):
                digest.update(block)
        if digest.hexdigest() != artifact.sha256:
            mismatched.append(artifact.storage_key)
    return {
        "checked": checked,
        "matched": checked - len(mismatched) - len(missing),
        "mismatched": mismatched,
        "missing": missing,
        "ok": not mismatched and not missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="every artifact, not only frozen ones")
    args = parser.parse_args(argv)
    settings = load_settings()
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as session:
        report = verify(every_artifact(session, frozen_only=not args.all), make_backend(settings))
    report["scope"] = "all" if args.all else "frozen"
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
