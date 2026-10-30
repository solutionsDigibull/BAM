"""E13.2 / E13.1 network plan: pinned resolution, public addresses only, a safe hosts file.

The resolver is a fake: nothing here touches DNS. The attacks are what a hostile name server, a
careless operator or a bad manifest can hand the planner.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence

import pytest
from sbam_contracts.sandbox import EgressPolicy, InfraReason
from sbam_workers.sandbox import policy as policy_module
from sbam_workers.sandbox.backend import SandboxInfraError
from sbam_workers.sandbox.policy import (
    MAX_ADDRESSES_PER_HOST,
    EgressPolicyError,
    MockApi,
    NetworkPlan,
    is_public_ipv4,
    plan_network,
    render_hosts,
    system_resolver,
)

PUBLIC = "93.184.216.34"
MOCK = MockApi(host="sbam-mock-api", ip="100.64.0.7", port=8080)  # a Netbird address


def fake(answers: dict[str, list[str]]) -> Callable[[str], Awaitable[list[str]]]:
    calls: list[str] = []

    async def resolve(host: str) -> list[str]:
        calls.append(host)
        return answers[host]

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


def plan(
    entries: list[str], answers: dict[str, list[str]], mock: MockApi | None = MOCK
) -> NetworkPlan:
    return asyncio.run(plan_network(EgressPolicy(allow=entries), mock, fake(answers)))


# --- which addresses count as public ----------------------------------------------------------

REFUSED_RANGES = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",  # CGNAT: Netbird lives here
    "127.0.0.0/8",
    "169.254.0.0/16",  # link-local, cloud metadata
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",  # documentation
    "192.168.0.0/16",
    "198.18.0.0/15",  # benchmarking
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",  # multicast
    "240.0.0.0/4",  # reserved
]


@pytest.mark.parametrize("cidr", REFUSED_RANGES)
def test_e13_1_t04_t06_no_address_in_a_special_range_is_public(cidr: str) -> None:
    net = ipaddress.ip_network(cidr)
    for address in (net[0], net[1], net[net.num_addresses // 2], net[-2], net[-1]):
        assert not is_public_ipv4(str(address)), address


@pytest.mark.parametrize(
    "address",
    ["255.255.255.255", "169.254.169.254", "0.0.0.0", "127.0.0.1"],  # noqa: S104 - refused ones
)
def test_e13_1_t04_the_named_dangerous_addresses_are_refused(address: str) -> None:
    assert not is_public_ipv4(address)


@pytest.mark.parametrize(
    "address", ["1.1.1.1", "8.8.8.8", PUBLIC, "151.101.1.69", "100.63.255.255", "100.128.0.0"]
)
def test_e13_2_ordinary_public_addresses_are_accepted(address: str) -> None:
    assert is_public_ipv4(address)  # 100.63.* and 100.128.* are just outside the CGNAT block


@pytest.mark.parametrize(
    "text",
    [
        "",
        " 1.1.1.1",
        "1.1.1.1 ",
        "1.1.1.1\n",
        "01.1.1.1",
        "1.1.1",
        "1.1.1.1.1",
        "0x01.1.1.1",
        "2130706433",
        "::1",
        "::ffff:1.1.1.1",
        "2606:4700::1111",
        "1.1.1.256",
        "a.b.c.d",
        "1.1.1.-1",
    ],
)
def test_e13_2_anything_that_is_not_a_plain_dotted_ipv4_is_refused(text: str) -> None:
    assert not is_public_ipv4(text)


# --- planning ---------------------------------------------------------------------------------


def test_e13_2_a_public_name_is_pinned_and_allowed_by_address() -> None:
    p = plan(["api.example.com:443"], {"api.example.com": [PUBLIC]})
    assert PUBLIC in p.allow_out and p.hosts["api.example.com"] == (PUBLIC,)
    assert "api.example.com:443" in p.probe


def test_e13_1_t06_a_name_that_rebinds_to_a_private_address_is_refused_not_allowed() -> None:
    with pytest.raises(EgressPolicyError, match="non-public") as caught:
        plan(["test.allowed.example:443"], {"test.allowed.example": ["10.0.0.5"]})
    assert caught.value.host == "test.allowed.example"


@pytest.mark.parametrize(
    "bad", ["10.0.0.5", "169.254.169.254", "127.0.0.1", "100.64.0.1", "192.168.1.1"]
)
def test_e13_1_t06_one_bad_answer_among_good_ones_refuses_the_whole_name(bad: str) -> None:
    with pytest.raises(EgressPolicyError):
        plan(["a.example.com:443"], {"a.example.com": [PUBLIC, bad]})


def test_e13_2_a_name_with_no_address_is_refused() -> None:
    with pytest.raises(EgressPolicyError, match="no IPv4"):
        plan(["a.example.com:443"], {"a.example.com": []})


def test_e13_2_the_planner_sorts_deduplicates_and_caps_whatever_the_resolver_returns() -> None:
    many = [f"93.184.216.{n}" for n in (40, 20, 30, 10, 50, 60, 20)]
    p = plan(["a.example.com:443"], {"a.example.com": many}, mock=None)
    assert list(p.hosts["a.example.com"]) == sorted(set(many))[:MAX_ADDRESSES_PER_HOST]
    assert len(p.allow_out) == MAX_ADDRESSES_PER_HOST


def test_e13_2_the_same_name_on_two_ports_is_resolved_once() -> None:
    resolver = fake({"a.example.com": [PUBLIC]})
    asyncio.run(
        plan_network(
            EgressPolicy(allow=["a.example.com:443", "a.example.com:8443"]), None, resolver
        )
    )
    assert resolver.calls == ["a.example.com"]  # type: ignore[attr-defined]


def test_e13_2_the_mock_api_is_pinned_added_and_never_resolved() -> None:
    resolver = fake({})
    p = asyncio.run(plan_network(EgressPolicy(), MOCK, resolver))
    assert p.allow_out == ("100.64.0.7",) and p.hosts == {"sbam-mock-api": ("100.64.0.7",)}
    assert p.probe == ("sbam-mock-api:8080",)
    assert resolver.calls == []  # type: ignore[attr-defined]


def test_e13_2_without_a_mock_api_and_an_empty_policy_the_vm_reaches_nothing() -> None:
    p = plan([], {}, mock=None)
    assert p.allow_out == () and dict(p.hosts) == {} and p.probe == ()


def test_e13_2_the_manifest_cannot_name_the_mock_apis_address_or_any_ip() -> None:
    with pytest.raises(ValueError):
        EgressPolicy(allow=["100.64.0.7:8080"])


# --- the mock API's own checks ----------------------------------------------------------------


@pytest.mark.parametrize(
    "ip", ["10.1.2.3", "100.64.0.1", "172.20.0.5", "192.168.0.9", "203.0.113.9"]
)
def test_e13_2_the_operator_may_pin_a_private_or_netbird_mock_api(ip: str) -> None:
    assert MockApi(host="sbam-mock-api", ip=ip, port=8080).ip == ip


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.255.255.254",
        "169.254.169.254",
        "169.254.1.1",
        "0.0.0.0",  # noqa: S104 - an address to refuse, not a bind
        "0.1.2.3",
        "224.0.0.1",
        "239.255.255.250",
        "::1",
        "fe80::1",
        "2606:4700::1111",
        "",
        "localhost",
        "10.0.0",
    ],
)
def test_e13_2_the_mock_api_cannot_be_loopback_metadata_multicast_or_ipv6(ip: str) -> None:
    with pytest.raises(ValueError):
        MockApi(host="sbam-mock-api", ip=ip, port=8080)


@pytest.mark.parametrize("host", ["", "UPPER", "a_b", "127.0.0.1", "a b", "a\n", "x.local", "*.x"])
def test_e13_2_the_mock_apis_name_is_a_plain_platform_name(host: str) -> None:
    with pytest.raises(ValueError):
        MockApi(host=host, ip="10.1.2.3", port=8080)


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_e13_2_the_mock_apis_port_is_a_real_port(port: int) -> None:
    with pytest.raises(ValueError):
        MockApi(host="sbam-mock-api", ip="10.1.2.3", port=port)


# --- the hosts file ---------------------------------------------------------------------------


def test_e13_2_the_hosts_file_has_loopback_and_only_the_pinned_names() -> None:
    text = render_hosts(
        {"b.example.com": ("1.2.3.4", "1.2.3.5"), "a.example.com": ("5.6.7.8",)}
    ).decode()
    assert text.splitlines() == [
        "127.0.0.1 localhost",
        "::1 localhost ip6-localhost ip6-loopback",
        "5.6.7.8 a.example.com",
        "1.2.3.4 b.example.com",
        "1.2.3.5 b.example.com",
    ]
    assert text.endswith("\n")


@pytest.mark.parametrize(
    "name",
    [
        "a.example.com\n9.9.9.9 evil",
        "a b",
        "a#b",
        "A.example.com",
        "a.example.com ",
        "é.example.com",
        "",
        "-a.example.com",
        "a.example.com\t",
        "a;b",
        "a\x00b",
    ],
)
def test_e13_2_a_name_that_could_add_a_line_or_a_second_name_is_refused(name: str) -> None:
    with pytest.raises(ValueError):
        render_hosts({name: ("1.2.3.4",)})


@pytest.mark.parametrize(
    "address", ["1.2.3.4\n9.9.9.9 evil", "1.2.3.4 evil", "::1", "1.2.3", "a.b.c.d", "", "1.2.3.4#x"]
)
def test_e13_2_an_address_that_is_not_a_dotted_ipv4_is_refused(address: str) -> None:
    with pytest.raises(ValueError):
        render_hosts({"a.example.com": (address,)})


def test_e13_2_the_hosts_file_is_plain_ascii() -> None:
    render_hosts({"a.example.com": ("1.2.3.4",)}).decode("ascii")


# --- the resolver's failure modes -------------------------------------------------------------


def patch_getaddrinfo(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException | None,
    found: Sequence[object] | None = None,
) -> None:
    def fake_getaddrinfo(*_: object, **__: object) -> Sequence[object]:
        if error is not None:
            raise error
        return found or []

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_e13_2_a_name_that_does_not_exist_is_a_policy_error_not_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_getaddrinfo(monkeypatch, socket.gaierror(socket.EAI_NONAME, "unknown"))
    with pytest.raises(EgressPolicyError, match="does not resolve"):
        asyncio.run(system_resolver("nope.example.com"))


def test_e13_2_a_resolver_outage_is_infrastructure_so_it_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_getaddrinfo(monkeypatch, socket.gaierror(socket.EAI_AGAIN, "try again"))
    with pytest.raises(SandboxInfraError) as caught:
        asyncio.run(system_resolver("a.example.com"))
    assert caught.value.reason is InfraReason.HOST_UNREACHABLE


def test_e13_2_a_resolver_that_hangs_is_infrastructure_too(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    def hang(*_a: object, **_k: object) -> list[object]:
        time.sleep(1.0)
        return []

    monkeypatch.setattr(policy_module, "RESOLVE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(socket, "getaddrinfo", hang)
    with pytest.raises(SandboxInfraError) as caught:
        asyncio.run(system_resolver("a.example.com"))
    assert caught.value.reason is InfraReason.HOST_UNREACHABLE


def test_e13_2_the_system_resolver_returns_sorted_unique_v4_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0))
        for a in ("5.5.5.5", "1.1.1.1", "5.5.5.5")
    ]
    patch_getaddrinfo(monkeypatch, None, rows)
    assert asyncio.run(system_resolver("a.example.com")) == ["1.1.1.1", "5.5.5.5"]


def test_e13_2_localhost_through_the_real_resolver_is_refused_by_the_planner() -> None:
    """No fake: whatever `localhost` resolves to on this machine must never become allowed."""

    async def go() -> NetworkPlan:
        return await plan_network(
            EgressPolicy(allow=["localhost.example:80"]), None, system_resolver_for_localhost
        )

    async def system_resolver_for_localhost(_: str) -> list[str]:
        return await system_resolver("localhost")

    with pytest.raises(EgressPolicyError):
        asyncio.run(go())
