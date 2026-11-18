"""#764 (ADR 0030): cleaning a value (WG16-T02), names, the expected names, `.env.example`."""

import pytest
from pydantic import SecretStr
from sbam_api.connections.clean import ValueRejected, clean_value
from sbam_api.connections.env_example import EnvEntry, env_example
from sbam_api.connections.expected import expected_connections
from sbam_api.connections.names import check_name, is_valid_name
from sbam_contracts.connections import MAX_VALUE_BYTES, CleanStep, ValueRejection

TRIMMED, UNQUOTED = CleanStep.TRIMMED, CleanStep.UNQUOTED


def cleaned(raw: str, required: bool = True) -> tuple[str, tuple[CleanStep, ...]]:
    result = clean_value(raw, required=required)
    return result.value.get_secret_value(), result.steps


# --- WG16-T02: quotes and spaces cleaned ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value", "steps"),
    [
        ("abc123", "abc123", ()),
        ("  abc123  ", "abc123", (TRIMMED,)),
        ("abc123\n", "abc123", (TRIMMED,)),
        ("\r\n\tabc123 \r\n", "abc123", (TRIMMED,)),
        ('"abc123"', "abc123", (UNQUOTED,)),
        ("'abc123'", "abc123", (UNQUOTED,)),
        ('  "abc123"\n', "abc123", (TRIMMED, UNQUOTED)),
        ('" abc123 "', "abc123", (UNQUOTED, TRIMMED)),
        ('""abc""', '"abc"', (UNQUOTED,)),  # one pair only
        ("\"abc'", "\"abc'", ()),  # not a matching pair
        ('"abc', '"abc', ()),
        ("'", "'", ()),  # a lone quote is a value
        ("a\nb", "a\nb", ()),  # a line break inside is kept
        (
            "-----BEGIN KEY-----\nAAAA\n-----END KEY-----\n",
            "-----BEGIN KEY-----\nAAAA\n-----END KEY-----",
            (TRIMMED,),
        ),
        ("it's-fine", "it's-fine", ()),
        ('"it\'s"', "it's", (UNQUOTED,)),
    ],
)
def test_wg16_t02_quotes_and_spaces_are_cleaned_and_the_steps_reported(
    raw: str, value: str, steps: tuple[CleanStep, ...]
) -> None:
    assert cleaned(raw) == (value, steps)


def test_wg16_t02_a_secretstr_is_cleaned_like_a_str() -> None:
    assert clean_value(SecretStr('  "k"  '), required=True).value.get_secret_value() == "k"


@pytest.mark.parametrize("raw", ["", "   ", "\n\n", '""', "''", '" "', "\t'  '\n"])
def test_wg16_t02_a_required_value_that_is_empty_after_cleaning_is_refused(raw: str) -> None:
    with pytest.raises(ValueRejected) as caught:
        clean_value(raw, required=True)
    assert caught.value.code is ValueRejection.EMPTY_VALUE


def test_wg16_t02_an_optional_value_that_is_empty_comes_back_empty() -> None:
    result = clean_value('  ""  ', required=False)
    assert result.empty and result.steps == (TRIMMED, UNQUOTED)


