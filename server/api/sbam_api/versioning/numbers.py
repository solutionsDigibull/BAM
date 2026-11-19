"""Version arithmetic (TD §11.4, C-32): `MAJOR.0.PATCH`, minor always 0.

The first freeze of a Workflow is `MAJOR.0.0`, where MAJOR is the Workflow's own (each newly
approved SOW version is the next MAJOR: 1.0.0, then 2.0.0). A rebuild under the same SOW is
PATCH + 1.
"""

import re
from typing import Final, NamedTuple

from sbam_contracts.manifest import VERSION_PATTERN

_PATTERN: Final = re.compile(VERSION_PATTERN)


class Version(NamedTuple):
    major: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.0.{self.patch}"


def parse(text: str) -> Version:
    """`ValueError` unless the text is MAJOR.0.PATCH (minor must be 0)."""
    if not _PATTERN.fullmatch(text):
        raise ValueError(f"{text!r} is not MAJOR.0.PATCH")
    major, _, patch = text.split(".")
    return Version(int(major), int(patch))


def first(major: int) -> Version:
    """The first freeze of the Workflow of this MAJOR (a new approved SOW version: MAJOR + 1)."""
    if major < 1:
        raise ValueError("MAJOR starts at 1")
    return Version(major, 0)


def next_patch(previous: Version) -> Version:
    return Version(previous.major, previous.patch + 1)


def decide(major: int, frozen: list[str]) -> Version:
    """The version the Workflow of `major` is frozen as, given the versions it already froze."""
    same = [v for v in map(parse, frozen) if v.major == major]
    return next_patch(max(same)) if same else first(major)
