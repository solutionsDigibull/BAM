"""Name rules for a connection, and a hint for a near miss (ADR 0030, decision 6)."""

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

from sbam_contracts.connections import NAME_PATTERN

_NAME: Final = re.compile(NAME_PATTERN)
MAX_DISTANCE: Final = 2  # edits away from an expected name that still earns a hint


def is_valid_name(name: str) -> bool:
    """An environment variable name: upper-case letters, digits and `_`, no leading digit."""
    return _NAME.fullmatch(name) is not None


def _distance(a: str, b: str) -> int:
    """Levenshtein distance (insert, delete, replace), with an early exit past MAX_DISTANCE."""
    if abs(len(a) - len(b)) > MAX_DISTANCE:
        return MAX_DISTANCE + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _shape(name: str) -> str:
    """The name as it would be written as a variable: upper case, other characters as `_`."""
    return re.sub(r"[^A-Z0-9_]", "_", name.upper())


@dataclass(frozen=True)
class NameCheck:
    valid: bool  # can be an environment variable name at all
    expected: bool  # is one of the expected names
    did_you_mean: str | None  # the expected name it is probably meant to be

    @property
    def unexpected(self) -> bool:
        return not self.expected


def check_name(name: str, expected: Collection[str]) -> NameCheck:
    """Whether `name` is usable and expected, and else which expected name it is closest to: the
    same name in another case or with other separators, or within two edits."""
    valid = is_valid_name(name)
    if name in expected:
        return NameCheck(valid, True, None)
    shaped = _shape(name)
    best: tuple[int, str] | None = None
    for candidate in sorted(expected):
        distance = 0 if shaped == candidate else _distance(shaped, candidate)
        if distance <= MAX_DISTANCE and (best is None or distance < best[0]):
            best = (distance, candidate)
    return NameCheck(valid, False, None if best is None else best[1])
