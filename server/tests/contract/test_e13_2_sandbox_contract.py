"""E13.2 contract: modes, digests, egress allowlist, run record, runner files (ADR 0023).

The egress parser is the part an attacker (a manifest, an LLM, a document) can feed, so it gets a
hostile corpus and a property test against the parsers a real resolver uses.
"""

import ipaddress
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sbam_contracts.sandbox import (
    ALIAS_MAX_LENGTH,
    ALIAS_PREFIX,
    EgressPolicy,
    FailureCategory,
    InfraReason,
    RunnerOutputs,
    RunnerRequest,
    SandboxContractError,
    SandboxLimits,
    SandboxMode,
    SandboxRun,
    SandboxRunStatus,
    normalize_digest,
    parse_egress_target,
    template_alias,
)

HEX = "a" * 64
NOW = datetime(2026, 10, 29, tzinfo=UTC)


# --- mode -------------------------------------------------------------------------------------


def test_e13_2_t02_only_cubesandbox_certifies() -> None:
    assert SandboxMode.CUBESANDBOX.certifying is True
    assert SandboxMode.LOCAL_DOCKER_NONCERT.certifying is False
    assert {m for m in SandboxMode if m.certifying} == {SandboxMode.CUBESANDBOX}


def test_e13_2_failure_categories_are_the_issue_list_and_only_one_is_infrastructure() -> None:
    assert {c.value for c in FailureCategory} == {
        "IMPORT_ERROR",
        "RUNTIME_ERROR",
        "TIMEOUT",
        "OOM",
        "OUTPUT_SCHEMA_MISMATCH",
        "ASSERTION_FAILED",
        "EGRESS_BLOCKED",
        "INFRA_ERROR",
    }
    assert [c for c in FailureCategory if c.infrastructure] == [FailureCategory.INFRA_ERROR]


# --- digest and alias -------------------------------------------------------------------------


@pytest.mark.parametrize("value", [HEX, f"sha256:{HEX}"])
def test_e13_2_both_digest_spellings_normalise_to_one(value: str) -> None:
    assert normalize_digest(value) == f"sha256:{HEX}"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "latest",
        "sha256:" + HEX[:-1],  # short
        "sha256:" + HEX + "0",  # long
        "SHA256:" + HEX,  # wrong case of the prefix
        "sha256:" + "A" * 64,  # uppercase hex
        "sha512:" + HEX,
        " sha256:" + HEX,
        "sha256:" + HEX + "\n",  # `$` would accept a trailing newline in a careless pattern
        "sha256:" + "g" * 64,
        "sha256:sha256:" + HEX,
    ],
)
def test_e13_2_malformed_digests_are_refused(value: str) -> None:
    with pytest.raises(SandboxContractError):
        normalize_digest(value)


def test_e13_2_alias_is_content_derived_and_fits_cubesandbox_alias_rules() -> None:
    alias = template_alias(f"sha256:{HEX}")
    assert alias == ALIAS_PREFIX + HEX[:56]
    assert len(alias) == ALIAS_MAX_LENGTH == 64
    assert alias == alias.lower() and alias[0].isalnum()
    assert not alias.startswith(("tpl-", "snap-"))  # reserved by CubeSandbox
    assert set(alias) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")
    assert template_alias(HEX) == alias  # either spelling, one alias
    assert template_alias("sha256:" + "b" * 64) != alias  # a new digest gets a new alias


def test_e13_2_alias_keys_on_the_first_56_hex_and_any_change_there_changes_it() -> None:
    base = "1" * 64
    for i in (0, 27, 55):
        changed = base[:i] + "2" + base[i + 1 :]
        assert template_alias(changed) != template_alias(base)
    # past 56 the alias is the same by design; the in-VM check compares the whole digest
    assert template_alias(base[:56] + "a" * 8) == template_alias(base[:56] + "b" * 8)


# --- egress allowlist: accepted ---------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        "api.example.com:443",
        "a.b.c.d.example.org:8080",
        "xn--bcher-kva.example:443",  # punycode, the only IDN spelling we accept
        "x-y.example.co.uk:1",
        "test.allowed.example:65535",
        "example.com:80",
    ],
)
def test_e13_2_real_names_are_accepted(entry: str) -> None:
    assert str(parse_egress_target(entry)) == entry


# --- egress allowlist: the hostile corpus -----------------------------------------------------

