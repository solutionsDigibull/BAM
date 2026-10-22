"""What a SOW citation points at (E6.5 citation panel), read from the version's OKF bundle.

* `GET P/sow/versions/{v}/citations/{okf_ref_id}`: the chunk or entity, its document's title,
  location and passage (at most 4000 characters). A deleted document keeps its passage (the bundle
  is the version's immutable record) but has no title and no source: the panel shows "source
  deleted".
* `GET P/sow/versions/{v}/citations/{okf_ref_id}/source`: the document's derived text as Markdown,
  rebuilt from the bundle's chunks of that document (E5.3 keeps it byte for byte).

404 for an id that is not in the bundle, an unknown version, or another tenant / unassigned
project. The bundle is read hash-verified and cached (`knowledge.graph.load`).
"""

import uuid
from dataclasses import dataclass

from sbam_contracts.okf import OkfChunk
from sbam_contracts.sow_api import MAX_CITATION_PASSAGE, SowCitationDetail
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import not_found
from sbam_api.knowledge import graph
from sbam_api.knowledge.repository import KnowledgeVersionRepository
from sbam_api.sow.versions import _version
from sbam_api.storage.base import StorageBackend


@dataclass(frozen=True)
class Source:
    filename: str
    text: str


def _loaded(
    session: Session, storage: StorageBackend, user: CurrentUser, project_id: uuid.UUID, number: int
) -> graph.Loaded:
    row = _version(session, user, project_id, number)
    kv = KnowledgeVersionRepository(session, user.context(project_id)).get(row.knowledge_version_id)
    if kv is None or not kv.okf_bundle_key:
        raise not_found()
    return graph.load(storage, kv)


def _where(chunk_location: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in chunk_location.items() if k != "scope" and v is not None}


def detail(
    session: Session,
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    number: int,
    okf_ref_id: str,
) -> SowCitationDetail:
    loaded = _loaded(session, storage, user, project_id, number)
    chunk: OkfChunk | None = loaded.chunks.get(okf_ref_id)
    if chunk is not None:
        kind, doc_id, location, passage = (
            "chunk",
            chunk.document_version_id,
            _where(chunk.location.model_dump()),
            chunk.text,
        )
    else:
        entity = loaded.by_id.get(okf_ref_id)
        if entity is None:
            raise not_found()
        ref = entity.source_refs[0]
        source = loaded.chunks.get(ref.chunk_id)
        kind, doc_id, location = (
            "entity",
            ref.document_version_id,
            _where(ref.location.model_dump()),
        )
        passage = source.text if source is not None else entity.name
    titles = graph._titles(session, user.context(project_id), {doc_id})
    return SowCitationDetail(
        okf_ref_id=okf_ref_id,
        kind=kind,  # type: ignore[arg-type]
        document_version_id=doc_id,
        title=titles.get(doc_id),
        location=location,
        passage=passage[:MAX_CITATION_PASSAGE],
        truncated=len(passage) > MAX_CITATION_PASSAGE,
        source_available=doc_id in titles,
    )


def source(
    session: Session,
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    number: int,
    okf_ref_id: str,
) -> Source:
    found = detail(session, storage, user, project_id, number, okf_ref_id)
    if not found.source_available or found.title is None:
        raise not_found()
    loaded = _loaded(session, storage, user, project_id, number)
    chunks = [
        c for c in loaded.chunks.values() if c.document_version_id == found.document_version_id
    ]
    stem = found.title.rsplit(".", 1)[0] or "source"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stem).strip("-") or "source"
    return Source(filename=f"{safe}.md", text="".join(c.text for c in chunks))
