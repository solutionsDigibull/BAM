"""The SSRF guard and the pinned HTTPS call behind Test connection (ADR 0030, decision 7).

Test connection sends a stored key to a URL a person typed, from inside our network. This module is
the control. It is built so the safe path is the only path:

- **https only**, no credentials, no fragment, a plain host. The key goes in one request header or
  one query parameter, never in the path.
- **The host is judged before it is resolved.** An IP written as a decimal, octal, hex or short
  number (`2130706433`, `0177.0.0.1`, `0x7f.1`, `127.1`) is one the system resolver would turn into
  an address the guard never saw as an address: all of those are refused as `BLOCKED_HOST`.
- **Every resolved address must be public.** One private answer among public ones blocks the whole
  name (a split answer is how an attacker hides a target). Refused: loopback, private, link-local
  (including 169.254.169.254), carrier-grade NAT, multicast, reserved, unspecified, unique-local,
  the documentation, benchmarking and Teredo ranges, and any IPv4 embedded in an IPv6 form (mapped,
  NAT64, 6to4) is judged as that IPv4.
- **Pinned.** The call connects to the validated address, not to the name; TLS checks the
  certificate against the original host name (SNI and verification); the `Host` header is the
  original name; and the connected peer address must equal the pinned one. The name is never
  resolved a second time, so a resolver that changes its mind (DNS rebinding) changes nothing.
- **No redirects, no body.** A 3xx is a result (`REDIRECT`), never followed. The response body
  is not read, kept or logged: only the status class leaves this module. 10 s limit.

The resolver and the transport are injected: tests mock both and never make a real call.
"""

import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import quote, urlsplit

from sbam_contracts.connections import (
    AuthScheme,
    ConnectionTestConfig,
    ConnectionTestOutcome,
    ProbeMethod,
)

TIMEOUT_SECONDS: Final = 10.0

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_NEVER_PUBLIC: Final = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",  # "this network"
        "100.64.0.0/10",  # carrier-grade NAT
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # documentation
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",
        "203.0.113.0/24",
        "240.0.0.0/4",  # reserved, and the broadcast address
        "2001::/32",  # Teredo
        "2001:db8::/32",  # documentation
        "100::/64",  # discard-only
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
        "ff00::/8",  # multicast
    )
)
_NAT64: Final = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR: Final = ipaddress.ip_network("2002::/16")


class BlockedHost(Exception):  # noqa: N818 - a fixed name in the ADR
    """The guard refused the target. The message is a fixed phrase, never the URL or the address."""


class Unreachable(Exception):  # noqa: N818
    """The connection could not be made (refused, reset, no route)."""


class TlsFailure(Exception):  # noqa: N818
    """The TLS handshake or certificate check failed."""


class CallTimeout(Exception):  # noqa: N818
    """No answer within the limit."""


class PeerMismatch(Exception):  # noqa: N818
    """The connected peer is not the pinned address: the call is dropped."""


def is_public(ip: IPAddress) -> bool:
    """True only for an address the public internet routes to. An IPv4 inside an IPv6 form is
    judged as that IPv4."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public(ip.ipv4_mapped)
        if ip in _NAT64:
            return is_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip in _SIX_TO_FOUR:
            return is_public(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
    if any(ip in net for net in _NEVER_PUBLIC if net.version == ip.version):
        return False
    return bool(
        ip.is_global
        and not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    )


@dataclass(frozen=True)
class Target:
    host: str  # as typed, lower case, no brackets: the name TLS and `Host` use
    port: int
    path_and_query: str  # the path, and the query before the key is added


def parse_target(url: str) -> Target:
    """The parts of a URL the guard accepts; anything unusual is `BlockedHost`."""
    if any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in url):
        raise BlockedHost("not a plain URL")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise BlockedHost("not a URL") from None
    if parts.scheme != "https":
        raise BlockedHost("only https")
    host = parts.hostname
    if not host or parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise BlockedHost("a host and no credentials")
    if parts.fragment or "\\" in url:
        raise BlockedHost("not a plain URL")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return Target(host.lower().rstrip("."), port or 443, path)


def host_address(host: str) -> IPAddress | None:
    """The address if `host` is an IP literal, None for a name. A number the system resolver would
    read as an address in some other notation (decimal, octal, hex, short) is refused."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        socket.inet_aton(host)  # accepts 2130706433, 0177.0.0.1, 0x7f.1, 127.1 ...
    except OSError:
        pass
    else:
        raise BlockedHost("an address in an unusual notation")
    if ":" in host or host.isdigit() or host.lower().startswith("0x"):
        raise BlockedHost("an address in an unusual notation")
    return None


