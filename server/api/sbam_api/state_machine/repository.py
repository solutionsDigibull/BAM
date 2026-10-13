"""Tenant-scoped, row-locking access to any `Stateful` entity (built on #36's repository base)."""

import uuid
from typing import Any

from sbam_api.db.repository import TenantScopedRepository


class StatefulRepository(TenantScopedRepository[Any]):
    def get_for_update(self, id: uuid.UUID) -> Any | None:
        """Like `get`, but `SELECT … FOR UPDATE`: a concurrent transition on the same row waits
        for this transaction, then sees its result (E1.8-T04)."""
        query = self._scoped().where(self.model.id == id).with_for_update()
        return self.session.scalars(query).one_or_none()


_REPOSITORIES: dict[type[Any], type[StatefulRepository]] = {}


def repository_for(model: type[Any]) -> type[StatefulRepository]:
    if model not in _REPOSITORIES:
        name = f"{model.__name__}StateRepository"
        _REPOSITORIES[model] = type(name, (StatefulRepository,), {"model": model})
    return _REPOSITORIES[model]
