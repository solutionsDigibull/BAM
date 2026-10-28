"""Tenant-scoped queries for Macro Skills (E8.2)."""

import uuid

from sbam_api.isatvon.models import MacroSkill
from sbam_api.state_machine.repository import StatefulRepository


class MacroSkillRepository(StatefulRepository):
    """Use with a context that names the project."""

    model = MacroSkill

    def of_workflow(self, workflow_id: uuid.UUID) -> list[MacroSkill]:
        """In `order`."""
        query = self._scoped().where(MacroSkill.workflow_id == workflow_id)
        return list(self.session.scalars(query.order_by(MacroSkill.order)))
