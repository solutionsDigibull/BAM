"""Tenant- and project-scoped `code_attempts` access (E11.2)."""

import uuid
from typing import Any

from sqlalchemy import func

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

    def of_sub_skill(
        self,
        workflow_id: uuid.UUID,
        sub_skill_id: uuid.UUID,
        build_no: int | None = None,
        cycle: int | None = None,
    ) -> list[CodeAttempt]:
        """One build cycle's attempts, attempt 0 first. Default: the newest build, newest cycle."""
        base = self._scoped().where(
            CodeAttempt.workflow_id == workflow_id, CodeAttempt.sub_skill_id == sub_skill_id
        )
        if build_no is None:
            build_no = self.session.scalar(base.with_only_columns(func.max(CodeAttempt.build_no)))
        if build_no is None:
            return []
        base = base.where(CodeAttempt.build_no == build_no)
        if cycle is None:
            cycle = self.session.scalar(base.with_only_columns(func.max(CodeAttempt.cycle)))
        if cycle is None:
            return []
        query = base.where(CodeAttempt.cycle == cycle).order_by(CodeAttempt.attempt)
        return list(self.session.scalars(query))

    def record(self, values: dict[str, Any]) -> CodeAttempt:
        """Insert once per stage key; a retried activity gets the row it already wrote."""
        found = self.find(
            values["sub_skill_id"], values["build_no"], values["cycle"], values["attempt"]
        )
        if found is not None:
            return found
        return self.add(CodeAttempt(**values))
