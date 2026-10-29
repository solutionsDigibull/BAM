"""The Cognee adapter (E5.1, TD §5.1, ADR 0001): the only door from SBAM into Cognee.

Every public method takes a `ProjectScope` first. Before Cognee is touched the adapter
  1. refuses anything that is not a `ProjectScope` (no global or default dataset);
  2. derives the dataset `sbam_<tenant>_<project>` and asserts its pattern;
  3. checks in the database that the project belongs to the tenant (`ScopeError`, E5.1-T03).
Each project has its own Cognee user that owns only its dataset, because dataset scoping alone
leaked on an unscoped search (spike T01). The user's password is derived from
`COGNEE_USER_SECRET`, so nothing is stored and nothing is logged.

Errors are classified here: a Bifrost outage reaches us as Cognee's HTTP 422 `EmbeddingException`
and becomes the retryable `CogneeUnavailable` (E5.1-T07); a missing or foreign dataset is
`DatasetNotFound` (SBAM's 404, never a 403). Logs carry ids and counts, never document text.
"""

import asyncio
import hashlib
import hmac
import logging
import re
import uuid
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from sbam_api.core.logging import correlation_id_var
from sbam_api.storage.base import Kind, StorageBackend
from sbam_contracts.knowledge import (
    DATASET_PATTERN,
    MAX_GRAPH_PAGE,
    MAX_TOP_K,
    AddResult,
    GraphEdge,
    GraphNode,
    GraphPage,
    ItemSource,
    KnowledgeItem,
    ProjectScope,
    SearchHit,
    cognee_dataset,
)

log = logging.getLogger("sbam_workers.knowledge.cognee")

DATASET_RE = re.compile(DATASET_PATTERN)
USER_DOMAIN = "projects.sbam.example.com"  # Cognee requires an email; never mailed
MAX_QUERY_LENGTH = 1000
_UNAVAILABLE = frozenset(
    {
        "EmbeddingException",  # what Cognee raises when Bifrost is down (ADR 0001 §7)
        "APIConnectionError",
        "APITimeoutError",
        "ServiceUnavailableError",
        "InternalServerError",
        "RateLimitError",
        "Timeout",
        "ConnectError",
        "ReadTimeout",
    }
)
_NOT_FOUND = frozenset(
    {"DatasetNotFoundError", "PermissionDeniedError", "UnauthorizedDataAccessError"}
)


class ScopeError(ValueError):
    """No valid scope, or the project is not the tenant's. Raised before any Cognee call."""


class ItemRejected(ValueError):  # noqa: N818
    """An item that must not reach Cognee (wrong project, not CLEAN + PARSED, hash mismatch)."""


class DatasetNotFound(LookupError):  # noqa: N818
    """The project's dataset does not exist (or is not this user's): SBAM's 404."""


class CogneeUnavailable(RuntimeError):  # noqa: N818 - name from ADR 0001
    """Cognee or Bifrost is down; retry the activity (E5.1-T07)."""


@dataclass(frozen=True)
class Principal:
    """The project's own Cognee user."""

    email: str
    password: str = field(repr=False)


