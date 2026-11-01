"""Stands in for `sbam_contracts/_jsonschema.py` inside the VM.

The real module reads its release from the installed `sbam-contracts` distribution, which the sandbox
image deliberately does not contain (platform code is copied, never installed: ADR 0023, decision 7).
The runner only validates; it never generates a schema, so the one function other modules import fails
loudly if anything ever tries.
"""

from typing import Any

from pydantic import BaseModel


def contract_schema(model: type[BaseModel], path: str) -> dict[str, Any]:
    raise RuntimeError("schemas are generated on the platform, not in the sandbox")
