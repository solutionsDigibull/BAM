"""The Staging egress allowlist (#765, ADR 0032 decision 3: real services only within the hosts the
code declares).

A Staging container has no route to the outside except through the egress proxy
(`sbam_workers.staging.egress_proxy`), and the proxy lets a host through only when `EgressPolicy`
allows it. The allowed hosts are the certified manifest's `ui_bundle.allowed_hosts` (the code's own
declaration); nothing else is ever added at run time. A host matches by exact name, or by `*.name`
for any subdomain (not the bare name). A decision is made on the DNS name the caller asked for, in
lower case without a port or a trailing dot; an IP literal is never allowed.
"""

import ipaddress
import logging
from collections.abc import Iterable
from dataclasses import dataclass

log = logging.getLogger("sbam.staging.egress")


def normalise(host: str) -> str:
    """`Example.COM.:443` -> `example.com`; a bracketed or bare IP literal stays an IP literal."""
    name = host.strip().lower()
    if name.startswith("["):
        return name[1 : name.find("]")] if "]" in name else name
    if name.count(":") == 1:
        name = name.split(":", 1)[0]
    return name.rstrip(".")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class EgressDecision:
    host: str
    allowed: bool
    reason: str


class EgressPolicy:
    def __init__(self, allowed_hosts: Iterable[str]) -> None:
        self.allowed_hosts = tuple(sorted({normalise(h) for h in allowed_hosts if h.strip()}))

    def decide(self, host: str) -> EgressDecision:
        name = normalise(host)
        if not name or _is_ip(name):
            return EgressDecision(name, False, "an IP address or empty host is never allowed")
        for pattern in self.allowed_hosts:
            if pattern == name:
                return EgressDecision(name, True, "declared by the workflow")
            if pattern.startswith("*.") and name.endswith(pattern[1:]) and name != pattern[2:]:
                return EgressDecision(name, True, f"matches {pattern}")
        return EgressDecision(name, False, "not declared by the workflow")

    def check(self, host: str, *, deployment: str) -> EgressDecision:
        """Decide and log a block (the log line names the deployment and the host, nothing else)."""
        decision = self.decide(host)
        if not decision.allowed:
            log.warning(
                "staging egress blocked: deployment=%s host=%s (%s)",
                deployment,
                decision.host,
                decision.reason,
            )
        return decision
