"""What the sandbox needs from the stored build, in the shapes the runner reads (E13.2, ADR 0023).

The harness configuration is derived from the same render params the bundle was generated from
(`params_from_manifest`), never from the bundle under test: the tool names, the fresh-state names, the
key of the fresh-state queries and the handoff fields are what the Sub-Skill was *asked* to be, so a
bundle that drifted from them fails its tests instead of redefining them.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sbam_contracts.sandbox import HarnessConfig


def harness_config(params: Mapping[str, Any], ui_updates: Sequence[str]) -> HarnessConfig:
    inputs = params["inputs"]
    required = [f["name"] for f in inputs if f["required"]]
    if not required:  # render_skeleton refuses such a Sub-Skill, so a bundle never exists for it
        raise ValueError("the Sub-Skill declares no required input to key fresh state on")
    return HarnessConfig(
        action_tool=params["action_tool"],
        fixed_check_tool=params["fixed_check_tool"],
        fresh_state_parameters=list(params["fresh_state"]),
        required_inputs=required,
        key_field=required[0],
        handoff_fields=list(params["required_fields"]),
        ui_updates=list(ui_updates),
    )
