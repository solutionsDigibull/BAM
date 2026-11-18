"""The masked tail of a stored value (ADR 0030, decision 4): the last 4 characters of a value of
16 or more, the last 2 of a value of 8 or more, and nothing for a shorter one. A short secret is not
shown at all."""

from typing import Final

LONG: Final = 16
MEDIUM: Final = 8


def masked_tail(value: str) -> str:
    if len(value) >= LONG:
        return value[-4:]
    if len(value) >= MEDIUM:
        return value[-2:]
    return ""
