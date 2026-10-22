"""A fake Cognee backend for the knowledge tests (E5.1, E5.2): datasets keyed by (user, dataset),
like Cognee with access control on. No Cognee import, no Docker."""

import itertools

from sbam_contracts.knowledge import GraphEdge, GraphNode, SearchHit
from sbam_workers.knowledge.cognee_adapter import Principal


class EmbeddingException(Exception):  # noqa: N818 - same name as Cognee's
    status_code = 422


class DatasetNotFoundError(Exception):
    pass


class FakeCognee:
    """Datasets keyed by (user email, dataset name), like Cognee with access control on."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.data: dict[tuple[str, str], dict[str, bytes]] = {}
        self.fail: Exception | None = None

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail is not None:
            raise self.fail

    def _ds(self, who: Principal, dataset: str) -> dict[str, bytes]:
        if (who.email, dataset) not in self.data:
            raise DatasetNotFoundError(dataset)
        return self.data[(who.email, dataset)]

    async def ensure_dataset(self, who: Principal, dataset: str) -> None:
        self._call("ensure_dataset")
        self.data.setdefault((who.email, dataset), {})

    async def item_names(self, who: Principal, dataset: str) -> set[str]:
        self._call("item_names")
        return set(self._ds(who, dataset))

    async def add(self, who: Principal, dataset: str, name: str, data: bytes) -> None:
        self._call("add")
        self._ds(who, dataset)[name] = data

    async def cognify(self, who: Principal, dataset: str) -> None:
        self._call("cognify")
        self._ds(who, dataset)

    async def search(self, who: Principal, dataset: str, query: str, top_k: int) -> list[SearchHit]:
        self._call("search")
        items = self._ds(who, dataset).items()
        hits = [(k, v.decode()) for k, v in items]
        return [SearchHit(id=k, text=t, source_id=k) for k, t in hits if query in t]

    async def graph(self, who: Principal, dataset: str) -> tuple[list[GraphNode], list[GraphEdge]]:
        self._call("graph")
        names = sorted(self._ds(who, dataset))
        nodes = [GraphNode(id=n) for n in names]
        edges = [GraphEdge(source=a, target=b, label="next") for a, b in itertools.pairwise(names)]
        return nodes, edges

    async def delete_dataset(self, who: Principal, dataset: str) -> None:
        self._call("delete_dataset")
        self._ds(who, dataset)
        del self.data[(who.email, dataset)]
