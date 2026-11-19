"""E14.4 (#132): MAJOR.0.PATCH arithmetic (T07: 1.0.0, 1.0.1, 1.0.2, then 2.0.0 for a new SOW)."""

import pytest
from sbam_api.versioning import numbers


def test_e14_4_t07_version_arithmetic() -> None:
    frozen: list[str] = []
    first = numbers.decide(1, frozen)
    assert str(first) == "1.0.0"
    frozen.append(str(first))
    second = numbers.decide(1, frozen)
    frozen.append(str(second))
    third = numbers.decide(1, frozen)
    assert (str(second), str(third)) == ("1.0.1", "1.0.2")
    assert str(numbers.decide(2, frozen)) == "2.0.0"  # a new approved SOW version: MAJOR + 1


def test_e14_4_the_highest_patch_of_the_major_counts_not_the_order_given() -> None:
    assert str(numbers.decide(1, ["1.0.2", "1.0.0", "1.0.10", "1.0.1"])) == "1.0.11"


@pytest.mark.parametrize("bad", ["1.1.0", "1.0", "1.0.0.0", "v1.0.0", "1.0.x", "", "01.0.-1"])
def test_e14_4_minor_is_always_zero_and_the_shape_is_strict(bad: str) -> None:
    with pytest.raises(ValueError):
        numbers.parse(bad)


def test_e14_4_major_starts_at_one() -> None:
    with pytest.raises(ValueError):
        numbers.first(0)
    assert numbers.parse("3.0.7") == numbers.Version(3, 7)
    assert str(numbers.next_patch(numbers.Version(3, 7))) == "3.0.8"
