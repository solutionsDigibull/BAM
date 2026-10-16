"""Tenant- and project-scoped `llm_calls` access (E6.1). Rows are only ever added: a call is
history."""

from typing import Any

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.llm.models import LlmCall, LlmCallStatus


class LlmCallRepository(TenantScopedRepository[LlmCall]):
    model = LlmCall

    def insert(self, values: dict[str, Any]) -> LlmCall:
        """The tenant and project always come from the context, never from `values`."""
        row = LlmCall(**{**values, "tenant_id": self.ctx.tenant_id})
        if self.ctx.project_id is not None:
            row.project_id = self.ctx.project_id
        self.session.add(row)
        self.session.flush()
        return row

    def cached(
        self, temporal_workflow_id: str, activity_id: str, activity_attempt: int, prompt_id: str
    ) -> LlmCall | None:
        """The SUCCEEDED row of this activity attempt, if a replay can reuse it."""
        query = (
            self._scoped()
            .where(
                LlmCall.temporal_workflow_id == temporal_workflow_id,
                LlmCall.activity_id == activity_id,
                LlmCall.activity_attempt == activity_attempt,
                LlmCall.prompt_id == prompt_id,
                LlmCall.status == LlmCallStatus.SUCCEEDED,
                LlmCall.output.is_not(None),
            )
            .order_by(LlmCall.id.desc())
            .limit(1)
        )
        return self.session.scalars(query).one_or_none()

    def for_activity(self, temporal_workflow_id: str, activity_id: str) -> list[LlmCall]:
        query = (
            self._scoped()
            .where(
                LlmCall.temporal_workflow_id == temporal_workflow_id,
                LlmCall.activity_id == activity_id,
            )
            .order_by(LlmCall.id)
        )
        return list(self.session.scalars(query))