IP_LITERALS = [
    "127.0.0.1:80",
    "10.0.0.5:443",
    "172.16.0.1:80",
    "192.168.1.1:80",
    "169.254.169.254:80",  # cloud metadata
    "100.64.0.1:443",  # CGNAT: Netbird's range, a hole here reaches the sandbox host
    "0.0.0.0:80",
    "1.1.1.1:443",  # a public literal is refused too: names only
    "2130706433:80",  # 127.0.0.1 as one decimal number
    "0x7f000001:80",  # hex
    "0x7f.0x0.0x0.0x1:80",
    "0177.0.0.1:80",  # octal
    "127.1:80",  # short form
    "127.0.1:80",
    "[::1]:80",
    "::1:80",
    "[fe80::1]:80",
    "[::ffff:10.0.0.1]:80",
]


@pytest.mark.parametrize("entry", IP_LITERALS)
def test_e13_2_no_ip_literal_in_any_spelling(entry: str) -> None:
    with pytest.raises(SandboxContractError):
        parse_egress_target(entry)


@pytest.mark.parametrize(
    "entry",
    [
        "*.example.com:443",  # wildcards need a deny-all fallback and widen the policy
        "api.*.example.com:443",
        "https://example.com:443",
        "example.com/path:443",
        "user@example.com:443",
        "example.com:443:8080",
        "example.com",  # no port
        "example.com:",
        ":443",
        "",
        "example.com:0",
        "example.com:65536",
        "example.com:-1",
        "example.com:+80",
        "example.com:0080",  # leading zeros
        "example.com: 80",
        "example.com:80 ",
        " example.com:80",
        "example.com:80\n",
        "example.com\n:80",
        "example.com:80\x00",
        "exam\x00ple.com:80",
        "example.com.:80",  # trailing dot
        ".example.com:80",
        "a..example.com:80",
        "-a.example.com:80",
        "a-.example.com:80",
        "a_b.example.com:80",  # underscore
        "ex ample.com:80",
        "Example.com:80",  # uppercase: refuse, do not silently fold
        "EXAMPLE.COM:80",
        "\u0430pi.example.com:80",  # Cyrillic a (homograph)
        "example.com:\uff18\uff10",  # fullwidth digits
        "example\u3002com:80",  # ideographic full stop
        "example.com:\u0668\u0660",  # arabic-indic digits
        "a" * 64 + ".example.com:80",  # label over 63
        ".".join(["a" * 63] * 4) + ".com:80",  # host over 253
        "example.123:80",  # all-numeric TLD
        "example.1e3:80",
    ],
)
def test_e13_2_malformed_entries_are_refused(entry: str) -> None:
    with pytest.raises(SandboxContractError):
        parse_egress_target(entry)


@pytest.mark.parametrize(
    "entry",
    [
        "localhost:80",
        "metadata:80",
        "sbam-mock-api:8080",  # a platform name: the manifest cannot name it
        "postgres:5432",
        "temporal:7233",
        "x.local:80",
        "x.localhost:80",
        "x.localdomain:80",
        "metadata.google.internal:80",
        "svc.cluster.local:443",
        "printer.lan:631",
        "nas.home:80",
        "intranet.corp:443",
        "x.intranet:80",
        "x.private:80",
        "1.0.0.127.in-addr.arpa:80",
        "x.home.arpa:80",
    ],
)
def test_e13_2_internal_names_cannot_be_allowlisted_by_a_manifest(entry: str) -> None:
    with pytest.raises(SandboxContractError):
        parse_egress_target(entry)


def test_e13_2_a_platform_entry_may_be_one_label_but_nothing_else_loosens() -> None:
    assert str(parse_egress_target("sbam-mock-api:8080", platform=True)) == "sbam-mock-api:8080"
    for bad in ("*.x:80", "127.0.0.1:80", "UPPER:80", "x.local:80", "a_b:80", "sbam-mock-api"):
        with pytest.raises(SandboxContractError):
            parse_egress_target(bad, platform=True)


