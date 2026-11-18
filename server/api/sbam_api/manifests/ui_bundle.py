"""The UI_BUNDLE evidence of a Workflow (#857, ADR 0033).

BAM always delivers a Workflow's UI as a bundle, and SRIM shows only that bundle. The bundle is part
of the certified artifact, so certification of a manifest SBAM built needs one `gate_results` row,
`UI_BUNDLE`, on the bundle hash:

  present   the manifest names a bundle and the packaged bundle was found
  matching  the digest of the packaged files equals `ui_bundle.digest` of the frozen manifest
  hosts     every `allowed_hosts` entry is a legal host (a DNS name, never a scheme, port or IP)

The row has a `workflow_id` and no Sub-Skill: a UI bundle belongs to the Workflow. Generating the
bundle is #768, and this module never reads its files; the caller hands in the digest it computed
over the packaged bundle. A manifest without an `mcp_server` is a legacy one and is not judged.

Findings name the rule and the offending value, never bundle contents.
"""

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sbam_contracts.build_dashboard import GateName, GateStatus, Severity
from sbam_contracts.manifest import WorkflowManifest, host_problem
from sqlalchemy.orm import Session

from sbam_api.db.repository import TenantContext
from sbam_api.manifests.models import GateResultRow
from sbam_api.manifests.repository import GateResultRepository

GATE_VERSION: Final = "ui-bundle/1.0.0"
CONFIG_SHA256: Final = "sha256:" + hashlib.sha256(GATE_VERSION.encode()).hexdigest()
MISSING: Final = "MISSING_EVIDENCE:UI_BUNDLE"
FAILED: Final = "GATE_FAILED:UI_BUNDLE"
MISMATCH: Final = "EVIDENCE_HASH_MISMATCH:UI_BUNDLE"


def finding(
    gate: GateName, code: str, file: str, line: int | None, severity: Severity, message: str
) -> dict[str, Any]:
    """A finding in the shape of the other gates (`static_tools.finding`)."""
    return {
        "gate": gate.value,
        "code": code,
        "file": file,
        "line": line,
        "severity": severity.value,
        "message": message[:2000],
    }


@dataclass(frozen=True)
class UiBundleVerdict:
    status: GateStatus
    findings: list[dict[str, Any]]


def _bare(digest: str) -> str:
    return digest.removeprefix("sha256:")


def check_ui_bundle(manifest: WorkflowManifest, packaged_digest: str | None) -> UiBundleVerdict:
    """Present, hash matches the manifest, hosts valid. `packaged_digest` is the digest of the
    packaged bundle as found, or None when there is none."""
    gate, high = GateName.UI_BUNDLE, Severity.HIGH
    entry = manifest.ui_bundle
    if entry is None:
        message = "the manifest has no UI bundle entry"
        return UiBundleVerdict(
            GateStatus.FAILED, [finding(gate, "UI_BUNDLE_MISSING", "manifest", None, high, message)]
        )
    found: list[dict[str, Any]] = []
    if packaged_digest is None:
        message = "the packaged UI bundle was not found"
        found.append(finding(gate, "UI_BUNDLE_NOT_FOUND", entry.entry_file, None, high, message))
    elif _bare(packaged_digest) != _bare(entry.digest):
        got, want = _bare(packaged_digest), _bare(entry.digest)
        message = f"packaged digest {got} is not the manifest's {want}"
        code = "UI_BUNDLE_HASH_MISMATCH"
        found.append(finding(gate, code, entry.entry_file, None, high, message))
    for host in entry.allowed_hosts:
        if (problem := host_problem(host)) is not None:
            message = f"host {host!r} {problem}"
            found.append(finding(gate, "UI_BUNDLE_HOST_INVALID", host, None, high, message))
    return UiBundleVerdict(GateStatus.FAILED if found else GateStatus.PASSED, found)


def record_ui_bundle_gate(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    manifest: WorkflowManifest,
    packaged_digest: str | None,
) -> GateResultRow:
    """Run the check and write the Workflow-level `UI_BUNDLE` row (idempotent on a retry)."""
    verdict = check_ui_bundle(manifest, packaged_digest)
    entry = manifest.ui_bundle
    return GateResultRepository(session, ctx).record_workflow_gate(
        {
            "workflow_id": workflow_id,
            "project_id": ctx.project_id,
            "gate": GateName.UI_BUNDLE.value,
            "status": verdict.status.value,
            "findings": verdict.findings,
            "tool_version": GATE_VERSION,
            "config_sha256": CONFIG_SHA256,
            "bundle_sha256": _bare(entry.digest) if entry is not None else None,
            "created_by": "SYSTEM",
        }
    )


def certification_reasons(manifest: WorkflowManifest, rows: Sequence[GateResultRow]) -> list[str]:
    """Why the Workflow cannot be certified for want of UI bundle evidence; empty when it can.

    `rows` are the Workflow's `UI_BUNDLE` rows. The newest decides, and it must be PASSED on the
    digest the manifest carries. A legacy manifest (no server image) is not judged.
    """
    if not manifest.requires_ui_bundle:
        return []
    entry = manifest.ui_bundle
    if entry is None or not rows:
        return [MISSING]
    row = rows[-1]
    if row.bundle_sha256 != _bare(entry.digest):
        return [MISMATCH]
    if row.status != GateStatus.PASSED.value:
        return [FAILED]
    return []