class ScopeGuard(Protocol):
    """Database checks; the real one is `knowledge.scope.SqlScopeGuard`."""

    def project_in_tenant(self, scope: ProjectScope) -> bool: ...

    def eligible_versions(self, scope: ProjectScope, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """The ids among `ids` that are document versions of this project, CLEAN and PARSED."""
        ...

    def eligible_master_documents(self, scope: ProjectScope, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """The ids among `ids` that are master documents the project may use: the tenant's own or
        the project's, CLEAN and PARSED (E3.8b)."""
        ...


class CogneeBackend(Protocol):
    """The Cognee calls the adapter makes; the real one is `knowledge.cognee_backend`."""

    async def ensure_dataset(self, who: Principal, dataset: str) -> None: ...

    async def item_names(self, who: Principal, dataset: str) -> set[str]: ...

    async def add(self, who: Principal, dataset: str, name: str, data: bytes) -> None: ...

    async def remove(self, who: Principal, dataset: str, names: set[str]) -> None:
        """Delete the items with these names, with the graph nodes and edges only they sourced."""
        ...

    async def cognify(self, who: Principal, dataset: str) -> None: ...

    async def search(
        self, who: Principal, dataset: str, query: str, top_k: int
    ) -> list[SearchHit]: ...

    async def graph(
        self, who: Principal, dataset: str
    ) -> tuple[list[GraphNode], list[GraphEdge]]: ...

    async def delete_dataset(self, who: Principal, dataset: str) -> None: ...


def dataset_name(scope: ProjectScope) -> str:
    """`sbam_<tenant>_<project>` from a validated scope (E5.1-T01)."""
    if not isinstance(scope, ProjectScope):
        raise ScopeError("a ProjectScope is required")
    name = cognee_dataset(scope.tenant_id, scope.project_id)
    if not DATASET_RE.fullmatch(name):
        raise ScopeError("invalid dataset name")
    return name


def item_key(item: KnowledgeItem) -> str:
    """The idempotency key and Cognee data name of an item (E5.1-T08)."""
    return f"{item.source_id}_{item.sha256}"


def classify(exc: BaseException) -> BaseException:
    """Map a Cognee/LiteLLM/HTTP error to the adapter's error, by walking the cause chain."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ScopeError | ItemRejected | DatasetNotFound | CogneeUnavailable):
            return cur
        name = type(cur).__name__
        if name in _NOT_FOUND:
            return DatasetNotFound("dataset not found")
        # Not a bare 422: Cognee uses it for every validation error, which retrying cannot fix.
        status = getattr(cur, "status_code", None)
        if name in _UNAVAILABLE or status in (429, 502, 503, 504):
            return CogneeUnavailable(f"Cognee unavailable ({name})")
        if isinstance(cur, ConnectionError | TimeoutError):
            return CogneeUnavailable(f"Cognee unavailable ({name})")
        cur = cur.__cause__ or cur.__context__
    return exc


@asynccontextmanager
async def _classified() -> AsyncIterator[None]:
    try:
        yield
    except Exception as exc:
        mapped = classify(exc)
        if mapped is exc:
            raise
        raise mapped from exc


class CogneeAdapter:
    def __init__(
        self,
        backend: CogneeBackend,
        guard: ScopeGuard,
        storage: StorageBackend,
        user_secret: str,
    ) -> None:
        self._backend = backend
        self._guard = guard
        self._storage = storage
        self._secret = user_secret.encode("utf-8")

    async def ensure_dataset(self, scope: ProjectScope) -> str:
        """Create the project's user and dataset; a no-op when they exist. Returns the name."""
        dataset, who = await self._authorize(scope)
        async with _classified():
            await self._backend.ensure_dataset(who, dataset)
        self._log("dataset ready", scope)
        return dataset

    async def add(self, scope: ProjectScope, items: Sequence[KnowledgeItem]) -> AddResult:
        """Add the items not in the dataset yet. All items are checked before anything is added."""
        dataset, who = await self._authorize(scope)
        await self._check_items(scope, items)
        async with _classified():
            await self._backend.ensure_dataset(who, dataset)
            present = await self._backend.item_names(who, dataset)
        added: list[str] = []
        skipped: list[str] = []
        for item in items:
            key = item_key(item)
            if key in present:
                skipped.append(key)
                continue
            data = await asyncio.to_thread(self._storage.get, item.storage_key)
            if hashlib.sha256(data).hexdigest() != item.sha256:
                raise ItemRejected(f"sha256 mismatch for item {item.source_id}")
            async with _classified():
                await self._backend.add(who, dataset, key, data)
            present.add(key)
            added.append(key)
        self._log("items added", scope, added=len(added), skipped=len(skipped))
        return AddResult(added=added, skipped=skipped)

    async def prune(self, scope: ProjectScope, keep: set[str]) -> list[str]:
        """Remove every item of the project's dataset whose key is not in `keep` (#450): a deleted
        or replaced document, an older master snapshot. Returns the removed keys, sorted."""
        dataset, who = await self._authorize(scope)
        async with _classified():
            present = await self._backend.item_names(who, dataset)
            stale = present - keep
            if stale:
                await self._backend.remove(who, dataset, stale)
        self._log("items pruned", scope, removed=len(stale), kept=len(present) - len(stale))
        return sorted(stale)

    async def cognify(self, scope: ProjectScope) -> None:
        """Build the graph. Can take minutes: the calling activity heartbeats from its own task."""
        dataset, who = await self._authorize(scope)
        async with _classified():
            await self._backend.cognify(who, dataset)
        self._log("cognify done", scope)

    async def search(self, scope: ProjectScope, query: str, top_k: int = 10) -> list[SearchHit]:
        dataset, who = await self._authorize(scope)
        if not query.strip() or len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"query must be 1..{MAX_QUERY_LENGTH} characters")
        if not 1 <= top_k <= MAX_TOP_K:
            raise ValueError(f"top_k must be 1..{MAX_TOP_K}")
        async with _classified():
            hits = await self._backend.search(who, dataset, query, top_k)
        self._log("search", scope, hits=len(hits))
        return hits[:top_k]

    async def graph_page(
        self, scope: ProjectScope, cursor: str | None = None, limit: int = 100
    ) -> GraphPage:
        """Nodes sorted by id, `limit` per page; each edge is on the page of its source node."""
        dataset, who = await self._authorize(scope)
        if not 1 <= limit <= MAX_GRAPH_PAGE:
            raise ValueError(f"limit must be 1..{MAX_GRAPH_PAGE}")
        if cursor is not None and not cursor.isdecimal():
            raise ValueError("invalid cursor")
        start = int(cursor or 0)
        async with _classified():
            nodes, edges = await self._backend.graph(who, dataset)
        nodes = sorted(nodes, key=lambda n: n.id)
        page = nodes[start : start + limit]
        on_page = {n.id for n in page}
        end = start + len(page)
        return GraphPage(
            nodes=page,
            edges=[e for e in edges if e.source in on_page],
            next_cursor=str(end) if end < len(nodes) else None,
            total_nodes=len(nodes),
            total_edges=len(edges),
        )

    async def delete_dataset(self, scope: ProjectScope) -> None:
        dataset, who = await self._authorize(scope)
        async with _classified():
            await self._backend.delete_dataset(who, dataset)
        self._log("dataset deleted", scope)

    # --- checks ---------------------------------------------------------------------------------

    async def _authorize(self, scope: ProjectScope) -> tuple[str, Principal]:
        dataset = dataset_name(scope)
        if not await asyncio.to_thread(self._guard.project_in_tenant, scope):
            raise ScopeError("project not found in tenant")
        return dataset, self._principal(scope)

    def _principal(self, scope: ProjectScope) -> Principal:
        message = f"cognee-user:{scope.tenant_id}:{scope.project_id}".encode()
        return Principal(
            email=f"p-{scope.project_id}@{USER_DOMAIN}",
            password=hmac.new(self._secret, message, hashlib.sha256).hexdigest(),
        )

    async def _check_items(self, scope: ProjectScope, items: Iterable[KnowledgeItem]) -> None:
        prefix = f"{scope.tenant_id}/{scope.project_id}/"
        # A tenant-wide master document's derivative is stored under the tenant's all-zero project.
        tenant_derived = f"{scope.tenant_id}/{uuid.UUID(int=0)}/{Kind.DERIVED.value}/"
        documents: set[uuid.UUID] = set()
        master_documents: set[uuid.UUID] = set()
        for item in items:
            key = item.storage_key
            master = item.source is ItemSource.MASTER_DOCUMENT
            under = key.startswith(prefix) or (master and key.startswith(tenant_derived))
            if not under or ".." in key.split("/"):
                raise ItemRejected(f"item {item.source_id} is not stored under this project")
            if item.source is ItemSource.DOCUMENT:
                documents.add(item.source_id)
            if master:
                master_documents.add(item.source_id)
        if documents:
            ok = await asyncio.to_thread(self._guard.eligible_versions, scope, documents)
            if missing := documents - ok:
                raise ItemRejected(f"{len(missing)} document version(s) are not CLEAN and PARSED")
        if master_documents:
            ok = await asyncio.to_thread(
                self._guard.eligible_master_documents, scope, master_documents
            )
            if missing := master_documents - ok:
                raise ItemRejected(f"{len(missing)} master document(s) are not CLEAN and PARSED")

    @staticmethod
    def _log(event: str, scope: ProjectScope, **counts: int) -> None:
        log.info(
            event,
            extra={
                "tenant_id": str(scope.tenant_id),
                "project_id": str(scope.project_id),
                "correlation_id": correlation_id_var.get(),
                **counts,
            },
        )
