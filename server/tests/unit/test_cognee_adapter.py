"""E5.1 Cognee adapter: scope, dataset naming, idempotent add, error mapping (fake Cognee)."""

import asyncio
import hashlib
import itertools
import logging
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.knowledge import (
    GraphEdge,
    GraphNode,
    ItemSource,
    KnowledgeItem,
    ProjectScope,
    SearchHit,
)
from sbam_workers.knowledge.cognee_adapter import (
    CogneeAdapter,
    CogneeUnavailable,
    DatasetNotFound,
    ItemRejected,
    Principal,
    ScopeError,
    classify,
    dataset_name,
)

T1, P1 = uuid.uuid4(), uuid.uuid4()
T2, P2 = uuid.uuid4(), uuid.uuid4()
SECRET = "s" * 32


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeGuard:
    def __init__(self, projects: dict[uuid.UUID, uuid.UUID], eligible: set[uuid.UUID]) -> None:
        self.projects = projects  # project -> tenant
        self.eligible = eligible

    def project_in_tenant(self, scope: ProjectScope) -> bool:
        return self.projects.get(scope.project_id) == scope.tenant_id

    def eligible_versions(self, scope: ProjectScope, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        return ids & self.eligible


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


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskBackend:
    return LocalDiskBackend(tmp_path)


@pytest.fixture
def cognee() -> FakeCognee:
    return FakeCognee()


def adapter(
    cognee: FakeCognee, storage: LocalDiskBackend, eligible: set[uuid.UUID] | None = None
) -> CogneeAdapter:
    guard = FakeGuard({P1: T1, P2: T2}, eligible or set())
    return CogneeAdapter(cognee, guard, storage, SECRET)


def scope(tenant: uuid.UUID = T1, project: uuid.UUID = P1) -> ProjectScope:
    return ProjectScope(tenant_id=tenant, project_id=project)


def stored(storage: LocalDiskBackend, sc: ProjectScope, text: str) -> KnowledgeItem:
    obj = storage.put(text.encode(), StorageScope(sc.tenant_id, sc.project_id), Kind.DERIVED, "x")
    return KnowledgeItem(
        source=ItemSource.DOCUMENT,
        source_id=uuid.uuid4(),
        sha256=obj.sha256,
        storage_key=obj.storage_key,
    )


# --- T01 dataset naming -----------------------------------------------------------------------


def test_e5_1_t01_dataset_name_is_derived_and_validated() -> None:
    assert dataset_name(scope()) == f"sbam_{T1}_{P1}"
    assert dataset_name(scope()) == dataset_name(scope())


@pytest.mark.parametrize("bad", ["../x", "", "not-a-uuid", str(uuid.uuid1())])
def test_e5_1_t01_invalid_project_id_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):  # a ValueError: the scope never exists
        ProjectScope(tenant_id=T1, project_id=bad)  # type: ignore[arg-type]


