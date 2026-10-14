"""Which table holds each state machine's entities (E10.1).

Feature modules register their `Stateful` model once, from their `models.py`:

    register_entity(MachineName.PROJECT_PIPELINE, Project)   # #54
    register_entity(MachineName.WORKFLOW, Workflow)          # #91

The pipeline's transition activity looks the model up here. Until a machine's table exists there is
nothing to transition, so `entity_model` returns None and the step is skipped (the run mirror still
records the stage). Tests register stand-in tables.
"""

from typing import Any

from sbam_contracts.state_machines import MachineName

_MODELS: dict[MachineName, type[Any]] = {}


def register_entity(machine: MachineName, model: type[Any]) -> None:
    if getattr(model, "machine", None) != machine:
        raise ValueError(f"{model.__name__}.machine must be {machine.value}")
    _MODELS[machine] = model


def unregister_entity(machine: MachineName) -> None:
    """Tests only: drop a stand-in."""
    _MODELS.pop(machine, None)


def entity_model(machine: MachineName) -> type[Any] | None:
    return _MODELS.get(machine)
