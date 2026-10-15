"""Deterministic ids for stub activities (E10.1): the same run + step always gives the same id, so
an activity retry or a resumed pipeline never invents a second Workflow / Sub-Skill. Real
activities upsert by their natural keys instead (TD §13.4)."""

from uuid import UUID, uuid5

from sbam_contracts.build import BuildStageInput
from sbam_contracts.runs import RunInput


def stub_id(inp: RunInput, step: str) -> UUID:
    return uuid5(inp.run_id, step)


def stage_stub_id(inp: BuildStageInput, step: str) -> UUID:
    """Same as `stub_id`, per Sub-Skill build and attempt (the real stages' idempotency key)."""
    key = f"{inp.sub_skill_id}:{inp.build_no}:{inp.cycle}:{inp.attempt}:{step}"
    return uuid5(inp.run_id, key)
