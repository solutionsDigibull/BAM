"""Tenant- and project-scoped `code_attempts` access (E11.2)."""

import uuid
from typing import Any

from sqlalchemy import func, select

from sbam_api.audit.models import AuditLog
from sbam_api.builds.models import CodeAttempt, CodeAttemptStatus
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

    def newest_stored(self, sub_skill_id: uuid.UUID, before_build_no: int) -> CodeAttempt | None:
        """The newest attempt of an earlier build whose bodies passed the pre-check (#925)."""
        query = (
            self._scoped()
            .where(
                CodeAttempt.sub_skill_id == sub_skill_id,
                CodeAttempt.build_no < before_build_no,
                CodeAttempt.status.in_(
                    [CodeAttemptStatus.BODIES_GENERATED, CodeAttemptStatus.ASSEMBLED]
                ),
            )
            .order_by(
                CodeAttempt.build_no.desc(), CodeAttempt.cycle.desc(), CodeAttempt.attempt.desc()
            )
            .limit(1)
        )
        return self.session.scalars(query).first()

    def recovered_since(self, stored: CodeAttempt) -> bool:
        """True when the FDE recovered the Sub-Skill from BUILD_ERROR after `stored` was written
        (the `recover` transition's audit row): the next build starts from `stored` (#925)."""
        found = self.session.scalars(
            select(AuditLog.id)
            .where(
                AuditLog.tenant_id == stored.tenant_id,
                AuditLog.entity_type == "sub_skill",
                AuditLog.entity_id == str(stored.sub_skill_id),
                AuditLog.action == "recover",
                AuditLog.at > stored.created_at,
            )
            .limit(1)
        ).first()
        return found is not None

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
