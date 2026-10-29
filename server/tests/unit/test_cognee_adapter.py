"""E5.1 Cognee adapter: scope, dataset naming, idempotent add, error mapping (fake Cognee)."""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from cognee_fake import DatasetNotFoundError, EmbeddingException, FakeCognee
from pydantic import ValidationError
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.knowledge import (
    ItemSource,
    KnowledgeItem,
    ProjectScope,
)
from sbam_workers.knowledge.cognee_adapter import (
    CogneeAdapter,
    CogneeUnavailable,
    DatasetNotFound,
    ItemRejected,
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

    def eligible_master_documents(self, scope: ProjectScope, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        return ids & self.eligible


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


def test_e5_1_sbam_uuid7_ids_are_accepted() -> None:
    tenant, project = uuid7(), uuid7()  # SBAM's ids (sbam_api.core.ids)
    assert dataset_name(ProjectScope(tenant_id=tenant, project_id=project)) == (
        f"sbam_{tenant}_{project}"
    )


def test_e5_1_t01_dataset_name_is_derived_and_validated() -> None:
    assert dataset_name(scope()) == f"sbam_{T1}_{P1}"
    assert dataset_name(scope()) == dataset_name(scope())


@pytest.mark.parametrize("bad", ["../x", "", "not-a-uuid", "sbam_x"])
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


def test_e5_1b_t01_every_adapter_log_line_carries_all_three_ids(
    cognee: FakeCognee, storage: LocalDiskBackend, caplog: pytest.LogCaptureFixture
) -> None:
    item = stored(storage, scope(), "SECRET-PRICE-991")
    caplog.set_level(logging.DEBUG)
    token = correlation_id_var.set("corr-351")
    try:
        a = adapter(cognee, storage, {item.source_id})
        run(a.ensure_dataset(scope()))
        run(a.add(scope(), [item]))
        run(a.cognify(scope()))
        run(a.search(scope(), "SECRET-PRICE-991"))
    finally:
        correlation_id_var.reset(token)
    records = [r for r in caplog.records if r.name == "sbam_workers.knowledge.cognee"]
    assert len(records) >= 4
    for r in records:
        assert r.__dict__["tenant_id"] == str(T1)
        assert r.__dict__["project_id"] == str(P1)
        assert r.__dict__["correlation_id"] == "corr-351"
    assert "SECRET-PRICE-991" not in " ".join(f"{r.getMessage()} {r.__dict__}" for r in records)


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


# --- E3.8b: parsed master documents as master context ---------------------------------------------

ZERO = uuid.UUID(int=0)


def master_document(
    storage: LocalDiskBackend, sc: ProjectScope, text: str, *, tenant_wide: bool = False
) -> KnowledgeItem:
    """A parsed master document's `doc.md`: under the project, or the tenant's all-zero project."""
    project = ZERO if tenant_wide else sc.project_id
    obj = storage.put(text.encode(), StorageScope(sc.tenant_id, project), Kind.DERIVED, "x")
    return KnowledgeItem(
        source=ItemSource.MASTER_DOCUMENT,
        source_id=uuid.uuid4(),
        sha256=obj.sha256,
        storage_key=obj.storage_key,
    )


def test_e3_8b_a_parsed_master_document_of_the_project_is_added(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    item = master_document(storage, scope(), "Costing standard: scrap is 1.5 percent")
    a = adapter(cognee, storage, {item.source_id})
    assert run(a.add(scope(), [item])).added == [f"{item.source_id}_{item.sha256}"]


def test_e3_8b_a_tenant_wide_master_document_is_added_to_any_project_of_the_tenant(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    item = master_document(storage, scope(), "Company policy", tenant_wide=True)
    a = adapter(cognee, storage, {item.source_id})
    assert len(run(a.add(scope(), [item])).added) == 1
    other_project = scope(T1, uuid.uuid4())  # a second project of the same tenant
    guard = FakeGuard({other_project.project_id: T1}, {item.source_id})
    again = CogneeAdapter(cognee, guard, storage, SECRET)
    assert len(run(again.add(other_project, [item])).added) == 1


def test_e3_8b_another_tenants_tenant_wide_document_is_rejected(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    stranger = master_document(storage, scope(T2, P2), "their policy", tenant_wide=True)
    with pytest.raises(ItemRejected, match="not stored under this project"):
        run(adapter(cognee, storage, {stranger.source_id}).add(scope(), [stranger]))
    assert "add" not in cognee.calls


def test_e3_8b_another_projects_master_document_is_rejected(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    item = master_document(storage, scope(T1, uuid.uuid4()), "project B context")
    with pytest.raises(ItemRejected, match="not stored under this project"):
        run(adapter(cognee, storage, {item.source_id}).add(scope(), [item]))


def test_e3_8b_only_master_documents_may_use_the_tenant_wide_prefix(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    item = master_document(storage, scope(), "policy", tenant_wide=True)
    as_fde_document = item.model_copy(update={"source": ItemSource.DOCUMENT})
    with pytest.raises(ItemRejected, match="not stored under this project"):
        run(adapter(cognee, storage, {item.source_id}).add(scope(), [as_fde_document]))
    as_masters = item.model_copy(update={"source": ItemSource.MASTERS})
    with pytest.raises(ItemRejected, match="not stored under this project"):
        run(adapter(cognee, storage).add(scope(), [as_masters]))


def test_e3_8b_a_master_document_that_is_not_clean_and_parsed_is_rejected(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    good = master_document(storage, scope(), "parsed")
    pending = master_document(storage, scope(), "not parsed yet")
    with pytest.raises(ItemRejected, match="1 master document"):
        run(adapter(cognee, storage, {good.source_id}).add(scope(), [good, pending]))
    assert "add" not in cognee.calls  # all-or-nothing


def test_e3_8b_a_traversal_in_a_master_document_key_is_rejected(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    item = master_document(storage, scope(), "policy", tenant_wide=True)
    sneaky = item.model_copy(
        update={"storage_key": item.storage_key.replace("/derived/", "/derived/../../")}
    )
    with pytest.raises(ItemRejected):
        run(adapter(cognee, storage, {item.source_id}).add(scope(), [sneaky]))


def test_e3_8b_a_master_document_with_a_wrong_hash_is_rejected(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    item = master_document(storage, scope(), "real")
    wrong = item.model_copy(update={"sha256": hashlib.sha256(b"other").hexdigest()})
    with pytest.raises(ItemRejected, match="sha256"):
        run(adapter(cognee, storage, {item.source_id}).add(scope(), [wrong]))


# --- #450: prune ---------------------------------------------------------------------------------


def test_450_prune_removes_only_the_items_outside_keep(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    a = adapter(cognee, storage)
    run(a.ensure_dataset(scope()))
    [data] = cognee.data.values()
    data.update({"kept_1": b"x", "stale_1": b"y", "stale_2": b"z"})
    assert run(a.prune(scope(), {"kept_1", "not_present"})) == ["stale_1", "stale_2"]
    assert set(data) == {"kept_1"}


def test_450_prune_with_nothing_stale_calls_no_delete(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    a = adapter(cognee, storage)
    run(a.ensure_dataset(scope()))
    assert run(a.prune(scope(), set())) == []
    assert "remove" not in cognee.calls


def test_450_prune_checks_the_scope_before_touching_cognee(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    calls_before = list(cognee.calls)
    with pytest.raises(ScopeError):
        run(adapter(cognee, storage).prune(scope(T1, P2), set()))  # P2 is T2's project
    assert cognee.calls == calls_before


def test_450_prune_with_cognee_down_is_retryable(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    a = adapter(cognee, storage)
    run(a.ensure_dataset(scope()))
    cognee.fail = EmbeddingException("Bifrost down")
    with pytest.raises(CogneeUnavailable):
        run(a.prune(scope(), set()))


def test_450_prune_of_a_missing_dataset_is_not_found(
    cognee: FakeCognee, storage: LocalDiskBackend
) -> None:
    with pytest.raises(DatasetNotFound):
        run(adapter(cognee, storage).prune(scope(), set()))
