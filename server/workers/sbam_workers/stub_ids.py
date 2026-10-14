"""Deterministic ids for stub activities (E10.1): the same run + step always gives the same id, so
an activity retry or a resumed pipeline never invents a second Workflow / Sub-Skill. Real
activities upsert by their natural keys instead (TD §13.4)."""

from uuid import UUID, uuid5

from sbam_contracts.runs import RunInput


def stub_id(inp: RunInput, step: str) -> UUID:
    return uuid5(inp.run_id, step)