def test_e5_1_t01_no_cognee_call_without_a_scope(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    for bad in (None, {"tenant_id": T1, "project_id": P1}, f"sbam_{T1}_{P1}"):
        with pytest.raises(ScopeError):
            run(adapter(cognee, storage).ensure_dataset(bad))  # type: ignore[arg-type]
    assert cognee.calls == []


# --- T03 cross-tenant forgery -----------------------------------------------------------------


def test_e5_1_t03_project_of_another_tenant_is_refused_before_cognee(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    forged = scope(T1, P2)  # P2 belongs to T2
    a = adapter(cognee, storage)
    for call in (
        a.ensure_dataset(forged),
        a.add(forged, []),
        a.cognify(forged),
        a.search(forged, "x"),
        a.graph_page(forged),
        a.delete_dataset(forged),
    ):
        with pytest.raises(ScopeError):
            run(call)
    assert cognee.calls == []


# --- T08 idempotent add -----------------------------------------------------------------------


def test_e5_1_t08_same_item_twice_is_a_no_op(cognee: FakeCognee, storage: LocalDiskBackend) -> None:
    item = stored(storage, scope(), "RFQ for 500 PCBA boards")
    a = adapter(cognee, storage, {item.source_id})
    first = run(a.add(scope(), [item]))
    second = run(a.add(scope(), [item]))
    assert first.added == [f"{item.source_id}_{item.sha256}"] and first.skipped == []
    assert second.added == [] and second.skipped == first.added
    assert cognee.calls.count("add") == 1
    assert len(next(iter(cognee.data.values()))) == 1


def test_e5_1_same_item_twice_in_one_call_is_added_once(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    item = stored(storage, scope(), "BOM")
    result = run(adapter(cognee, storage, {item.source_id}).add(scope(), [item, item]))
    assert len(result.added) == 1 and len(result.skipped) == 1


def test_e5_1_items_of_another_project_are_rejected(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    item = stored(storage, scope(T2, P2), "project B data")
    with pytest.raises(ItemRejected):
        run(adapter(cognee, storage, {item.source_id}).add(scope(), [item]))
    assert "add" not in cognee.calls


def test_e5_1_only_clean_parsed_versions_are_added(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    good = stored(storage, scope(), "parsed")
    pending = stored(storage, scope(), "still scanning")
    with pytest.raises(ItemRejected, match="1 document version"):
        run(adapter(cognee, storage, {good.source_id}).add(scope(), [good, pending]))
    assert "add" not in cognee.calls  # all-or-nothing


def test_e5_1_masters_item_needs_no_document_check(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    doc = stored(storage, scope(), '{"node_id": "ORG-001"}')
    masters = doc.model_copy(update={"source": ItemSource.MASTERS})
    assert len(run(adapter(cognee, storage).add(scope(), [masters])).added) == 1


def test_e5_1_sha256_mismatch_is_rejected(cognee: FakeCognee, storage: LocalDiskBackend) -> None:
    item = stored(storage, scope(), "real")
    wrong = item.model_copy(update={"sha256": hashlib.sha256(b"other").hexdigest()})
    with pytest.raises(ItemRejected, match="sha256"):
        run(adapter(cognee, storage, {item.source_id}).add(scope(), [wrong]))


def test_e5_1_logs_carry_ids_never_document_text(
    cognee: FakeCognee, storage: LocalDiskBackend, caplog: pytest.LogCaptureFixture
) -> None:
    item = stored(storage, scope(), "SECRET-PRICE-991")
    caplog.set_level(logging.DEBUG)
    a = adapter(cognee, storage, {item.source_id})
    run(a.add(scope(), [item]))
    run(a.search(scope(), "SECRET-PRICE-991"))
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "SECRET-PRICE-991" not in text
    assert str(P1) in text


# --- isolation, per-project users (unit side of T02) ------------------------------------------


def test_e5_1_each_project_has_its_own_cognee_user(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    a_item = stored(storage, scope(), "ZEBRA-7731")
    b_item = stored(storage, scope(T2, P2), "other")
    a = adapter(cognee, storage, {a_item.source_id, b_item.source_id})
    run(a.add(scope(), [a_item]))
    run(a.add(scope(T2, P2), [b_item]))
    assert run(a.search(scope(T2, P2), "ZEBRA-7731")) == []
    assert len(run(a.search(scope(), "ZEBRA-7731"))) == 1
    users = {email for email, _ in cognee.data}
    assert len(users) == 2


def test_e5_1_user_password_is_derived_and_never_shown() -> None:
    a = CogneeAdapter(FakeCognee(), FakeGuard({}, set()), None, SECRET)  # type: ignore[arg-type]
    one = a._principal(scope())
    assert one == a._principal(scope())
    assert one.password != a._principal(scope(T2, P2)).password
    assert one.password not in repr(one)


# --- errors (unit side of T07) ----------------------------------------------------------------


def test_e5_1_t07_bifrost_down_is_retryable_cognee_unavailable(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    a = adapter(cognee, storage)
    run(a.ensure_dataset(scope()))
    try:
        raise RuntimeError("pipeline failed") from EmbeddingException("Bifrost unreachable")
    except RuntimeError as wrapped:
        cognee.fail = wrapped
    with pytest.raises(CogneeUnavailable):
        run(a.cognify(scope()))


def test_e5_1_missing_dataset_is_not_found(cognee: FakeCognee, storage: LocalDiskBackend) -> None:
    with pytest.raises(DatasetNotFound):
        run(adapter(cognee, storage).cognify(scope()))


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (EmbeddingException("x"), CogneeUnavailable),
        (ConnectionError("x"), CogneeUnavailable),
        (TimeoutError("x"), CogneeUnavailable),
        (DatasetNotFoundError("x"), DatasetNotFound),
        (type("PermissionDeniedError", (Exception,), {})("x"), DatasetNotFound),
        (type("ServiceUnavailable", (Exception,), {"status_code": 503})("x"), CogneeUnavailable),
    ],
)
def test_e5_1_error_classification(exc: Exception, expected: type[Exception]) -> None:
    assert isinstance(classify(exc), expected)


def test_e5_1_validation_422_is_not_retryable() -> None:
    exc = type("CogneeValidationError", (Exception,), {"status_code": 422})("bad input")
    assert classify(exc) is exc


# --- search and graph paging ------------------------------------------------------------------


def test_e5_1_graph_page_walks_every_node_and_edge_once(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    items = [stored(storage, scope(), f"doc {i}") for i in range(5)]
    a = adapter(cognee, storage, {i.source_id for i in items})
    run(a.add(scope(), items))
    nodes, edges, cursor = [], [], None
    while True:
        page = run(a.graph_page(scope(), cursor, limit=2))
        nodes += page.nodes
        edges += page.edges
        assert (page.total_nodes, page.total_edges) == (5, 4)
        if (cursor := page.next_cursor) is None:
            break
    assert len({n.id for n in nodes}) == 5 and len(edges) == 4


@pytest.mark.parametrize(("cursor", "limit"), [("x", 10), ("-1", 10), (None, 0), (None, 501)])
def test_e5_1_graph_page_rejects_bad_paging(
    cognee: FakeCognee, storage: LocalDiskBackend, cursor: str | None, limit: int
) -> None:
    with pytest.raises(ValueError):
        run(adapter(cognee, storage).graph_page(scope(), cursor, limit))


BAD_SEARCH = [("", 5), (" ", 5), ("x" * 1001, 5), ("q", 0), ("q", 51)]


@pytest.mark.parametrize(("query", "top_k"), BAD_SEARCH)
def test_e5_1_search_rejects_bad_input(
    cognee: FakeCognee, storage: LocalDiskBackend, query: str, top_k: int
) -> None:
    with pytest.raises(ValueError):
        run(adapter(cognee, storage).search(scope(), query, top_k))


def test_e5_1_delete_dataset_removes_the_project_data(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    a = adapter(cognee, storage)
    run(a.ensure_dataset(scope()))
    run(a.delete_dataset(scope()))
    assert cognee.data == {}
    with pytest.raises(DatasetNotFound):
        run(a.delete_dataset(scope()))