@settings(max_examples=600, deadline=None)
@given(st.text(max_size=80))
def test_e13_2_property_nothing_a_resolver_reads_as_an_address_is_ever_accepted(raw: str) -> None:
    """Whatever the parser accepts must not be an address under inet_aton (decimal, hex, octal and
    short forms), `ipaddress` or `getaddrinfo`'s numeric parsing, and must round-trip unchanged.
    """
    try:
        target = parse_egress_target(raw)
    except SandboxContractError:
        return
    assert str(target) == raw
    assert raw == raw.strip() and raw.isascii() and raw == raw.lower()
    host = target.host
    with pytest.raises(OSError):
        socket.inet_aton(host)
    with pytest.raises(ValueError):
        ipaddress.ip_address(host)
    assert "." in host and not host.startswith(("-", ".")) and not host.endswith((".", "-"))
    assert not host.rsplit(".", 1)[1].isdigit()


@settings(max_examples=400, deadline=None)
@given(st.integers(min_value=0, max_value=2**32 - 1), st.sampled_from(["", "0x", "0"]))
def test_e13_2_property_every_ipv4_in_decimal_hex_or_octal_form_is_refused(
    n: int, base: str
) -> None:
    text = {"": str(n), "0x": hex(n), "0": "0" + oct(n)[2:]}[base]
    with pytest.raises(SandboxContractError):
        parse_egress_target(f"{text}:80")


# --- policy -----------------------------------------------------------------------------------


def test_e13_2_policy_holds_at_most_fifty_unique_entries() -> None:
    ok = [f"h{i}.example.com:443" for i in range(50)]
    assert len(EgressPolicy(allow=ok).targets()) == 50
    with pytest.raises(ValidationError):
        EgressPolicy(allow=[*ok, "one-more.example.com:443"])
    with pytest.raises(ValidationError):
        EgressPolicy(allow=["a.example.com:443", "a.example.com:443"])


def test_e13_2_policy_names_the_bad_entry() -> None:
    with pytest.raises(ValidationError, match=r"169\.254\.169\.254"):
        EgressPolicy(allow=["ok.example.com:443", "169.254.169.254:80"])


def test_e13_2_policy_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EgressPolicy.model_validate({"allow": [], "allow_internet_access": True})


# --- limits -----------------------------------------------------------------------------------


def test_e13_2_limits_defaults_match_the_design() -> None:
    limits = SandboxLimits()
    assert (limits.cpu, limits.memory_mb, limits.case_timeout_s) == (1, 1024, 60)


@pytest.mark.parametrize(
    "field,value",
    [
        ("cpu", 0),
        ("cpu", 3),
        ("memory_mb", 511),
        ("memory_mb", 2049),
        ("case_timeout_s", 9),
        ("case_timeout_s", 301),
        ("max_output_bytes", 1023),
        ("max_output_bytes", 10 * 2**20 + 1),
    ],
)
def test_e13_2_limits_outside_policy_are_rejected_never_clamped(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        SandboxLimits.model_validate({field: value})


# --- run record -------------------------------------------------------------------------------


def run(**fields: Any) -> SandboxRun:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "code_attempt_id": uuid.uuid4(),
        "dataset_id": uuid.uuid4(),
        "run_no": 1,
        "mode": SandboxMode.CUBESANDBOX,
        "status": SandboxRunStatus.FINISHED,
        "runtime_digest": f"sha256:{HEX}",
        "started_at": NOW,
        "finished_at": NOW,
    }
    return SandboxRun.model_validate({**base, **fields})


def test_e13_2_t01_a_clean_cubesandbox_run_may_certify() -> None:
    assert run().certifying is True


def test_e13_2_t02_a_local_docker_run_never_certifies_even_when_clean() -> None:
    assert run(mode=SandboxMode.LOCAL_DOCKER_NONCERT).certifying is False


@pytest.mark.parametrize("category", [c for c in FailureCategory if not c.infrastructure])
def test_e13_2_a_failed_run_does_not_certify(category: FailureCategory) -> None:
    assert run(failure_category=category).certifying is False


def test_e13_2_an_unfinished_run_does_not_certify() -> None:
    assert run(status=SandboxRunStatus.STARTED, finished_at=None).certifying is False


def test_e13_2_finished_at_is_set_exactly_when_finished() -> None:
    with pytest.raises(ValidationError):
        run(finished_at=None)
    with pytest.raises(ValidationError):
        run(status=SandboxRunStatus.STARTED)


