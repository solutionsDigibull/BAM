"""The connections a Workflow expects: the live union of its Sub-Skills' V-block secrets.

ADR 0030, decision 5. The V block lists secret *names* only, so every name found is required and its
purpose is the Sub-Skill (or Sub-Skills) that names it. Nothing is cached: the caller passes the
Workflow's current Sub-Skills each time, so an edit of the ISATVON shows at once. The generated code
cannot read the environment and the repository fetcher does not exist yet, so neither is scanned;
when repositories are, the names they carry join as optional (`source = REPO`).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from sbam_contracts.connections import MAX_TEXT_CHARS
from sbam_contracts.isatvon14 import SECRET_VALUE

from sbam_api.connections.names import is_valid_name

NAMED_SKILLS: Final = 3  # how many Sub-Skills a purpose lists before "and N more"
SHOWN_CHARS: Final = 64


@dataclass(frozen=True)
class ExpectedConnection:
    name: str
    purpose: str
    required: bool = True
    skills: tuple[str, ...] = ()  # the skill ids that name it


@dataclass(frozen=True)
class ExpectedScan:
    names: tuple[ExpectedConnection, ...]  # sorted by name
    invalid: tuple[str, ...]  # named in an ISATVON but not usable as a variable name


def _purpose(labels: list[str]) -> str:
    shown = ", ".join(labels[:NAMED_SKILLS])
    more = len(labels) - NAMED_SKILLS
    text = f"Used by {shown}" + (f" and {more} more" if more > 0 else "")
    return text[:MAX_TEXT_CHARS]


def _safe(name: str) -> str:
    """A name for a message: clipped, and never something that looks like a pasted value."""
    return "[REDACTED]" if SECRET_VALUE.search(name) else name[:SHOWN_CHARS]


def _expected(name: str, labels: Mapping[str, str]) -> ExpectedConnection:
    named = [labels[k] for k in sorted(labels) if labels[k]]
    return ExpectedConnection(
        name,
        _purpose(named) if named else "",
        True,
        tuple(sorted(k for k in labels if k)),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def expected_connections(sub_skills: Iterable[Mapping[str, Any]]) -> ExpectedScan:
    """`sub_skills`: each Sub-Skill's structured ISATVON (`skill_metadata`, `isatvon_definition`).
    A draft that lacks those parts names nothing."""
    by_name: dict[str, dict[str, str]] = {}  # name -> skill id -> label
    invalid: set[str] = set()
    for raw in sub_skills:
        meta = _mapping(_mapping(raw).get("skill_metadata"))
        definition = _mapping(_mapping(raw).get("isatvon_definition"))
        names = _mapping(definition.get("v_variables")).get("secrets")
        if not isinstance(names, list):
            continue
        skill_id = str(meta.get("skill_id") or "")
        label = " ".join(p for p in (skill_id, str(meta.get("skill_name") or "")) if p)
        for name in names:
            if not isinstance(name, str):
                continue
            if not is_valid_name(name):
                invalid.add(_safe(name))
            else:
                by_name.setdefault(name, {})[skill_id] = label
    found = tuple(_expected(name, labels) for name, labels in sorted(by_name.items()))
    return ExpectedScan(found, tuple(sorted(invalid)))
