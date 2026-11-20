"""Tenant- and project-scoped access to `project_token_caps` (#776)."""

import uuid

from sbam_contracts.budget import BudgetCaps
from sqlalchemy.orm import Session

from sbam_api.db.repository import TenantContext, TenantScopedRepository
from sbam_api.token_budget.models import ProjectTokenCapsRow

FIELDS = (
    "sub_skill_tokens",
    "workflow_tokens",
    "knowledge_tokens",
    "first_alert_pct",
    "second_alert_pct",
)


class TokenCapsRepository(TenantScopedRepository[ProjectTokenCapsRow]):
    model = ProjectTokenCapsRow

    def current(self) -> ProjectTokenCapsRow | None:
        """The project's row (the context names the project), or None for the platform defaults."""
        return self.session.scalars(self._scoped().limit(1)).one_or_none()

    def create(self, caps: BudgetCaps, actor: str) -> ProjectTokenCapsRow:
        row = ProjectTokenCapsRow(**caps.model_dump(), raised_builds=[], created_by=actor)
        return self.add(row)


def effective_caps(session: Session, tenant_id: uuid.UUID, project_id: uuid.UUID) -> BudgetCaps:
    """The project's caps: its own row, else the platform defaults."""
    row = TokenCapsRepository(session, TenantContext(tenant_id, project_id)).current()
    return BudgetCaps() if row is None else caps_of(row)


def caps_of(row: ProjectTokenCapsRow) -> BudgetCaps:
    return BudgetCaps(**{name: getattr(row, name) for name in FIELDS})
