"""Read a knowledge version's graph (E5.4, TD §8): from its OKF bundle, never from Cognee.

The bundle (E5.3) is the version's immutable record: a READY or OUTDATED version reads the same
whatever the project's Cognee dataset holds after later rebuilds, the API never waits on Cognee,
and `q` is a plain substring filter, never part of a graph query. Parts are read hash-verified and
cached by their sha256 (content-addressed, so a cache entry can never go stale).

Cursors are opaque: base64url of {version, offset, filter} plus an HMAC, so a cursor from another
version, another filter or edited by hand is 400 INVALID_CURSOR.
"""

import base64
import binascii
import hashlib
import hmac
import io
import json
import re
import threading
import time
import uuid
import zipfile
from collections import OrderedDict, deque
from dataclasses import dataclass

from sbam_contracts.api import ErrorCode
from sbam_contracts.knowledge import (
    MAX_NEIGHBOURS,
    MAX_PASSAGE_CHARS,
    GraphApiEdge,
    GraphApiNode,
    KnowledgeCoverage,
    KnowledgeGraphPage,
    KnowledgeNodeDetail,
    KnowledgeVersionList,
    KnowledgeVersionSummary,
    SourcePassage,
)
from sbam_contracts.okf import BUNDLE, PARTS, BundleManifest, OkfChunk, OkfEntity, OkfRelation
from sbam_contracts.state_machines import KnowledgeVersionState
from sqlalchemy.orm import Session

from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import Document
from sbam_api.ingest.repository import DocumentRepository, DocumentVersionRepository
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.knowledge.repository import KnowledgeVersionRepository
from sbam_api.knowledge.service import read_okf_part
from sbam_api.storage.base import Kind, StorageBackend, StorageScope

READABLE = frozenset({KnowledgeVersionState.READY.value, KnowledgeVersionState.OUTDATED.value})
MAX_TYPES = 10
MAX_QUERY = 200
CACHE_SIZE = 8
RATE_PER_SECOND = 20
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # fixed, so the same bundle always zips to the same bytes


def not_ready() -> ApiError:
    return ApiError(
        409,
        ErrorCode.KNOWLEDGE_NOT_READY,
        "Knowledge not ready",
        "This knowledge version is still building or failed; it has no graph to read.",
    )


def invalid_cursor() -> ApiError:
    return ApiError(400, ErrorCode.INVALID_CURSOR, "Invalid cursor")


# --- versions ------------------------------------------------------------------------------------


def list_versions(session: Session, ctx: TenantContext) -> KnowledgeVersionList:
    rows = KnowledgeVersionRepository(session, ctx).list()
    return KnowledgeVersionList(
        versions=[
            KnowledgeVersionSummary(
                id=r.id,
                version=r.version,
                status=r.state,
                coverage=KnowledgeCoverage.model_validate(r.coverage or {}),
                error_reason=r.error_reason,
                created_at=r.created_at,
            )
            for r in sorted(rows, key=lambda r: -r.version)
        ]
    )


def readable_version(session: Session, ctx: TenantContext, kv_id: uuid.UUID) -> KnowledgeVersion:
    """The project's version (else 404), READY or OUTDATED with a bundle (else 409)."""
    row: KnowledgeVersion | None = KnowledgeVersionRepository(session, ctx).get(kv_id)
    if row is None:
        raise not_found()
    if row.state not in READABLE or not row.okf_bundle_key:
        raise not_ready()
    return row


# --- the bundle, parsed once per content hash ----------------------------------------------------


@dataclass(frozen=True)
class Loaded:
    entities: list[OkfEntity]  # sorted by id
    by_id: dict[str, OkfEntity]
    relations: list[OkfRelation]
    chunks: dict[str, OkfChunk]
    degree: dict[str, int]
    neighbours: dict[str, list[str]]
    parts: dict[str, str]  # name -> storage key (bundle.json included)


_cache: "OrderedDict[str, Loaded]" = OrderedDict()
_cache_lock = threading.Lock()


