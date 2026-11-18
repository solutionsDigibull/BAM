"""The `.env.example` of a Workflow: names and descriptions only, never a value (ADR 0030, 9).

A pure function of what the Workflow expects. It takes no value, so it cannot print one: each entry
is a comment with the purpose and whether it is required, a comment with where to get it, and
`NAME=` with nothing after the sign. The export package calls it.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from sbam_api.connections.names import is_valid_name

HEADER: Final = (
    "# Environment variables of this workflow.\n"
    "# Names and descriptions only: values are never exported.\n"
    "# Copy this file to `.env` and fill it in.\n"
)
MAX_COMMENT: Final = 200


@dataclass(frozen=True)
class EnvEntry:
    name: str
    purpose: str = ""
    where_to_get: str = ""
    required: bool = True


def _comment(text: str) -> str:
    """One line: line breaks and tabs become spaces, runs of spaces one space, clipped."""
    return re.sub(r"\s+", " ", text).strip()[:MAX_COMMENT]


def env_example(entries: Iterable[EnvEntry]) -> str:
    lines = [HEADER.rstrip("\n")]
    for entry in sorted(entries, key=lambda e: e.name):
        if not is_valid_name(entry.name):
            raise ValueError(f"{entry.name[:64]!r} is not an environment variable name")
        kind = "required" if entry.required else "optional"
        purpose = _comment(entry.purpose)
        lines.append("")
        lines.append(f"# {purpose} ({kind})" if purpose else f"# ({kind})")
        where = _comment(entry.where_to_get)
        if where:
            lines.append(f"# Get it: {where}")
        lines.append(f"{entry.name}=")
    return "\n".join(lines) + "\n"
