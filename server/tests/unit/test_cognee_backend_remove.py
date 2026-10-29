"""#450: the real backend's `remove` deletes exactly the named items through Cognee's API.

Cognee itself is not run here (it needs its databases and Bifrost); its `datasets` API is replaced
by a recorder, so the test pins which calls `CogneeLibrary.remove` makes: one `delete_data` per
named item, by the item's own id, in the project's dataset, as the project's user.
"""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import cognee
import pytest
from sbam_workers.knowledge import cognee_backend
from sbam_workers.knowledge.cognee_adapter import Principal

WHO = Principal(email="p-1@projects.sbam.example.com", password="-")  # noqa: S106 - a test value
DATASET_ID, USER = uuid.uuid4(), object()


class Datasets:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.deleted: list[tuple[uuid.UUID, uuid.UUID, Any]] = []

    async def list_data(self, dataset_id: uuid.UUID, user: Any) -> list[SimpleNamespace]:
        assert (dataset_id, user) == (DATASET_ID, USER)
        return self.rows

    async def delete_data(self, dataset_id: uuid.UUID, data_id: uuid.UUID, user: Any) -> None:
        self.deleted.append((dataset_id, data_id, user))


def test_450_remove_deletes_each_named_item_by_its_id(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [SimpleNamespace(id=uuid.uuid4(), name=n) for n in ("keep_a", "gone_b", "gone_c")]
    datasets = Datasets(rows)
    monkeypatch.setattr(cognee, "datasets", datasets)
    library = cognee_backend.CogneeLibrary()
    permissions: list[str] = []

    async def dataset(who: Principal, name: str, permission: str = "read") -> Any:
        permissions.append(permission)
        return SimpleNamespace(id=DATASET_ID)

    async def user(who: Principal) -> Any:
        return USER

    monkeypatch.setattr(library, "_dataset", dataset)
    monkeypatch.setattr(library, "_user", user)
    asyncio.run(library.remove(WHO, "sbam_t_p", {"gone_b", "gone_c", "absent"}))
    assert permissions == ["delete"]  # Cognee checks the user may delete in this dataset
    assert sorted(d[1] for d in datasets.deleted) == sorted([rows[1].id, rows[2].id])
    assert all(d[0] == DATASET_ID and d[2] is USER for d in datasets.deleted)
