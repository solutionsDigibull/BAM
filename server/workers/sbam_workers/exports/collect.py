"""What goes into the export package of one frozen Workflow version (#133, E14.5).

Everything comes from the frozen version, never from "the newest build", so an export taken while a
patch is building is the requested version only:

  workflow_manifest.json          the sealed manifest of the `workflow_versions` row (hashed)
  skills/<id>/manifest.json       each Sub-Skill's frozen Skill Manifest (ISA14, status APPROVED)
  skills/<id>/loop_spec.json      from the Sub-Skill's code bundle
  skills/<id>/code/...            the rest of the bundle, vendored repositories (`vendor/`), patch
                                  files (`patches/`) and the patched tree (`overlay/`) included
  schema.sql                      the stored file the manifest names (sha256 checked)
  evidence_index.json             the manifest's evidence index
  sbom.cdx.json                   CycloneDX of the vendored repositories and their patches (#757)
  .env.example                    names only (ADR 0030), never a value
  tools.json, image.json          the workflow server's tools and image digests (manifest)
  ui_bundle.json                  the certified UI bundle entry (#857)

`files` lists the stored frozen files for the `read_back` hash re-check before packaging. Bundles
are opened lazily, one at a time, so a Workflow of 500 Sub-Skills does not sit in memory.
"""

import io
import tarfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.builds.workflow_code import overlays_of
from sbam_api.connections import service as connections
from sbam_api.connections.env_example import EnvEntry, env_example
from sbam_api.db.repository import TenantContext
from sbam_api.exports.service import frozen_version
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.models import SubSkillManifest
from sbam_api.manifests.repository import (
    SubSkillManifestRepository,
    WorkflowManifestRepository,
)
from sbam_api.storage.base import StorageBackend
from sbam_api.versioning.freezer import FrozenFile, HashMismatch
from sbam_contracts.canonical import canonical_json
from sbam_contracts.exports import (
    ENV_EXAMPLE_FILE,
    EVIDENCE_INDEX_FILE,
    IMAGE_FILE,
    SBOM_FILE,
    SCHEMA_SQL_FILE,
    SKILL_CODE_DIR,
    SKILL_LOOP_SPEC_FILE,
    SKILL_MANIFEST_FILE,
    SKILLS_ROOT,
    TOOLS_FILE,
    UI_BUNDLE_FILE,
    WORKFLOW_MANIFEST_FILE,
)
from sbam_contracts.manifest import ManifestStatus, SkillManifest, WorkflowManifest
from sbam_contracts.repo_overlay import RepoOverlay
from sqlalchemy.orm import Session

from sbam_workers.exports.package import Source, check_path
from sbam_workers.repo_fetch.sbom import build_sbom, sbom_bytes


@dataclass
class Collected:
    entries: dict[str, Source] = field(default_factory=dict)
    files: list[FrozenFile] = field(default_factory=list)


def _hex(digest: str | None) -> str | None:
    return None if digest is None else digest.removeprefix("sha256:")


def _verified(storage: StorageBackend, key: str) -> bytes:
    """The stored bytes, re-hashed against the key; missing or different is `HashMismatch`."""
    try:
        return storage.get(key, verify=True)
    except Exception as error:
        raise HashMismatch(f"{key} cannot be read back intact") from error


class _Bundles:
    """The files of one code bundle at a time (the entries are packaged in path order)."""

    def __init__(self, storage: StorageBackend) -> None:
        self.storage = storage
        self.key: str | None = None
        self.files: dict[str, bytes] = {}

    def read(self, key: str) -> dict[str, bytes]:
        if key != self.key:
            self.files = {}
            data = _verified(self.storage, key)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
                for member in tar.getmembers():
                    if member.isdir():
                        continue
                    if not member.isfile():  # a symlink, a device: never exported
                        raise HashMismatch(f"{key} holds {member.name!r}, which is not a file")
                    handle = tar.extractfile(member)
                    if handle is not None:
                        self.files[member.name] = handle.read()
            self.key = key
        return self.files

    def loader(self, key: str, name: str) -> Callable[[], bytes]:
        return lambda: self.read(key)[name]


def _sub_manifest(
    session: Session, ctx: TenantContext, sub_skill_id: uuid.UUID, deterministic_hash: str
) -> SubSkillManifest | None:
    """The frozen manifest row of the Sub-Skill whose hash the Workflow manifest records."""
    rows = SubSkillManifestRepository(session, ctx).history(sub_skill_id)
    found = [r for r in rows if r.is_frozen and r.sha256 == deterministic_hash]
    return max(found, key=lambda r: (r.build_no, r.cycle, r.attempt)) if found else None


