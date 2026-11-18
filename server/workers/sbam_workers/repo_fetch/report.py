"""Store the scan report and the vendored tree, content-addressed (WG-7: "the report is stored
content-addressed").

The report is canonical JSON (sorted keys, no spaces), so the same report always has the same
sha256,
and the sha256 is its address: `<tenant>/<project>/[<workflow>/]repos/<sha256>`. The vendored
tree is
a deterministic tar (sorted names, fixed mtime, owner and mode), so the same commit always gives the
same bundle hash, which joins the certification reuse key (ADR 0028, decision 2).
"""

import io
import json
import tarfile
from collections.abc import Mapping

from sbam_api.storage.base import Kind, StorageBackend, StorageScope, StoredObject
from sbam_contracts.repo_scan import RepoScanReport

REPORT_TYPE = "application/json"
BUNDLE_TYPE = "application/x-tar"
MAX_BUNDLE_BYTES = 150 * 1024 * 1024


def canonical(report: RepoScanReport) -> bytes:
    body = report.model_dump(mode="json")
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def bundle_tar(files: Mapping[str, bytes]) -> bytes:
    """The files as a reproducible tar: no timestamps, owners or permissions of the source."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(files[name]))
    return buf.getvalue()


def store_bundle(
    storage: StorageBackend, scope: StorageScope, files: Mapping[str, bytes]
) -> StoredObject:
    return storage.put(
        bundle_tar(files), scope, Kind.REPOS, BUNDLE_TYPE, max_bytes=MAX_BUNDLE_BYTES
    )


def store_report(
    storage: StorageBackend, scope: StorageScope, report: RepoScanReport
) -> StoredObject:
    return storage.put(canonical(report), scope, Kind.REPOS, REPORT_TYPE)
