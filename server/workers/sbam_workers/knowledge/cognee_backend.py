"""The real `CogneeBackend`: Cognee 1.6.5 as a library inside worker-knowledge (ADR 0001).

Cognee reads its configuration from the environment when it is first imported, so it is imported
here lazily: only worker-knowledge (after its preflight check) ever loads it. Each call runs as
the project's own Cognee user. Items are written to a temp file named after their key, because
Cognee names a data item after its file stem; the key is what makes `add` idempotent.
"""

import asyncio
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from sbam_api.storage.factory import make_backend
from sbam_contracts.knowledge import GraphEdge, GraphNode, SearchHit

from sbam_workers import mirror
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter, DatasetNotFound, Principal
from sbam_workers.knowledge.scope import SqlScopeGuard
from sbam_workers.llm import meter
from sbam_workers.llm.store import SqlCallStore
from sbam_workers.settings import WorkerSettings

COGNEE_LLM_PROVIDER = "custom"
_ready: asyncio.Lock | None = None
_setup_done = False


class CogneeLibrary:
    def __init__(self) -> None:
        self._users: dict[str, Any] = {}

    async def _setup(self) -> None:
        """Create Cognee's relational tables on first use (idempotent)."""
        global _ready, _setup_done
        _ready = _ready or asyncio.Lock()
        async with _ready:
            if not _setup_done:
                import cognee
                from cognee.modules.engine.operations.setup import setup

                # LLM_PROVIDER is SBAM's mock/bifrost switch in every worker, so Cognee's own
                # provider type is set here: an OpenAI-compatible endpoint, which is Bifrost.
                cognee.config.set_llm_provider(COGNEE_LLM_PROVIDER)
                await setup()
                _setup_done = True

    async def _user(self, who: Principal) -> Any:
        if who.email not in self._users:
            await self._setup()
            from cognee.modules.users.methods import create_user, get_user_by_email

            user = await get_user_by_email(who.email)
            if user is None:
                user = await create_user(who.email, who.password, is_verified=True)
            self._users[who.email] = user
        return self._users[who.email]

    async def _dataset(self, who: Principal, dataset: str, permission: str = "read") -> Any:
        from cognee.modules.data.methods import get_authorized_dataset_by_name

        found = await get_authorized_dataset_by_name(dataset, await self._user(who), permission)
        if found is None:
            raise DatasetNotFound("dataset not found")
        return found

    async def ensure_dataset(self, who: Principal, dataset: str) -> None:
        from cognee.modules.data.methods import (
            create_authorized_dataset,
            get_authorized_dataset_by_name,
        )

        user = await self._user(who)
        if await get_authorized_dataset_by_name(dataset, user, "write") is None:
            await create_authorized_dataset(dataset, user)

    async def item_names(self, who: Principal, dataset: str) -> set[str]:
        import cognee

        found = await self._dataset(who, dataset)
        rows = await cognee.datasets.list_data(found.id, await self._user(who))
        return {str(row.name) for row in rows}

    async def add(self, who: Principal, dataset: str, name: str, data: bytes) -> None:
        import cognee

        with tempfile.TemporaryDirectory(prefix="sbam-cognee-") as tmp:
            path = Path(tmp) / f"{name}.md"
            path.write_bytes(data)
            await cognee.add(str(path), dataset_name=dataset, user=await self._user(who))

    async def remove(self, who: Principal, dataset: str, names: set[str]) -> None:
        """`datasets.delete_data` per item (soft mode: Cognee's documented default; it removes
        the nodes and edges the item alone sourced, through its provenance ledger)."""
        import cognee

        found = await self._dataset(who, dataset, "delete")
        user = await self._user(who)
        for row in await cognee.datasets.list_data(found.id, user):
            if str(row.name) in names:
                await cognee.datasets.delete_data(found.id, row.id, user)

    async def cognify(self, who: Principal, dataset: str) -> None:
        import cognee

        await self._dataset(who, dataset, "write")
        await cognee.cognify(datasets=[dataset], user=await self._user(who))

    async def search(self, who: Principal, dataset: str, query: str, top_k: int) -> list[SearchHit]:
        import cognee
        from cognee import SearchType

        await self._dataset(who, dataset)
        results = await cognee.search(
            query,
            query_type=SearchType.CHUNKS,
            datasets=[dataset],
            user=await self._user(who),
            top_k=top_k,
        )
        return [_hit(item) for item in _flatten(results)]

    async def graph(self, who: Principal, dataset: str) -> tuple[list[GraphNode], list[GraphEdge]]:
        from cognee.context_global_variables import set_database_global_context_variables
        from cognee.infrastructure.databases.graph import get_graph_engine

        found = await self._dataset(who, dataset)
        async with set_database_global_context_variables(found.id, found.owner_id):
            engine = await get_graph_engine()
            nodes, edges = await engine.get_graph_data()
        return (
            [_node(node_id, props) for node_id, props in nodes],
            [GraphEdge(source=str(e[0]), target=str(e[1]), label=str(e[2])) for e in edges],
        )

    async def delete_dataset(self, who: Principal, dataset: str) -> None:
        import cognee

        found = await self._dataset(who, dataset, "delete")
        await cognee.datasets.empty_dataset(found.id, await self._user(who))


@lru_cache(maxsize=1)
def default_adapter() -> CogneeAdapter:
    """The adapter the knowledge activities use (E5.2+): real Cognee, the DB guard, storage."""
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    # Cognee's own model calls reach Bifrost without passing LlmClient: count them (#420).
    meter.install(SqlCallStore(mirror.sessions()), settings)
    return CogneeAdapter(
        CogneeLibrary(),
        SqlScopeGuard(mirror.sessions()),
        make_backend(settings),
        os.environ["COGNEE_USER_SECRET"],  # checked by the preflight before anything runs
    )


def _flatten(results: Any) -> list[Any]:
    out: list[Any] = []
    for result in results or []:
        if isinstance(result, dict) and "search_result" in result:
            out.extend(result["search_result"] or [])
        else:
            out.append(result)
    return out


def _hit(item: Any) -> SearchHit:
    if not isinstance(item, dict):
        return SearchHit(id="", text=str(item))
    document = item.get("is_part_of") or {}
    return SearchHit(
        id=str(item.get("id", "")),
        text=str(item.get("text", "")),
        score=item.get("score"),
        source_id=str(document.get("name")) if isinstance(document, dict) and document else None,
    )


def _node(node_id: Any, props: Any) -> GraphNode:
    props = props if isinstance(props, dict) else {}
    simple = json.loads(json.dumps(props, default=str))  # dates, UUIDs -> JSON values
    return GraphNode(
        id=str(node_id),
        type=simple.get("type"),
        name=simple.get("name"),
        properties=simple,
    )