def test_e13_2_infra_reason_goes_with_infra_error_and_nothing_else() -> None:
    infra = run(
        failure_category=FailureCategory.INFRA_ERROR, infra_reason=InfraReason.HASH_MISMATCH
    )
    assert infra.certifying is False
    with pytest.raises(ValidationError):
        run(failure_category=FailureCategory.INFRA_ERROR)
    with pytest.raises(ValidationError):
        run(failure_category=FailureCategory.TIMEOUT, infra_reason=InfraReason.HASH_MISMATCH)
    with pytest.raises(ValidationError):
        run(infra_reason=InfraReason.HASH_MISMATCH)


def test_e13_2_a_run_cannot_carry_a_forged_certifying_flag() -> None:
    with pytest.raises(ValidationError):
        run(mode=SandboxMode.LOCAL_DOCKER_NONCERT, certifying=True)


def test_e13_2_run_digest_must_be_the_prefixed_form() -> None:
    with pytest.raises(ValidationError):
        run(runtime_digest=HEX)


# --- runner files -----------------------------------------------------------------------------


def request(**fields: Any) -> RunnerRequest:
    base: dict[str, Any] = {
        "limits": SandboxLimits(),
        "mode": SandboxMode.CUBESANDBOX,
        "tool_module": "skill.server",
        "loop_spec": {"semantics": "TD §15.2"},
        "handoff_table": "handoff_rows",
        "cases": [{"case_id": "c1"}],
    }
    return RunnerRequest.model_validate({**base, **fields})


def test_e13_2_runner_request_round_trips() -> None:
    r = request()
    assert RunnerRequest.model_validate_json(r.model_dump_json()) == r


@pytest.mark.parametrize(
    "module", ["", "a b", "a;b", "a/b", "../x", "os.system('x')", "a\nb", "1x"]
)
def test_e13_2_tool_module_is_a_dotted_name_only(module: str) -> None:
    with pytest.raises(ValidationError):
        request(tool_module=module)


@pytest.mark.parametrize("table", ["", "A", "a-b", "a b", "a;drop", "1a", "a" * 64, 'a"b'])
def test_e13_2_handoff_table_is_a_plain_identifier(table: str) -> None:
    with pytest.raises(ValidationError):
        request(handoff_table=table)


def test_e13_2_runner_request_needs_at_least_one_case() -> None:
    with pytest.raises(ValidationError):
        request(cases=[])


def test_e13_2_runner_probe_entries_are_parsed_like_egress_entries() -> None:
    assert request(egress_probe=["sbam-mock-api:8080"]).egress_probe == ["sbam-mock-api:8080"]
    with pytest.raises(ValidationError):
        request(egress_probe=["127.0.0.1:80"])


def outputs(**fields: Any) -> RunnerOutputs:
    base: dict[str, Any] = {
        "runtime_digest": f"sha256:{HEX}",
        "mode": SandboxMode.CUBESANDBOX,
        "resources": {"wall_ms": 1, "cpu_ms": 1, "peak_rss_kb": 1},
        "cases": [
            {
                "case_id": "c1",
                "expected_terminal": "SUCCESS",
                "terminal": "SUCCESS",
                "passed": True,
                "trace": {},
            }
        ],
    }
    return RunnerOutputs.model_validate({**base, **fields})


def test_e13_2_runner_outputs_reject_duplicate_case_ids() -> None:
    case = outputs().cases[0].model_dump()
    with pytest.raises(ValidationError):
        outputs(cases=[case, case])


def test_e13_2_runner_outputs_reject_unknown_fields_and_other_versions() -> None:
    with pytest.raises(ValidationError):
        outputs(unexpected=1)
    with pytest.raises(ValidationError):
        outputs(schema_version=2)


@settings(max_examples=4000, deadline=None)
@given(st.from_regex(r"[0-9a-fxA-FX.\[\]:-]{1,24}:[0-9]{1,5}", fullmatch=True))
def test_e13_2_property_address_shaped_strings_never_pass(raw: str) -> None:
    """Strings made only of what IPv4 and IPv6 spellings use: none may be accepted as an address."""
    try:
        target = parse_egress_target(raw)
    except SandboxContractError:
        return
    with pytest.raises(OSError):
        socket.inet_aton(target.host)
    with pytest.raises(ValueError):
        ipaddress.ip_address(target.host)
    # `abc.def` is a legal name made only of hex digits; it must also not resolve numerically
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo(target.host, None, flags=socket.AI_NUMERICHOST)