def load(storage: StorageBackend, row: KnowledgeVersion) -> Loaded:
    key = row.okf_bundle_key or ""
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    head = BundleManifest.model_validate_json(read_okf_part(storage, key))
    raw = {name: read_okf_part(storage, ref.storage_key) for name, ref in head.parts.items()}

    def lines(name: str) -> list[str]:
        return raw[name].decode("utf-8").splitlines()

    entities = sorted(
        (OkfEntity.model_validate_json(x) for x in lines("entities.jsonl")), key=lambda e: e.id
    )
    relations = [OkfRelation.model_validate_json(x) for x in lines("relations.jsonl")]
    chunks = {c.id: c for c in (OkfChunk.model_validate_json(x) for x in lines("chunks.jsonl"))}
    degree: dict[str, int] = {}
    neighbours: dict[str, list[str]] = {}
    for r in relations:
        for a, b in ((r.from_, r.to), (r.to, r.from_)):
            degree[a] = degree.get(a, 0) + 1
            neighbours.setdefault(a, []).append(b)
    loaded = Loaded(
        entities=entities,
        by_id={e.id: e for e in entities},
        relations=relations,
        chunks=chunks,
        degree=degree,
        neighbours={k: sorted(set(v)) for k, v in neighbours.items()},
        parts={BUNDLE: key, **{n: ref.storage_key for n, ref in head.parts.items()}},
    )
    with _cache_lock:
        _cache[key] = loaded
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return loaded


def _node(loaded: Loaded, entity: OkfEntity) -> GraphApiNode:
    return GraphApiNode(
        id=entity.id, type=entity.type, name=entity.name, degree=loaded.degree.get(entity.id, 0)
    )


# --- filters and cursors -------------------------------------------------------------------------


def clean_query(q: str | None) -> str | None:
    """Control characters stripped, trimmed; whitespace only means no query. 422 if too long."""
    if q is None:
        return None
    text = _CONTROL.sub("", q).strip()
    if len(text) > MAX_QUERY:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, f"q is longer than {MAX_QUERY} characters")
    return text or None


def clean_types(raw: str | None) -> list[str]:
    types = sorted({t.strip() for t in (raw or "").split(",") if t.strip()})
    if len(types) > MAX_TYPES or any(len(t) > 64 for t in types):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, f"type takes at most {MAX_TYPES} types")
    return types


def _matches(entity: OkfEntity, types: list[str], q: str | None) -> bool:
    if types and entity.type not in types:
        return False
    if q is None:
        return True
    needle = q.casefold()
    haystack = [entity.name, *entity.attributes.values()]
    return any(needle in value.casefold() for value in haystack)  # plain text, never a query


def _filter_key(types: list[str], q: str | None) -> str:
    return hashlib.sha256(json.dumps([types, q]).encode()).hexdigest()[:16]


