"""Whole-Workflow code preview reads (ADR 0028, WG-15, #763). Read only.

The tree is built from what is stored: each Sub-Skill's newest code attempt (its file map and the
gate results and findings of that attempt) plus the Workflow's own manifest and schema. A file's tag
says where it came from: vendored repository code is `reused`, a patch and the repository files a
patch changed are `customized`, everything SBAM wrote is `generated`. The provenance of the
repository files is the Sub-Skill manifest's `build.repos` (WG-9). Nothing here writes: there is no
route that changes a file, and a path is only ever matched against the stored file maps.
"""

import hashlib
import json
import uuid
from typing import Any, Final

from pydantic import ValidationError
from sbam_contracts.code_view import (
    MAX_VIEW_BYTES,
    WORKFLOW_GROUP,
    CodeFinding,
    FileTag,
    GateStatusRow,
    GroupKind,
    WorkflowCodeFile,
    WorkflowCodeGroup,
    WorkflowCodeTree,
    WorkflowFileView,
)
from sbam_contracts.repo_overlay import OVERLAY_ROOT, PATCH_ROOT, VENDOR_ROOT, RepoOverlay
from sbam_contracts.schema_sql import SchemaSource
from sqlalchemy.orm import Session

from sbam_api.builds.code_view import _findings as attempt_findings
from sbam_api.builds.code_view import _read_file as read_attempt_file
from sbam_api.builds.models import CodeAttempt
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.builds.workflow_server import newest as newest_server
from sbam_api.builds.workflow_server import read_file as read_server_file
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import not_found
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.repository import (
    GateResultRepository,
    SubSkillManifestRepository,
    WorkflowManifestRepository,
)
from sbam_api.schema_sql.repository import WorkflowSchemaRepository
from sbam_api.storage.base import StorageBackend
from sbam_api.workflows.repository import WorkflowRepository

MANIFEST_FILE: Final = "manifest.json"
SCHEMA_FILE: Final = "schema.sql"


def overlays_of(manifest: dict[str, Any] | None) -> list[RepoOverlay]:
    """The repositories a Skill manifest records in `build.repos`; a bad entry is skipped."""
    raw = ((manifest or {}).get("build") or {}).get("repos") or []
    out: list[RepoOverlay] = []
    for item in raw:
        try:
            out.append(RepoOverlay.model_validate(item))
        except ValidationError:
            continue
    return out


def _repo_name(overlay: RepoOverlay) -> str:
    """`vendor/<repo>@<commit>` -> `<repo>`: the directory name under `overlay/`."""
    return overlay.repo_dir.removeprefix(f"{VENDOR_ROOT}/").split("@", 1)[0]


def file_tag(path: str, overlays: list[RepoOverlay]) -> FileTag:
    """Where `path` of a bundle comes from, by its place in the bundle and the recorded patches."""
    root, _, rest = path.partition("/")
    if root == VENDOR_ROOT:
        return FileTag.REUSED
    if root == PATCH_ROOT:
        return FileTag.CUSTOMIZED
    if root == OVERLAY_ROOT:
        name, _, inner = rest.partition("/")
        for overlay in overlays:
            if _repo_name(overlay) == name and any(inner in p.files for p in overlay.patches):
                return FileTag.CUSTOMIZED
        return FileTag.REUSED
    return FileTag.GENERATED


def _newest_attempt(rows: list[CodeAttempt]) -> CodeAttempt | None:
    """The last attempt that stored a bundle, else the last attempt (rows run attempt 0 first)."""
    stored = [r for r in rows if r.files]
    pool = stored or rows
    return pool[-1] if pool else None


def _check_workflow(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID
) -> TenantContext:
    """The caller's tenant and project scope; the Workflow must be in it (else 404)."""
    ctx = user.context(project_id)
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    return ctx


def _sub_group(
    sub: SubSkill,
    row: CodeAttempt | None,
    overlays: list[RepoOverlay],
    gates: GateResultRepository,
) -> WorkflowCodeGroup:
    if row is None:
        return WorkflowCodeGroup(
            kind=GroupKind.SUB_SKILL,
            group_id=str(sub.id),
            label=sub.skill_id,
            files=[],
            gates=[],
            findings=[],
        )
    all_findings = attempt_findings(row)
    files = [
        WorkflowCodeFile(
            path=path,
            sha256=digest,
            tag=file_tag(path, overlays),
            findings=[f for f in all_findings if f.file == path],
        )
        for path, digest in sorted((row.files or {}).items())
    ]
    return WorkflowCodeGroup(
        kind=GroupKind.SUB_SKILL,
        group_id=str(sub.id),
        label=sub.skill_id,
        build_no=row.build_no,
        cycle=row.cycle,
        attempt=row.attempt,
        status=row.status,
        files=files,
        gates=[GateStatusRow(gate=g.gate, status=g.status) for g in gates.of_attempt(row.id)],
        findings=[f for f in all_findings if f.file is None],
    )


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode()


