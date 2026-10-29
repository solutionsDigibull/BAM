"""E0.15: the state columns' CHECK constraints against the state machines (TD §14).

Every model with the `Stateful` mixin gets `CHECK (state IN (...))` from `state_check(machine)`,
built from `check_constraint_values(machine)`. These helpers find those models from the ORM
registry (a new stateful table is covered without touching the tests), read a constraint's
values back from its SQL and compare them with the machine.
"""

import importlib
import importlib.util
import pkgutil
import re
from dataclasses import dataclass

import sbam_api
from sbam_api.db.base import Base
from sbam_api.state_machine.stateful import Stateful
from sbam_contracts.state_machines import MachineName

_QUOTED = re.compile(r"'((?:[^']|'')*)'")


@dataclass(frozen=True)
class StatefulTable:
    table: str  # without the schema
    machine: MachineName

    @property
    def constraint(self) -> str:
        """The name `state_check` gives it under the naming convention (ck_<table>_<name>)."""
        return f"ck_{self.table}_state_valid"


def import_models() -> None:
    """Like migrations/env.py: every `sbam_api.<module>.models`, so the registry is complete."""
    for info in pkgutil.iter_modules(sbam_api.__path__):
        if info.ispkg and importlib.util.find_spec(f"sbam_api.{info.name}.models"):
            importlib.import_module(f"sbam_api.{info.name}.models")


def stateful_tables() -> list[StatefulTable]:
    import_models()
    found = {
        StatefulTable(mapper.class_.__tablename__, mapper.class_.machine)
        for mapper in Base.registry.mappers
        if issubclass(mapper.class_, Stateful) and getattr(mapper.class_, "machine", None)
    }
    return sorted(found, key=lambda t: t.table)


def check_values(definition: str) -> tuple[str, ...]:
    """The quoted values of a CHECK definition, in order. Both PostgreSQL's own spelling
    (`CHECK (((state)::text = ANY ((ARRAY['A'::character varying, ...])::text[])))`) and the
    `state IN ('A', 'B')` the model writes are understood."""
    return tuple(v.replace("''", "'") for v in _QUOTED.findall(definition))


@dataclass(frozen=True)
class Drift:
    missing: tuple[str, ...]  # states of the machine the database refuses
    extra: tuple[str, ...]  # values the database allows that the machine does not have

    def __bool__(self) -> bool:
        return bool(self.missing or self.extra)


def drift(database: tuple[str, ...], machine: tuple[str, ...]) -> Drift:
    return Drift(
        missing=tuple(v for v in machine if v not in database),
        extra=tuple(v for v in database if v not in machine),
    )