Resolver = Callable[[str, int], Sequence[str]]


def system_resolver(host: str, port: int) -> Sequence[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise Unreachable("the name does not resolve") from None
    return [str(info[4][0]) for info in infos]


def pin_address(target: Target, resolve: Resolver) -> str:
    """The one validated address the call connects to. Every answer must be public."""
    literal = host_address(target.host)
    if literal is not None:
        if not is_public(literal):
            raise BlockedHost("not a public address")
        return str(literal)
    try:
        encoded = target.host.encode("idna").decode("ascii")
    except UnicodeError:
        raise BlockedHost("not a valid host name") from None
    answers = list(resolve(encoded, target.port))
    if not answers:
        raise Unreachable("the name does not resolve")
    addresses: list[IPAddress] = []
    for raw in answers:
        try:
            addresses.append(ipaddress.ip_address(raw.split("%", 1)[0]))
        except ValueError:
            raise BlockedHost("an unusable answer") from None
    if not all(is_public(a) for a in addresses):  # one private answer blocks the name
        raise BlockedHost("not a public address")
    return str(addresses[0])


# --- the request ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PinnedRequest:
    ip: str
    host: str
    port: int
    method: str
    path_and_query: str
    headers: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:  # the headers may hold the key
        return f"PinnedRequest({self.method} {self.host}:{self.port})"


class Transport(Protocol):
    def send(self, request: PinnedRequest) -> int:
        """The HTTP status. Raises `Unreachable`, `TlsFailure`, `CallTimeout` or `PeerMismatch`."""
        ...


class UnsendableValue(Exception):  # noqa: N818
    """The value cannot travel in a header (a line break or control character)."""


def build_request(
    target: Target, ip: str, config: ConnectionTestConfig, value: str
) -> PinnedRequest:
    """Put the key in exactly one header or one query parameter."""
    headers: list[tuple[str, str]] = [("Accept", "*/*"), ("User-Agent", "sbam-connection-test")]
    path = target.path_and_query
    if config.auth is AuthScheme.QUERY:
        name = config.auth_name or ""
        path += ("&" if "?" in path else "?") + f"{quote(name, safe='')}={quote(value, safe='')}"
    else:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise UnsendableValue
        if config.auth is AuthScheme.BEARER:
            headers.append(("Authorization", f"Bearer {value}"))
        elif config.auth is AuthScheme.BASIC:
            headers.append(("Authorization", f"Basic {value}"))
        else:
            headers.append((config.auth_name or "", value))
    return PinnedRequest(ip, target.host, target.port, config.method.value, path, tuple(headers))


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a fixed address: TLS is verified against the name, the peer must be the address."""

    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        self.tls = ssl.create_default_context()  # verifies the certificate and the host name
        super().__init__(host, port, timeout=timeout, context=self.tls)
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        try:
            peer = sock.getpeername()[0]
            if ipaddress.ip_address(str(peer).split("%", 1)[0]) != ipaddress.ip_address(self._ip):
                raise PeerMismatch("the peer is not the pinned address")
            self.sock = self.tls.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class HttpsTransport:
    """The real call. Not used by any test."""

    def __init__(self, timeout: float = TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    def send(self, request: PinnedRequest) -> int:
        conn = PinnedHTTPSConnection(request.host, request.ip, request.port, self.timeout)
        try:
            conn.request(request.method, request.path_and_query, headers=dict(request.headers))
            return conn.getresponse().status  # the body is never read
        except PeerMismatch:
            raise
        except TimeoutError:
            raise CallTimeout from None
        except ssl.SSLError:
            raise TlsFailure from None
        except (OSError, http.client.HTTPException):
            raise Unreachable("the connection failed") from None
        finally:
            conn.close()


# --- the result -----------------------------------------------------------------------------------

MESSAGES: Final = {
    ConnectionTestOutcome.OK: "The service accepted this key.",
    ConnectionTestOutcome.UNAUTHORIZED: "The service rejected this key.",
    ConnectionTestOutcome.NOT_FOUND: "The service answered not found: check the test URL.",
    ConnectionTestOutcome.RATE_LIMITED: "The service is rate limiting this key; try again later.",
    ConnectionTestOutcome.SERVER_ERROR: "The service answered with a server error.",
    ConnectionTestOutcome.REDIRECT: "The service redirected; redirects are not followed.",
    ConnectionTestOutcome.UNREACHABLE: "The service could not be reached or gave no pass.",
    ConnectionTestOutcome.TIMEOUT: "The service did not answer within 10 seconds.",
    ConnectionTestOutcome.TLS_ERROR: "The service's certificate or TLS handshake was refused.",
    ConnectionTestOutcome.BLOCKED_HOST: "This address is not allowed as a test target.",
}


@dataclass(frozen=True)
class ProbeResult:
    outcome: ConnectionTestOutcome
    http_status_class: int | None = None

    @property
    def message(self) -> str:
        return MESSAGES[self.outcome]


def outcome_of(status: int) -> ConnectionTestOutcome:
    """OK for 2xx; 401 and 403 are 'the service rejected this key'. Any other 4xx is not a pass and
    is reported as `UNREACHABLE` (the answer is no use as a test), never as success."""
    if 200 <= status < 300:
        return ConnectionTestOutcome.OK
    if 300 <= status < 400:
        return ConnectionTestOutcome.REDIRECT
    if status in (401, 403):
        return ConnectionTestOutcome.UNAUTHORIZED
    if status in (404, 410):
        return ConnectionTestOutcome.NOT_FOUND
    if status == 429:
        return ConnectionTestOutcome.RATE_LIMITED
    if status >= 500:
        return ConnectionTestOutcome.SERVER_ERROR
    return ConnectionTestOutcome.UNREACHABLE


def probe(
    config: ConnectionTestConfig,
    value: str,
    *,
    resolve: Resolver = system_resolver,
    transport: Transport | None = None,
) -> ProbeResult:
    """Test one key against the service the FDE named. Never raises for the target's behaviour:
    every failure is an outcome. Raises `UnsendableValue` only for a value that cannot travel."""
    try:
        target = parse_target(config.url)
        ip = pin_address(target, resolve)
    except BlockedHost:
        return ProbeResult(ConnectionTestOutcome.BLOCKED_HOST)
    except Unreachable:
        return ProbeResult(ConnectionTestOutcome.UNREACHABLE)
    request = build_request(target, ip, config, value)  # may raise UnsendableValue
    try:
        status = (transport or HttpsTransport()).send(request)
    except PeerMismatch:
        return ProbeResult(ConnectionTestOutcome.BLOCKED_HOST)
    except CallTimeout:
        return ProbeResult(ConnectionTestOutcome.TIMEOUT)
    except TlsFailure:
        return ProbeResult(ConnectionTestOutcome.TLS_ERROR)
    except Unreachable:
        return ProbeResult(ConnectionTestOutcome.UNREACHABLE)
    return ProbeResult(outcome_of(status), status // 100 if 100 <= status < 600 else None)


__all__ = ["ProbeMethod", "ProbeResult", "probe"]
