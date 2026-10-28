"""Tenant-scoped queries for Macro Skills (E8.2) and Sub-Skills (E8.3)."""

import uuid

from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.state_machine.repository import StatefulRepository


class MacroSkillRepository(StatefulRepository):
    """Use with a context that names the project."""

    model = MacroSkill

    def of_workflow(self, workflow_id: uuid.UUID) -> list[MacroSkill]:
        """In `order`."""
        query = self._scoped().where(MacroSkill.workflow_id == workflow_id)
        return list(self.session.scalars(query.order_by(MacroSkill.order)))


class SubSkillRepository(StatefulRepository):
    """Use with a context that names the project."""

    model = SubSkill

    def of_macro(self, macro_skill_id: uuid.UUID) -> list[SubSkill]:
        """In `order`."""
        query = self._scoped().where(SubSkill.macro_skill_id == macro_skill_id)
        return list(self.session.scalars(query.order_by(SubSkill.order)))

    def of_workflow(self, workflow_id: uuid.UUID) -> list[SubSkill]:
        """By Macro Skill (UUIDv7 ids follow the macros' creation order), then `order`."""
        query = self._scoped().where(SubSkill.workflow_id == workflow_id)
        return list(self.session.scalars(query.order_by(SubSkill.macro_skill_id, SubSkill.order)))
