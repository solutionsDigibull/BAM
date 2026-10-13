"""`Stateful`: the mixin every entity with a lifecycle uses (TD §14).

    class SubSkill(Stateful, ProjectOwned, Base):
        __tablename__ = "sub_skills"
        __table_args__ = (state_check(MachineName.SUB_SKILL),)
        machine = MachineName.SUB_SKILL

The `state` column is never written directly: only `state_machine.apply_transition` changes it.
"""

from typing import ClassVar

from sbam_contracts.state_machines import MachineName, check_constraint_values
from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import status_check


class Stateful:
    machine: ClassVar[MachineName]
    state: Mapped[str] = mapped_column(String(32), index=True)


def state_check(machine: MachineName) -> CheckConstraint:
    """`CHECK (state IN (...))` from the machine's states (E0.6 tables)."""
    return status_check(check_constraint_values(machine), column="state")
