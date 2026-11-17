"""Which hosts the fetcher may reach (WG-7: "only hosts from the approved sources are reachable").

A repository may come only from the ISATVON, the Admin masters or the FDE, so the approved hosts are
the hosts of the URLs those sources gave (the caller passes them), plus the four fixed services the
checks need (package indexes, the vulnerability database, the host API for project health). Anything
else is refused before a connection is made: a non-https URL, credentials or a query in the URL,
an IP
address in any form, a private or internal name, and a name that resolves to a private address.
"""

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

FIXED_HOSTS = frozenset({"pypi.org", "registry.npmjs.org", "api.osv.dev", "api.github.com"})
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".corp", ".home", ".intranet")

Resolver = Callable[[str], list[str]]


class HostRefused(Exception):  # noqa: N818 - a refusal, not a failure
    def __init__(self, host: str, why: str) -> None:
        super().__init__(f"{host}: {why}")
        self.host, self.why = host, why


def system_resolver(host: str) -> list[str]:
    try:
        return sorted(
            {str(i[4][0]) for i in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        )
    except OSError:
        return []


def _is_ip(host: str) -> bool:
    bare = host.strip("[]")
    try:
        ipaddress.ip_address(bare)
        return True
    except ValueError:
        pass
    # 2130706433, 0x7f000001 and 0177.0.0.1 are all 127.0.0.1 to a resolver
    return bare.replace(".", "").isdigit() or bare.lower().startswith("0x")


def _public(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def host_of(url: str) -> str:
    """The host of an https URL without credentials or a query; anything else is refused."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https":
        raise HostRefused(host or url[:60], "only https is allowed")
    if parts.username or parts.password or "@" in parts.netloc:
        raise HostRefused(host, "credentials in the URL")
    if parts.query:
        raise HostRefused(host, "a query string in the URL")
    if not host or host == "localhost" or "." not in host or _is_ip(host):
        raise HostRefused(host or "?", "not a public host name")
    if host.endswith(_PRIVATE_SUFFIXES):
        raise HostRefused(host, "a private host name")
    return host


@dataclass(frozen=True)
class HostPolicy:
    approved: frozenset[str]
    resolver: Resolver = field(default=system_resolver)

    @classmethod
    def for_urls(cls, urls: Iterable[str], resolver: Resolver = system_resolver) -> "HostPolicy":
        """The hosts of the repositories the catalog, the ISATVON or the FDE named."""
        hosts: set[str] = set()
        for url in urls:
            try:
                hosts.add(host_of(url))
            except HostRefused:
                continue  # an unsafe entry approves nothing
        return cls(frozenset(hosts), resolver)

    def check(self, url: str) -> str:
        """The host of `url`, if it may be reached; otherwise `HostRefused`. Resolves the name."""
        host = host_of(url)
        if host not in self.approved and host not in FIXED_HOSTS:
            raise HostRefused(host, "not an approved source")
        addresses = self.resolver(host)
        if not addresses or not all(_public(a) for a in addresses):
            raise HostRefused(host, "does not resolve to a public address")
        return host

    def check_repo(self, url: str) -> str:
        """A repository URL: its host must be one the sources approved (not only a fixed
        service)."""
        host = host_of(url)
        if host not in self.approved:
            raise HostRefused(host, "not an approved source")
        return self.check(url)
