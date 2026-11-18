"""What a saved value goes through before it is encrypted (ADR 0030, decision 6).

Leading and trailing spaces, tabs and line breaks are removed; then one pair of matching wrapping
quotes (`"` or `'`); then the ends are trimmed once more. Line breaks inside the value are kept (a
PEM key needs them). A NUL byte, a value over 8 KiB, and (for a required name) an empty value are
refused, with a fixed code and no echo of the value. The result is a `SecretStr` and the names of
the steps that ran, so a log line or a response made from it cannot show the value.
"""

from dataclasses import dataclass
from typing import Final

from pydantic import SecretStr
from sbam_contracts.connections import MAX_VALUE_BYTES, CleanStep, ValueRejection

_QUOTES: Final = frozenset({'"', "'"})


class ValueRejected(Exception):  # noqa: N818 - a fixed name in the ADR
    """The value cannot be stored. `code` says why; the message never holds the value."""

    def __init__(self, code: ValueRejection) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True)
class Cleaned:
    value: SecretStr
    steps: tuple[CleanStep, ...]

    @property
    def empty(self) -> bool:
        return not self.value.get_secret_value()

    def __repr__(self) -> str:
        return f"Cleaned(steps={self.steps!r})"


def clean_value(raw: SecretStr | str, *, required: bool) -> Cleaned:
    """The value as it will be stored. An empty result is refused for a required name and returned
    for an optional one (the caller treats it as clearing the value)."""
    text = raw.get_secret_value() if isinstance(raw, SecretStr) else raw
    if "\x00" in text:
        raise ValueRejected(ValueRejection.NUL_BYTE)
    steps: list[CleanStep] = []
    trimmed = text.strip()
    if trimmed != text:
        steps.append(CleanStep.TRIMMED)
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in _QUOTES:
        trimmed = trimmed[1:-1]
        steps.append(CleanStep.UNQUOTED)
        again = trimmed.strip()
        if again != trimmed and CleanStep.TRIMMED not in steps:
            steps.append(CleanStep.TRIMMED)
        trimmed = again
    if len(trimmed.encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueRejected(ValueRejection.TOO_LARGE)
    if required and not trimmed:
        raise ValueRejected(ValueRejection.EMPTY_VALUE)
    return Cleaned(SecretStr(trimmed), tuple(steps))