def test_wg16_t02_a_nul_byte_and_a_value_over_8_kib_are_refused() -> None:
    with pytest.raises(ValueRejected) as nul:
        clean_value("ab\x00cd", required=False)
    assert nul.value.code is ValueRejection.NUL_BYTE
    assert cleaned("x" * MAX_VALUE_BYTES)[0] == "x" * MAX_VALUE_BYTES  # exactly 8 KiB is fine
    with pytest.raises(ValueRejected) as big:
        clean_value("x" * (MAX_VALUE_BYTES + 1), required=True)
    assert big.value.code is ValueRejection.TOO_LARGE
    with pytest.raises(ValueRejected) as wide:  # the limit is bytes, not characters
        clean_value("é" * (MAX_VALUE_BYTES // 2 + 1), required=True)
    assert wide.value.code is ValueRejection.TOO_LARGE


def test_wg16_t02_a_refusal_and_a_result_never_show_the_value() -> None:
    secret = "not_a_real_TopSecret123"  # noqa: S105 - a fixture, not a credential
    with pytest.raises(ValueRejected) as caught:
        clean_value(secret + "\x00", required=True)
    assert secret not in str(caught.value) and secret not in repr(caught.value)
    result = clean_value(f' "{secret}" ', required=True)
    assert secret not in repr(result) and secret not in str(result.value)


# --- names --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["A", "LEDGER_API_KEY", "_X", "A1_B2", "K" * 128])
def test_wg16_a_valid_name_is_an_environment_variable_name(name: str) -> None:
    assert is_valid_name(name)


@pytest.mark.parametrize(
    "name", ["", "1ABC", "ledger_api_key", "A-B", "A.B", "A B", "A=B", "K" * 129, "A\n", "É"]
)
def test_wg16_an_invalid_name_is_refused(name: str) -> None:
    assert not is_valid_name(name)


EXPECTED = ["LEDGER_API_KEY", "AUTH_VAULT_KEY", "JWT_SIGNING_KEY"]


def test_wg16_an_expected_name_is_expected_and_has_no_hint() -> None:
    check = check_name("LEDGER_API_KEY", EXPECTED)
    assert check.valid and check.expected and not check.unexpected and check.did_you_mean is None


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("ledger_api_key", "LEDGER_API_KEY"),  # case
        ("Ledger-Api-Key", "LEDGER_API_KEY"),  # other separators
        ("LEDGER_API_KY", "LEDGER_API_KEY"),  # one edit
        ("LEDGR_API_KY", "LEDGER_API_KEY"),  # two edits
        ("AUTH_VAULT_KE", "AUTH_VAULT_KEY"),
    ],
)
def test_wg16_a_near_miss_gets_a_hint(name: str, hint: str) -> None:
    check = check_name(name, EXPECTED)
    assert check.unexpected and check.did_you_mean == hint


@pytest.mark.parametrize("name", ["SOMETHING_ELSE", "LEDGER", "LEDGER_API_KEY_PROD_V2", "X"])
def test_wg16_a_name_that_is_far_from_every_expected_one_gets_no_hint(name: str) -> None:
    check = check_name(name, EXPECTED)
    assert check.unexpected and check.did_you_mean is None


def test_wg16_a_valid_but_unexpected_name_is_flagged_and_an_invalid_one_says_so() -> None:
    assert check_name("EXTRA_TOKEN", EXPECTED).valid
    assert not check_name("extra-token", EXPECTED).valid


# --- the expected names -------------------------------------------------------------------------


def sub_skill(skill_id: str, name: str, secrets: object) -> dict[str, object]:
    return {
        "skill_metadata": {"skill_id": skill_id, "skill_name": name},
        "isatvon_definition": {"v_variables": {"secrets": secrets, "tables": ["t"]}},
    }


def test_wg16_the_expected_names_are_the_union_of_the_sub_skills_v_block_secrets() -> None:
    scan = expected_connections(
        [
            sub_skill("MS01.1", "Investor Auth", ["AUTH_VAULT_KEY", "JWT_SIGNING_KEY"]),
            sub_skill("MS01.2", "KYC", ["AUTH_VAULT_KEY", "KYC_API_KEY"]),
            sub_skill("MS02.1", "Nothing", None),
            sub_skill("MS02.2", "Empty", []),
        ]
    )
    assert [e.name for e in scan.names] == ["AUTH_VAULT_KEY", "JWT_SIGNING_KEY", "KYC_API_KEY"]
    assert all(e.required for e in scan.names) and scan.invalid == ()
    vault = scan.names[0]
    assert vault.skills == ("MS01.1", "MS01.2")
    assert vault.purpose == "Used by MS01.1 Investor Auth, MS01.2 KYC"
    assert scan.names[1].purpose == "Used by MS01.1 Investor Auth"


