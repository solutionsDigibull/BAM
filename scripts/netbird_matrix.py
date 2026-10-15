#!/usr/bin/env python3
"""Netbird reachability matrix for SBAM (E16.3, #142, TD 20.5).

Offline: computes from compose/netbird/policies.json which source group may reach which destination group on
which service, and checks the file against its own `must_deny` list (a laptop must never reach prod ops).
Live (on a Netbird peer, `--probe`): opens a TCP connection per (destination host, service) and compares the
result with the matrix for the group this peer is in. Probing sends one SYN per service, nothing else.

    python3 scripts/netbird_matrix.py                      # print the expected matrix, verify must_deny
    python3 scripts/netbird_matrix.py --json               # machine readable
    python3 scripts/netbird_matrix.py --probe --as dev-laptops \
        --host vm-dev=100.64.0.10 --host vm-prod=100.64.0.30  # T07: probes from a dev laptop
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any

POLICY_FILE = Path(__file__).resolve().parents[1] / "compose" / "netbird" / "policies.json"


def load(path: Path = POLICY_FILE) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def allowed_pairs(policy: dict[str, Any]) -> set[tuple[str, str, str]]:
    """Every (source group, destination group, service) some policy allows. Policies are one-way."""
    groups = set(policy["groups"])
    services = set(policy["services"])
    pairs: set[tuple[str, str, str]] = set()
    for rule in policy["policies"]:
        for name in (*rule["sources"], *rule["destinations"]):
            if name not in groups:
                raise ValueError(f"policy {rule['name']} uses unknown group {name}")
        for service in rule["services"]:
            if service not in services:
                raise ValueError(f"policy {rule['name']} uses unknown service {service}")
        for src in rule["sources"]:
            for dst in rule["destinations"]:
                pairs.update((src, dst, svc) for svc in rule["services"])
    return pairs


def is_allowed(policy: dict[str, Any], source: str, destination: str, service: str) -> bool:
    return (source, destination, service) in allowed_pairs(policy)


def violations(policy: dict[str, Any]) -> list[str]:
    """`must_deny` entries that some policy allows anyway, and obviously unsafe policies."""
    pairs = allowed_pairs(policy)
    problems = [
        f"{deny['source']} -> {deny['destination']} {svc} is allowed ({deny['why']})"
        for deny in policy["must_deny"]
        for svc in deny["services"]
        if (deny["source"], deny["destination"], svc) in pairs
    ]
    for rule in policy["policies"]:
        if rule.get("bidirectional"):
            problems.append(
                f"policy {rule['name']} is bidirectional; Netbird policies here are one-way"
            )
    # Production ops: only the platform group and the VMs-to-sandbox rule may touch vm-prod / sandbox-host.
    ops = set(policy["ops_services"])
    for src, dst, svc in sorted(pairs):
        if dst == "vm-prod" and svc in ops and src != "platform":
            problems.append(
                f"{src} reaches vm-prod {svc}: production ops is for the platform group only"
            )
    return problems


def matrix(policy: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """source group -> destination group -> allowed services."""
    out: dict[str, dict[str, list[str]]] = {}
    for src, dst, svc in sorted(allowed_pairs(policy)):
        out.setdefault(src, {}).setdefault(dst, []).append(svc)
    return out


def probe(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy", type=Path, default=POLICY_FILE)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--probe", action="store_true", help="live TCP probes (run on a Netbird peer)"
    )
    parser.add_argument("--as", dest="source", help="the Netbird group of THIS peer (with --probe)")
    parser.add_argument("--host", action="append", default=[], metavar="GROUP=ADDRESS")
    args = parser.parse_args(argv)

    policy = load(args.policy)
    problems = violations(policy)
    mx = matrix(policy)
    if args.json:
        print(json.dumps(mx, indent=2))
    else:
        for src, dsts in mx.items():
            for dst, services in dsts.items():
                print(f"{src:13} -> {dst:13} {', '.join(services)}")
    for problem in problems:
        print(f"FAIL  {problem}", file=sys.stderr)

    if args.probe:
        if not args.source:
            parser.error("--probe needs --as <your group>")
        for item in args.host:
            group, _, address = item.partition("=")
            for service, spec in policy["services"].items():
                if group == "vm-prod" and service == "portal":
                    continue  # prod's 443 is public by design: reachable with or without Netbird
                expected = is_allowed(policy, args.source, group, service)
                actual = probe(address, int(spec["port"]))
                verdict = "OK   " if expected == actual else "FAIL "
                if expected != actual:
                    problems.append(f"{args.source} -> {group} {service}")
                print(
                    f"{verdict} {args.source} -> {group}({address}) {service}:{spec['port']} "
                    f"expected {'open' if expected else 'blocked'}, got {'open' if actual else 'blocked'}"
                )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
