"""Skill Manifest -> template context for Spike E0.11 (#29), TD §12.3.

The manifest is validated with the real contract (`sbam_contracts.manifest.SkillManifest`, #20)
before anything is rendered. Values come from the structured ISATVON detail in
`sbam_build_record.isatvon_detail` (it has V parameters and fresh-state names as identifiers);
the flat ISA14 block only has those as prose.

gate_outputs = names used by success_gate / escalation_condition (parsed with the SBAM grammar,
`sbam_contracts.gate_expr`, #19) that are not inputs, V bounds or fresh state. Those are the
values only the fixed check tool can produce, so it must return every one of them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sbam_contracts.gate_expr import identifiers, parse
from sbam_contracts.manifest import SkillManifest

PYTYPES = ("string", "integer", "number", "boolean")


class ManifestError(ValueError):
    """The manifest cannot be turned into code with this template."""


def load_manifest(path: Path) -> SkillManifest:
    return SkillManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    required = set(schema.get("required") or [])
    out = []
    for name, spec in schema["properties"].items():
        if spec.get("type") not in PYTYPES:
            raise ManifestError(f"{name}: type {spec.get('type')!r} is not one of {PYTYPES}")
        out.append({"name": name, "type": spec["type"], "required": name in required})
    return out


def build_context(manifest: SkillManifest, template_version: str) -> dict[str, Any]:
    data = manifest.dump()
    detail = data["sbam_build_record"]["isatvon_detail"]
    t, a, v, o = (detail[k] for k in ("t_tech_stack", "a_automation", "v_variables", "o_outcome"))
    if t["action_tool"] == t["fixed_check_tool"]:
        raise ManifestError("action_tool and fixed_check_tool must differ (ISATVON rule 4)")
    handoff = o["state_handoff_file"]
    if not isinstance(handoff, dict) or handoff.get("mode") != "append_only":
        raise ManifestError("o_outcome.state_handoff_file must be an append_only table object")

    inputs = _fields(data["contracts"]["input_schema"])
    if not inputs:
        raise ManifestError("contracts.input_schema has no properties")
    parameters = [{"name": p["name"], "value": p["value"]} for p in v.get("parameters") or []]
    fresh_state = list(detail["s_source"]["fresh_state_parameters"])
    used = identifiers(parse(v["success_gate"])) | identifiers(parse(v["escalation_condition"]))
    known = {i["name"] for i in inputs} | {"max_iterations"} | {p["name"] for p in parameters}
    return {
        "skill_id": data["skill_metadata"]["skill_id"],
        "template_version": template_version,
        "action_tool": t["action_tool"],
        "fixed_check_tool": t["fixed_check_tool"],
        "action_step": a["action_step"],
        "acceptance_check": a["acceptance_check"],
        "inputs": inputs,
        "outputs": _fields(data["contracts"]["output_schema"]),
        "parameters": parameters,
        "fresh_state": fresh_state,
        "gate_outputs": sorted(used - known - set(fresh_state)),
        "handoff_table": handoff["table"],
        "required_fields": list(handoff["required_fields"]),
        # Prompt-only context (not read by any template)
        "success_gate": v["success_gate"],
        "escalation_condition": v["escalation_condition"],
        "tables": list(v.get("tables") or []),
    }
