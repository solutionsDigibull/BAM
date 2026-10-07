"""E0.3 unit tests: RFC 8785 canonical JSON and SHA-256 hashing (TD §11.5)."""

import json
import re
import struct
from typing import Any

import pytest
from sbam_contracts.canonical import (
    canonical_json,
    deterministic_hash,
    sha256_of,
    workflow_content_hash,
)

# --- T05: official RFC 8785 vectors ---------------------------------------------------------------


def test_e0_3_t05_rfc8785_section_3_2_2_example() -> None:
    source = (
        '{"numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],'
        ' "string": "\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/",'
        ' "literals": [null, true, false]}'
    )
    expected = (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )
    assert canonical_json(json.loads(source)) == expected.encode("utf-8")


def test_e0_3_t05_rfc8785_property_sorting_uses_utf16_code_units() -> None:
    source = (
        '{"\\u20ac": "Euro Sign", "\\r": "Carriage Return",'
        ' "\\ufb33": "Hebrew Letter Dalet With Dagesh",'
        ' "1": "One", "\\ud83d\\ude00": "Emoji: Grinning Face", "\\u0080": "Control",'
        ' "\\u00f6": "Latin Small Letter O With Diaeresis"}'
    )
    keys = list(json.loads(canonical_json(json.loads(source))))
    assert keys == ["\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]


@pytest.mark.parametrize(
    ("ieee754", "expected"),
    [
        ("0000000000000000", "0"),
        ("8000000000000000", "0"),
        ("0000000000000001", "5e-324"),
        ("8000000000000001", "-5e-324"),
        ("7fefffffffffffff", "1.7976931348623157e+308"),
        ("ffefffffffffffff", "-1.7976931348623157e+308"),
        ("4340000000000000", "9007199254740992"),
        ("c340000000000000", "-9007199254740992"),
        ("4430000000000000", "295147905179352830000"),
        ("44b52d02c7e14af5", "9.999999999999997e+22"),
        ("44b52d02c7e14af6", "1e+23"),
        ("3eb0c6f7a0b5ed8d", "0.000001"),
        ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ],
)
def test_e0_3_t05_rfc8785_appendix_b_numbers(ieee754: str, expected: str) -> None:
    (number,) = struct.unpack(">d", bytes.fromhex(ieee754))
    assert canonical_json(number) == expected.encode("ascii")


# --- Edge cases -----------------------------------------------------------------------------------


def test_e0_3_numbers_canonicalise_per_rfc8785() -> None:
    assert canonical_json([1.0, 1, 1e21, 4.50, -0.0]) == b"[1,1,1e+21,4.5,0]"
    assert sha256_of({"x": 1.0}) == sha256_of({"x": 1})


def test_e0_3_unicode_escapes_hash_the_same() -> None:
    escaped = json.loads('{"caf\\u00e9": "\\u00e9"}')
    literal = json.loads('{"café": "é"}')
    assert canonical_json(escaped) == canonical_json(literal) == '{"café":"é"}'.encode()
    assert sha256_of(escaped) == sha256_of(literal)


# --- T08: values JCS cannot represent never get a hash --------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"parameters": [{"default": float("nan")}]},
        {"x": float("inf")},
        {"x": float("-inf")},
        {"x": "\ud800"},  # lone surrogate: not UTF-8
        {1: "non-string key"},
        {"x": 2**60},  # outside the I-JSON safe integer range
    ],
)
def test_e0_3_t08_unrepresentable_values_raise_value_error(bad: object) -> None:
    with pytest.raises(ValueError):
        canonical_json(bad)
    with pytest.raises(ValueError):
        sha256_of(bad)


# --- Hash shape and exclusions --------------------------------------------------------------------


def test_e0_3_hash_format() -> None:
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", sha256_of({}))
    assert (
        sha256_of({}) == "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )


def test_e0_3_skill_hash_ignores_only_the_excluded_paths() -> None:
    base: dict[str, Any] = {
        "governance": {"deterministic_hash": "a", "versioning": {"major": 1}},
        "sbam_build_record": {"evidence": {"t": 1}, "certification": {"c": 1}, "ids": {"i": 1}},
    }
    changed = json.loads(json.dumps(base))
    changed["governance"]["deterministic_hash"] = "b"
    changed["sbam_build_record"]["evidence"] = {"t": 2}
    del changed["sbam_build_record"]["certification"]
    assert deterministic_hash(changed) == deterministic_hash(base)
    changed["sbam_build_record"]["ids"]["i"] = 2
    assert deterministic_hash(changed) != deterministic_hash(base)
    assert base["governance"]["deterministic_hash"] == "a"  # input is never mutated


def test_e0_3_workflow_hash_ignores_integrity_and_certification() -> None:
    base = {
        "integrity": {"content_sha256": "x"},
        "certification": {"frozen_at": "t1"},
        "skills": [1],
    }
    other = {
        "integrity": {"content_sha256": "y"},
        "certification": {"frozen_at": "t2"},
        "skills": [1],
    }
    assert workflow_content_hash(base) == workflow_content_hash(other)
    assert workflow_content_hash({**base, "skills": [2]}) != workflow_content_hash(base)