def _sign(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()[:32]


def encode_cursor(secret: bytes, kv_id: uuid.UUID, offset: int, filters: str) -> str:
    payload = json.dumps({"kv": str(kv_id), "o": offset, "f": filters}).encode()
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{token}.{_sign(secret, payload)}"


def decode_cursor(secret: bytes, cursor: str, kv_id: uuid.UUID, filters: str) -> int:
    try:
        token, sig = cursor.split(".", 1)
        payload = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        data = json.loads(payload)
    except (ValueError, binascii.Error):
        raise invalid_cursor() from None
    if not hmac.compare_digest(_sign(secret, payload), sig):
        raise invalid_cursor()
    offset = data.get("o") if isinstance(data, dict) else None
    if data.get("kv") != str(kv_id) or data.get("f") != filters or not isinstance(offset, int):
        raise invalid_cursor()
    if offset < 0:
        raise invalid_cursor()
    return offset


# --- graph page and node detail ------------------------------------------------------------------


def graph_page(
    loaded: Loaded,
    row: KnowledgeVersion,
    *,
    secret: bytes,
    cursor: str | None,
    limit: int,
    types: list[str],
    q: str | None,
) -> KnowledgeGraphPage:
    filters = _filter_key(types, q)
    start = decode_cursor(secret, cursor, row.id, filters) if cursor else 0
    matching = [e for e in loaded.entities if _matches(e, types, q)]
    page = matching[start : start + limit]
    on_page = {e.id for e in page}
    edges = [
        GraphApiEdge.model_validate(
            {
                "id": r.id,
                "type": r.type,
                "from": r.from_,
                "to": r.to,
                "external": r.to not in on_page,
            }
        )
        for r in sorted(loaded.relations, key=lambda r: (r.from_, r.id))
        if r.from_ in on_page
    ]
    end = start + len(page)
    return KnowledgeGraphPage(
        knowledge_version_id=row.id,
        status=row.state,
        nodes=[_node(loaded, e) for e in page],
        edges=edges,
        next_cursor=encode_cursor(secret, row.id, end, filters) if end < len(matching) else None,
        total_estimate=len(matching),
    )


def node_detail(
    session: Session, ctx: TenantContext, loaded: Loaded, row: KnowledgeVersion, node_id: str
) -> KnowledgeNodeDetail:
    entity = loaded.by_id.get(node_id)
    if entity is None:
        raise not_found()
    around = [loaded.by_id[n] for n in loaded.neighbours.get(node_id, []) if n in loaded.by_id]
    passages = []
    titles = _titles(session, ctx, {ref.document_version_id for ref in entity.source_refs})
    for ref in entity.source_refs:
        chunk = loaded.chunks.get(ref.chunk_id)
        if chunk is None:
            continue
        passages.append(
            SourcePassage(
                document_version_id=ref.document_version_id,
                title=titles.get(ref.document_version_id, ""),
                location=ref.location,
                text=chunk.text[:MAX_PASSAGE_CHARS],
                truncated=len(chunk.text) > MAX_PASSAGE_CHARS,
            )
        )
    return KnowledgeNodeDetail(
        knowledge_version_id=row.id,
        status=row.state,
        node=_node(loaded, entity),
        neighbours=[_node(loaded, e) for e in around[:MAX_NEIGHBOURS]],
        has_more=len(around) > MAX_NEIGHBOURS,
        source_passages=passages,
    )


def _titles(
    session: Session, ctx: TenantContext, version_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    versions = DocumentVersionRepository(session, ctx)
    documents = DocumentRepository(session, ctx)
    out: dict[uuid.UUID, str] = {}
    for version_id in version_ids:
        version = versions.get(version_id)
        document: Document | None = documents.get(version.document_id) if version else None
        if document is not None:
            out[version_id] = document.title
    return out


# --- OKF download --------------------------------------------------------------------------------


def bundle_zip(storage: StorageBackend, loaded: Loaded) -> bytes:
    """The six parts in a zip with fixed timestamps: the same bundle, the same zip bytes."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in (BUNDLE, *PARTS):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, read_okf_part(storage, loaded.parts[name]))
    return out.getvalue()


def store_zip(storage: StorageBackend, ctx: TenantContext, data: bytes) -> str:
    """Content-addressed under exports/: storing it again is a no-op."""
    if ctx.project_id is None:
        raise ValueError("store_zip needs a project in the context")
    where = StorageScope(ctx.tenant_id, ctx.project_id)
    return storage.put(data, where, Kind.EXPORTS, "application/zip").storage_key


# --- rate limit ----------------------------------------------------------------------------------


class RateLimiter:
    """At most `rate` requests per second per user, in this process (E5.4: 20/s -> 429)."""

    def __init__(self, rate: int = RATE_PER_SECOND) -> None:
        self.rate = rate
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, user_id: str, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(user_id, deque())
            while hits and hits[0] <= current - 1.0:
                hits.popleft()
            if len(hits) >= self.rate:
                raise ApiError(429, ErrorCode.RATE_LIMITED, "Too many requests")
            hits.append(current)


limiter = RateLimiter()  # the knowledge read routes' (sbam_api.knowledge.router.limited)