def collect(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    version: str,
    storage: StorageBackend,
) -> Collected:
    row = frozen_version(session, ctx, workflow_id, version)  # 404 / 409 NOT_FROZEN
    sealed = WorkflowManifestRepository(session, ctx).get(row.workflow_manifest_id)
    if sealed is None or not sealed.is_frozen:
        raise HashMismatch(f"the frozen Workflow manifest of {version} is missing")
    manifest = WorkflowManifest.model_validate(sealed.manifest)
    if manifest.computed_hash() != sealed.sha256:
        raise HashMismatch(f"the Workflow manifest of {version} does not match its hash")
    out = Collected()
    out.entries[WORKFLOW_MANIFEST_FILE] = canonical_json(sealed.manifest)
    subs = {s.skill_id: s for s in SubSkillRepository(session, ctx).of_workflow(workflow_id)}
    attempts = CodeAttemptRepository(session, ctx)
    bundles = _Bundles(storage)
    overlays: dict[tuple[str, str], RepoOverlay] = {}
    for agent in manifest.macro_skills:
        for skill in agent.skills:
            base = check_path(f"{SKILLS_ROOT}/{skill.skill_id}")
            sub = subs.get(skill.skill_id)
            found = (
                None
                if sub is None
                else _sub_manifest(session, ctx, sub.id, skill.deterministic_hash)
            )
            if sub is None or found is None:
                raise HashMismatch(f"the frozen manifest of {skill.skill_id} is missing")
            skill_manifest = SkillManifest.model_validate(found.manifest)
            if skill_manifest.computed_hash() != skill.deterministic_hash:
                raise HashMismatch(f"the manifest of {skill.skill_id} does not match its hash")
            if skill_manifest.skill_metadata.status is not ManifestStatus.APPROVED:
                raise HashMismatch(f"the manifest of {skill.skill_id} is not APPROVED")
            out.entries[f"{base}/{SKILL_MANIFEST_FILE}"] = canonical_json(found.manifest)
            for overlay in overlays_of(found.manifest):
                overlays.setdefault((str(overlay.url), str(overlay.commit)), overlay)
            wanted = _hex(skill.bundle_sha256)
            usable = [
                a
                for a in attempts.of_sub_skill(workflow_id, sub.id)
                if a.bundle_key and _hex(a.bundle_sha256) == wanted
            ]
            if not usable or usable[-1].bundle_key is None:
                raise HashMismatch(f"the frozen code bundle of {skill.skill_id} is not stored")
            key = usable[-1].bundle_key
            out.files.append(FrozenFile(key))
            for name in sorted(bundles.read(key)):
                path = (
                    f"{base}/{SKILL_LOOP_SPEC_FILE}"
                    if name == SKILL_LOOP_SPEC_FILE
                    else f"{base}/{SKILL_CODE_DIR}/{name}"
                )
                out.entries[check_path(path)] = bundles.loader(key, name)
    sql_key = manifest.schema_sql.artifact_key
    out.files.append(FrozenFile(sql_key))

    def schema_sql() -> bytes:
        data = _verified(storage, sql_key)
        if not sql_key.endswith(f"/{_hex(manifest.schema_sql.sha256)}"):
            raise HashMismatch("schema.sql is not the file the manifest names")
        return data

    out.entries[SCHEMA_SQL_FILE] = schema_sql
    out.entries[EVIDENCE_INDEX_FILE] = canonical_json(
        [entry.model_dump(mode="json") for entry in manifest.evidence_index]
    )
    out.entries[SBOM_FILE] = sbom_bytes(
        build_sbom(
            subject=manifest.name,
            version=version,
            overlays=[overlays[k] for k in sorted(overlays)],
        )
    )
    expected = connections.expected_for_workflow(session, ctx, workflow_id)
    out.entries[ENV_EXAMPLE_FILE] = env_example(
        EnvEntry(name=c.name, purpose=c.purpose, required=c.required) for c in expected.names
    ).encode()
    image = {"runtime_image_digest": manifest.runtime_image_digest}
    if manifest.mcp_server is not None:
        image |= {
            "workflow_server": manifest.mcp_server.name,
            "image_digest": manifest.mcp_server.image_digest,
        }
        out.entries[TOOLS_FILE] = canonical_json(
            [tool.model_dump(mode="json") for tool in manifest.mcp_server.tools]
        )
    out.entries[IMAGE_FILE] = canonical_json(image)
    if manifest.ui_bundle is not None:
        out.entries[UI_BUNDLE_FILE] = canonical_json(manifest.ui_bundle.model_dump(mode="json"))
    bundles.files, bundles.key = {}, None  # listing is done: load again lazily while packaging
    return out
