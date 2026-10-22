"""MS01.1 inputs for the build stages (E11.x): manifest params, recorded bodies, loop spec."""

import json
from pathlib import Path
from typing import Any

from fixtures import fixture_path
from sbam_contracts.isatvon14 import SubSkill
from sbam_contracts.manifest import SkillManifest
from sbam_workers.build.codegen import params_from_manifest
from sbam_workers.isatvon.loop_spec import compile_definition

TESTS = Path(__file__).resolve().parent
MS01_BODIES = TESTS / "llm-mock" / "sbam-codegen" / "e11_2_ms01_1.json"


def ms01_params() -> dict[str, Any]:
    data = json.loads(fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8"))
    return params_from_manifest(SkillManifest.model_validate(data))


def ms01_loop_spec() -> bytes:
    raw = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    return compile_definition(SubSkill.model_validate(raw).isatvon_definition).canonical_bytes


def ms01_bodies(**over: Any) -> dict[str, Any]:
    """The recorded mock answer for MS01.1 (E11.2), as `code_attempts.bodies` holds it."""
    response = json.loads(MS01_BODIES.read_text(encoding="utf-8"))["response"]
    assert isinstance(response, dict)
    return {**response, **over}


class Counter:
    """Stands in for an OpenTelemetry counter."""

    def __init__(self) -> None:
        self.value = 0

    def add(self, amount: int) -> None:
        self.value += amount
