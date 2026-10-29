"""The VM's network, decided before it exists (E13.2, E13.1 safety checks, ADR 0021).

Pinned resolution. CubeSandbox lets an allowlist hold names, but a name is learned through DNS
inside the VM, and its precedence is allow > deny: a name that rebinds to a private address would
be allowed. So the worker resolves each allowlisted name once, requires every address to be
public, hands the host only those IPs and writes them into the VM's hosts file. The VM does no DNS
at all, so a rebind cannot happen; a host whose addresses rotate fails as EGRESS_BLOCKED, visibly.

Rules worth knowing
  * A name that does not exist, or has any non-public address, is a configuration error: the run
    fails as EGRESS_BLOCKED and names the host. A resolver that is down is infrastructure (retried).
  * IPv4 only (CubeSandbox's allowlist is IPv4). A name with no A record cannot be allowed.
  * CubeSandbox's `allow_out` has no port: an entry is enforced per host, whatever its port says.
    The port stays in the contract (and in the guest probe's evidence) but is not a control here.
  * The platform's mock API is pinned by the operator (name + IPv4 + port), never resolved, and
    may be a private or Netbird address. It must be a dedicated address: `allow_out` opens every
    port on it.
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final

from sbam_contracts.sandbox import (
    EgressPolicy,
    InfraReason,
    SandboxContractError,
    parse_egress_target,
)

from sbam_workers.sandbox.backend import SandboxInfraError

MAX_ADDRESSES_PER_HOST: Final = 4
_HOSTS_NAME: Final = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")
RESOLVE_TIMEOUT_S: Final = 5.0

Resolver = Callable[[str], Awaitable[list[str]]]


class EgressPolicyError(Exception):
    """An allowlisted host cannot be used safely: the run fails as EGRESS_BLOCKED."""

    def __init__(self, host: str, why: str) -> None:
        super().__init__(f"{host}: {why}")
        self.host = host
        self.why = why


@dataclass(frozen=True)
class MockApi:
    """The platform's own egress target, pinned by the operator."""

    host: str
    ip: str
    port: int

    def __post_init__(self) -> None:
        try:
            parse_egress_target(f"{self.host}:{self.port}", platform=True)
        except SandboxContractError as exc:
            raise ValueError(f"mock API: {exc}") from None
        address = ipaddress.ip_address(self.ip)
        if (
            address.version != 4
            or address.is_loopback
            or address.is_link_local  # includes the cloud metadata address
            or address.is_unspecified
            or address.is_multicast
            or address in ipaddress.ip_network("0.0.0.0/8")
        ):
            raise ValueError(
                "mock API: the address must be a unicast IPv4 that is not loopback or link-local"
            )


@dataclass(frozen=True)
class NetworkPlan:
    allow_out: tuple[str, ...]  # the only addresses the VM may reach
    hosts: Mapping[str, tuple[str, ...]]  # name -> addresses for the VM's hosts file
    probe: tuple[str, ...]  # `host:port` entries the guest probe records attempts against


def is_public_ipv4(text: str) -> bool:
    """A globally routable unicast IPv4. Everything else (private, CGNAT, link-local, loopback,
    multicast, reserved, documentation, benchmarking) is refused."""
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_unspecified
        and address != ipaddress.IPv4Address("255.255.255.255")
    )


async def system_resolver(host: str) -> list[str]:
    """A-record addresses of `host`. Raises `EgressPolicyError` for a name that does not exist and
    `SandboxInfraError` when the resolver itself is unavailable."""
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(RESOLVE_TIMEOUT_S):
            found = await loop.getaddrinfo(
                host, None, family=socket.AF_INET, type=socket.SOCK_STREAM
            )
    except TimeoutError:
        raise SandboxInfraError(InfraReason.HOST_UNREACHABLE, "the resolver timed out") from None
    except socket.gaierror as exc:
        if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)):
            raise EgressPolicyError(host, "the name does not resolve") from None
        raise SandboxInfraError(InfraReason.HOST_UNREACHABLE, "the resolver failed") from None
    return sorted({str(item[4][0]) for item in found})


async def plan_network(
    policy: EgressPolicy, mock_api: MockApi | None, resolver: Resolver = system_resolver
) -> NetworkPlan:
    allow: set[str] = set()
    hosts: dict[str, tuple[str, ...]] = {}
    for target in policy.targets():
        if target.host in hosts:
            continue  # the same name on a second port: one set of addresses
        addresses = await resolver(target.host)
        if not addresses:
            raise EgressPolicyError(target.host, "the name has no IPv4 address")
        bad = [a for a in addresses if not is_public_ipv4(a)]
        if bad:
            raise EgressPolicyError(target.host, "it resolves to a non-public address")
        pinned = tuple(sorted(set(addresses))[:MAX_ADDRESSES_PER_HOST])
        hosts[target.host] = pinned
        allow.update(pinned)
    probe = [str(t) for t in policy.targets()]
    if mock_api is not None:
        hosts[mock_api.host] = (mock_api.ip,)
        allow.add(mock_api.ip)
        probe.append(f"{mock_api.host}:{mock_api.port}")
    return NetworkPlan(tuple(sorted(allow)), hosts, tuple(probe))


def render_hosts(hosts: Mapping[str, tuple[str, ...]]) -> bytes:
    """The VM's whole hosts file: loopback plus the pinned names, nothing else resolvable.

    It is written as root, so it checks its own input: a name or address that could add a line, a
    comment or a second name is refused, whoever called.
    """
    lines = ["127.0.0.1 localhost", "::1 localhost ip6-localhost ip6-loopback"]
    for name in sorted(hosts):
        if not _HOSTS_NAME.fullmatch(name):
            raise ValueError("a hosts-file name must be plain lowercase ASCII")
        for address in hosts[name]:
            try:
                ipaddress.IPv4Address(address)
            except ValueError:
                raise ValueError("a hosts-file address must be a dotted IPv4") from None
            lines.append(f"{address} {name}")
    return ("\n".join(lines) + "\n").encode("ascii")
