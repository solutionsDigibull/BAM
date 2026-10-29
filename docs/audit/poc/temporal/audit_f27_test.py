"""F-27: a rebuild after a document is removed from scope still leaves its content in the Cognee dataset."""
import asyncio
import sys
sys.path.insert(0, "tests/unit")
from test_cognee_adapter import FakeCognee, SECRET, adapter, scope, stored, storage  # noqa: F401
from sbam_workers.knowledge.cognee_adapter import dataset_name


def test_f27_removed_document_survives_rebuild(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from sbam_api.storage.local import LocalDiskBackend
    st = LocalDiskBackend(tmp_path); cg = FakeCognee(); sc = scope()
    a, b, c = (stored(st, sc, t) for t in ("document A: customer salary table", "document B", "document C"))
    ad = adapter(cg, st, {a.source_id, b.source_id, c.source_id})
    asyncio.run(ad.add(sc, [a, b]))                      # build 1: A and B in scope
    asyncio.run(ad.add(sc, [b, c]))                      # build 2: A was deleted, C added
    names = next(iter(cg.data.values()))
    print("items in the project's dataset after the rebuild:", len(names), "| A still present:", any(k.startswith(str(a.source_id)) for k in names))
    assert any(k.startswith(str(a.source_id)) for k in names)   # documents the defect: A is still there