def test_wg16_a_purpose_lists_three_sub_skills_and_counts_the_rest() -> None:
    skills = [sub_skill(f"MS0{i}.1", f"S{i}", ["SHARED_KEY"]) for i in range(1, 6)]
    [shared] = expected_connections(skills).names
    assert shared.purpose == "Used by MS01.1 S1, MS02.1 S2, MS03.1 S3 and 2 more"


def test_wg16_a_name_that_cannot_be_a_variable_is_listed_apart_and_a_pasted_value_is_hidden() -> (
    None
):
    scan = expected_connections(
        [sub_skill("MS01.1", "A", ["good_name", "AUTH.KEY", "sk-fake-fixture", "FINE"])]
    )
    assert [e.name for e in scan.names] == ["FINE"]
    assert scan.invalid == ("AUTH.KEY", "[REDACTED]", "good_name")
    assert all("sk-fake" not in n for n in scan.invalid)


def test_wg16_a_partial_or_odd_isatvon_names_nothing_and_never_raises() -> None:
    odd: list[object] = [
        {},
        {"skill_metadata": "x"},
        {"isatvon_definition": {"v_variables": {"secrets": "NOT_A_LIST"}}},
        {"isatvon_definition": {"v_variables": {"secrets": [1, None, "OK_NAME"]}}},
        {"isatvon_definition": "x"},
    ]
    scan = expected_connections(odd)  # type: ignore[arg-type]
    assert [e.name for e in scan.names] == ["OK_NAME"] and scan.names[0].purpose == ""


def test_wg16_the_scan_is_live_not_cached() -> None:
    skills = [sub_skill("MS01.1", "A", ["ONE_KEY"])]
    assert [e.name for e in expected_connections(skills).names] == ["ONE_KEY"]
    skills[0] = sub_skill("MS01.1", "A", ["ONE_KEY", "TWO_KEY"])  # an ISATVON edit shows at once
    assert [e.name for e in expected_connections(skills).names] == ["ONE_KEY", "TWO_KEY"]


# --- .env.example -------------------------------------------------------------------------------


def test_wg16_env_example_holds_names_and_descriptions_and_nothing_after_the_sign() -> None:
    text = env_example(
        [
            EnvEntry("LEDGER_API_KEY", "Used by MS01.1 Ledger", "https://ledger.example/keys"),
            EnvEntry("AUTH_VAULT_KEY", "Used by MS01.1 Auth", required=False),
            EnvEntry("BARE_KEY"),
        ]
    )
    assert text == (
        "# Environment variables of this workflow.\n"
        "# Names and descriptions only: values are never exported.\n"
        "# Copy this file to `.env` and fill it in.\n"
        "\n"
        "# Used by MS01.1 Auth (optional)\n"
        "AUTH_VAULT_KEY=\n"
        "\n"
        "# (required)\n"
        "BARE_KEY=\n"
        "\n"
        "# Used by MS01.1 Ledger (required)\n"
        "# Get it: https://ledger.example/keys\n"
        "LEDGER_API_KEY=\n"
    )
    assignments = [line for line in text.splitlines() if "=" in line and not line.startswith("#")]
    assert assignments and all(line.endswith("=") for line in assignments)


def test_wg16_env_example_cannot_be_broken_out_of_a_comment_and_clips_long_text() -> None:
    nasty = "first\nINJECTED=value\r\n\tmore   text " + "x" * 400
    text = env_example([EnvEntry("A_KEY", nasty, nasty)])
    lines = text.splitlines()
    assert [ln for ln in lines if ln.startswith("INJECTED")] == []
    assert [ln for ln in lines if not ln.startswith("#") and ln] == ["A_KEY="]
    assert all(len(ln) < 260 for ln in lines)


def test_wg16_env_example_refuses_a_name_that_is_not_a_variable_name() -> None:
    with pytest.raises(ValueError, match="not an environment variable name"):
        env_example([EnvEntry("bad name=1")])


def test_wg16_env_example_of_nothing_is_just_the_header() -> None:
    assert env_example([]).count("\n") == 3 and "=" not in env_example([]).replace("`.env`", "")