def _workflow_files(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID
) -> list[tuple[str, str, FileTag, str | None]]:
    """(path, sha256, tag, storage key) of the Workflow's own files; no key for the manifest."""
    out: list[tuple[str, str, FileTag, str | None]] = []
    manifests = WorkflowManifestRepository(session, ctx).history(workflow_id)
    if manifests:
        raw = _manifest_bytes(manifests[0].manifest)
        out.append((MANIFEST_FILE, hashlib.sha256(raw).hexdigest(), FileTag.GENERATED, None))
    schema = WorkflowSchemaRepository(session, ctx).latest(workflow_id)
    if schema is not None and schema.storage_key and schema.sha256:
        tag = FileTag.GENERATED if schema.source == SchemaSource.GENERATED else FileTag.REUSED
        out.append((SCHEMA_FILE, schema.sha256, tag, schema.storage_key))
    return out


def _server_files(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, taken: set[str]
) -> list[tuple[str, str]]:
    """(path, sha256) of the generated server files the build stored (#862), without the paths the
    Workflow already lists (its manifest and its `schema.sql`, which has its own record)."""
    record = newest_server(session, ctx.tenant_id, workflow_id)
    if record is None:
        return []
    return [(p, h) for p, h in sorted(record.files.items()) if p not in taken]


def tree(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID
) -> WorkflowCodeTree:
    ctx = _check_workflow(session, user, project_id, workflow_id)
    subs = SubSkillRepository(session, ctx).of_workflow(workflow_id)
    manifests = SubSkillManifestRepository(session, ctx).newest([s.id for s in subs])
    attempts = CodeAttemptRepository(session, ctx)
    gates = GateResultRepository(session, ctx)
    groups = []
    for sub in subs:
        row = _newest_attempt(attempts.of_sub_skill(workflow_id, sub.id))
        found = manifests.get(sub.id)
        overlays = overlays_of(found.manifest if found is not None else None)
        groups.append(_sub_group(sub, row, overlays, gates))
    own = [
        WorkflowCodeFile(path=p, sha256=h, tag=t, findings=[])
        for p, h, t, _ in _workflow_files(session, ctx, workflow_id)
    ]
    own += [
        WorkflowCodeFile(path=p, sha256=h, tag=FileTag.GENERATED, findings=[])
        for p, h in _server_files(session, ctx, workflow_id, {f.path for f in own})
    ]
    groups.append(
        WorkflowCodeGroup(
            kind=GroupKind.WORKFLOW,
            group_id=WORKFLOW_GROUP,
            label="Workflow",
            files=own,
            gates=[],
            findings=[],
        )
    )
    return WorkflowCodeTree(workflow_id=str(workflow_id), groups=groups)


def _cut(raw: bytes) -> tuple[str, bool]:
    return raw[:MAX_VIEW_BYTES].decode("utf-8", errors="replace"), len(raw) > MAX_VIEW_BYTES


def file_view(
    session: Session,
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    group_id: str,
    path: str,
) -> WorkflowFileView:
    ctx = _check_workflow(session, user, project_id, workflow_id)
    if group_id == WORKFLOW_GROUP:
        return _workflow_file(session, storage, ctx, workflow_id, path)
    try:
        sub_id = uuid.UUID(group_id)
    except ValueError:
        raise not_found() from None
    sub = SubSkillRepository(session, ctx).get(sub_id)
    if sub is None or sub.workflow_id != workflow_id:  # own, foreign or invented id
        raise not_found()
    row = _newest_attempt(CodeAttemptRepository(session, ctx).of_sub_skill(workflow_id, sub_id))
    if row is None:
        raise not_found()
    raw = read_attempt_file(storage, row, path)  # 404 unless the stored file map lists the path
    found = SubSkillManifestRepository(session, ctx).newest([sub_id]).get(sub_id)
    overlays = overlays_of(found.manifest if found is not None else None)
    text, truncated = _cut(raw)
    findings: list[CodeFinding] = attempt_findings(row, path)
    return WorkflowFileView(
        group_id=group_id,
        path=path,
        tag=file_tag(path, overlays),
        sha256=(row.files or {})[path],
        content=text,
        truncated=truncated,
        findings=findings,
    )


def _workflow_file(
    session: Session,
    storage: StorageBackend,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    path: str,
) -> WorkflowFileView:
    listed = {p: (h, t, k) for p, h, t, k in _workflow_files(session, ctx, workflow_id)}
    if path not in listed:
        return _server_file(session, storage, ctx, workflow_id, path)
    digest, tag, key = listed[path]
    if key is None:  # the manifest is rendered from the stored row
        manifests = WorkflowManifestRepository(session, ctx).history(workflow_id)
        raw = _manifest_bytes(manifests[0].manifest)
    else:
        try:
            raw = storage.get(key)
        except FileNotFoundError:
            raise not_found() from None
    if hashlib.sha256(raw).hexdigest() != digest:
        raise not_found()  # the stored file no longer matches its recorded hash
    text, truncated = _cut(raw)
    return WorkflowFileView(
        group_id=WORKFLOW_GROUP,
        path=path,
        tag=tag,
        sha256=digest,
        content=text,
        truncated=truncated,
        findings=[],
    )


def _server_file(
    session: Session,
    storage: StorageBackend,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    path: str,
) -> WorkflowFileView:
    """One generated server file (#862); 404 unless the stored file map lists it."""
    record = newest_server(session, ctx.tenant_id, workflow_id)
    if record is None or path not in record.files:
        raise not_found()
    text, truncated = _cut(read_server_file(storage, record, path))
    return WorkflowFileView(
        group_id=WORKFLOW_GROUP,
        path=path,
        tag=FileTag.GENERATED,
        sha256=record.files[path],
        content=text,
        truncated=truncated,
        findings=[],
    )
