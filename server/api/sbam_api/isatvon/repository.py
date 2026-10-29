"""Tenant-scoped queries for Macro Skills (E8.2) and Sub-Skills (E8.3)."""

import uuid

from sqlalchemy import func

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.isatvon.models import MacroSkill, SubSkill, SubSkillEdit
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

    def macro_id_of(self, sub_id: uuid.UUID) -> uuid.UUID | None:
        """The Sub-Skill's Macro Skill (it never changes), read as a bare id: nothing is loaded or
        locked, so a caller can lock the Macro Skill first and the Sub-Skill second (E8.4, A-29)."""
        query = self._scoped().where(SubSkill.id == sub_id)
        return self.session.scalar(query.with_only_columns(SubSkill.macro_skill_id))

    def of_workflow(self, workflow_id: uuid.UUID) -> list[SubSkill]:
        """By Macro Skill (UUIDv7 ids follow the macros' creation order), then `order`."""
        query = self._scoped().where(SubSkill.workflow_id == workflow_id)
        return list(self.session.scalars(query.order_by(SubSkill.macro_skill_id, SubSkill.order)))

    def lowest_order(self, macro_skill_id: uuid.UUID) -> int:
        """The smallest `order` in the Macro Skill, deleted rows included (0 when there is none)."""
        query = self._scoped(include_deleted=True).where(SubSkill.macro_skill_id == macro_skill_id)
        lowest = self.session.scalar(query.with_only_columns(func.min(SubSkill.order)))
        return int(lowest or 0)

    def used_skill_ids(self, workflow_id: uuid.UUID) -> set[str]:
        """Every `skill_id` of the Workflow, deleted rows included (their ids are never reused)."""
        query = self._scoped(include_deleted=True).where(SubSkill.workflow_id == workflow_id)
        return set(self.session.scalars(query.with_only_columns(SubSkill.skill_id)))


class SubSkillEditRepository(TenantScopedRepository[SubSkillEdit]):
    """Use with a context that names the project. Rows are only inserted, never changed."""

    model = SubSkillEdit

    def of_sub_skill(self, sub_skill_id: uuid.UUID) -> list[SubSkillEdit]:
        """Oldest first (UUIDv7 ids follow the order they were written)."""
        query = self._scoped().where(SubSkillEdit.sub_skill_id == sub_skill_id)
        return list(self.session.scalars(query.order_by(SubSkillEdit.id)))
