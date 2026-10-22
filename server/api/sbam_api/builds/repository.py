"""Tenant- and project-scoped `code_attempts` access (E11.2)."""

import uuid
from typing import Any

from sbam_api.builds.models import CodeAttempt
from sbam_api.db.repository import TenantScopedRepository


class CodeAttemptRepository(TenantScopedRepository[CodeAttempt]):
    model = CodeAttempt

    def find(
        self, sub_skill_id: uuid.UUID, build_no: int, cycle: int, attempt: int
    ) -> CodeAttempt | None:
        """The row for one build stage key, if the stage already ran."""
        query = self._scoped().where(
            CodeAttempt.sub_skill_id == sub_skill_id,
            CodeAttempt.build_no == build_no,
            CodeAttempt.cycle == cycle,
            CodeAttempt.attempt == attempt,
        )
        return self.session.scalars(query).one_or_none()

    def record(self, values: dict[str, Any]) -> CodeAttempt:
        """Insert once per stage key; a retried activity gets the row it already wrote."""
        found = self.find(
            values["sub_skill_id"], values["build_no"], values["cycle"], values["attempt"]
        )
        if found is not None:
            return found
        return self.add(CodeAttempt(**values))
