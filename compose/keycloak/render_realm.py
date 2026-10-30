#!/usr/bin/env python3
"""Render compose/keycloak/realm-bullm.json for a deployed origin (E16.3, #142).

The committed realm file is the local-dev form: its redirect URIs and web origins are
`http://localhost:${WEB_PORT:3000}` (Keycloak fills the placeholder from the container env). A
deployed environment serves the portals at https://<SBAM_PORTAL_HOST>, so this rewrites every such
URL and writes the file that compose.deploy.yaml mounts through SBAM_REALM_FILE. The committed file
is not changed.

The same step hardens the sign-in for a deployed environment (A-18, #438): a password policy and a
second factor. The committed dev file keeps both off, so the seed users still work.

* Password policy: at least 12 characters, not one of the last 5, not the username or email, and not
  in `password-blacklist.txt`. compose.deploy.yaml mounts that file where Keycloak looks for it: a
  policy that names a missing file rejects every password change, and an unknown policy name stops
  Keycloak from starting, so the policy string is checked against a real Keycloak (see
  tests/integration/test_a18_keycloak_hardening.py).
* Second factor (`--mfa`): `admins` (default) asks TOTP of every user who holds `sbam-admin` or
  `super-admin`; `all` asks everyone (the FDE policy flag); `off` leaves the browser flow as
  Keycloak ships it. A user with no authenticator yet is sent to set one up at that sign-in.
  WebAuthn is not offered: see `mfa_flows`.
* The two admin roles include the marker role `mfa-required`, which a single role condition in the
  flow tests (two conditions on a subflow are ANDed, and two subflows would ask a user with both
  roles twice). SBAM keeps only its three portal roles from a token: the marker is invisible to it.

    python compose/keycloak/render_realm.py \\
        --origin https://portal.example.com --out /etc/sbam/realm-bullm.json [--mfa admins|all|off]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

REALM = Path(__file__).with_name("realm-bullm.json")
LOCAL_ORIGIN = "http://localhost:${WEB_PORT:3000}"

# The file name is relative to Keycloak's data/password-blacklists/ (see compose.deploy.yaml).
BLACKLIST = "common-passwords.txt"
PASSWORD_POLICY = (
    "length(12) and passwordHistory(5) and notUsername and notEmail "
    f"and passwordBlacklist({BLACKLIST})"
)
MFA_ROLE = "mfa-required"
MFA_ROLES = ("sbam-admin", "super-admin")  # the roles that include MFA_ROLE
MFA_MODES = ("admins", "all", "off")
FLOW = "browser-mfa"


def _step(
    name: str,
    requirement: str,
    priority: int,
    *,
    flow: bool = False,
    setup: bool = False,
    **extra: object,
) -> dict:
    key = "flowAlias" if flow else "authenticator"
    return {
        key: name,
        "requirement": requirement,
        "priority": priority,
        "authenticatorFlow": flow,
        "userSetupAllowed": setup,
        **extra,
    }


def mfa_flows(mode: str) -> tuple[list[dict], list[dict]]:
    """The browser flow with a second factor, and the authenticator config it needs. Same shape as
    Keycloak's own `browser` flow (cookie, identity provider redirector, forms), with TOTP after the
    password: asked only of users holding MFA_ROLE (`admins`) or of everyone (`all`). The OTP step
    is REQUIRED with user setup allowed, so a user with no authenticator yet is sent to set one up
    at that sign-in. (An ALTERNATIVE step cannot do that: Keycloak has nothing to enrol into and
    the sign-in fails; and a REQUIRED step makes it ignore ALTERNATIVE siblings, so WebAuthn cannot
    sit beside it.)
    """
    forms = f"{FLOW} forms"

    def basic(alias: str, steps: list[dict], *, top: bool = False) -> dict:
        return {
            "alias": alias,
            "description": "SBAM: password, then TOTP" if top else alias,
            "providerId": "basic-flow",
            "topLevel": top,
            "builtIn": False,
            "authenticationExecutions": steps,
        }

    otp = _step("auth-otp-form", "REQUIRED", 20, setup=True)
    flows = [
        basic(
            FLOW,
            [
                _step("auth-cookie", "ALTERNATIVE", 10),
                _step("identity-provider-redirector", "ALTERNATIVE", 25),
                _step(forms, "ALTERNATIVE", 30, flow=True),
            ],
            top=True,
        )
    ]
    configs: list[dict] = []
    password = _step("auth-username-password-form", "REQUIRED", 10)
    if mode == "all":
        flows.append(basic(forms, [password, otp]))
    else:
        gate = f"{FLOW} admins"
        flows.append(basic(forms, [password, _step(gate, "CONDITIONAL", 20, flow=True)]))
        role = _step("conditional-user-role", "REQUIRED", 10, authenticatorConfig=MFA_ROLE)
        flows.append(basic(gate, [role, {**otp, "priority": 20}]))
        configs.append({"alias": MFA_ROLE, "config": {"condUserRole": MFA_ROLE, "negate": "false"}})
    return flows, configs


def harden(realm: dict, mfa: str = "admins") -> dict:
    """The deployed sign-in: password policy, and the second factor (see the module docstring)."""
    if mfa not in MFA_MODES:
        raise ValueError(f"--mfa must be one of {', '.join(MFA_MODES)}, got {mfa!r}")
    realm["passwordPolicy"] = PASSWORD_POLICY
    if mfa == "off":
        return realm
    flows, configs = mfa_flows(mfa)
    realm["authenticationFlows"] = flows  # Keycloak adds its own flows around these
    realm["authenticatorConfig"] = configs
    realm["browserFlow"] = FLOW
    if mfa == "admins":
        roles = realm.setdefault("roles", {}).setdefault("realm", [])
        roles.append(
            {
                "name": MFA_ROLE,
                "description": "Marker: holders sign in with a second factor",
            }
        )
        for role in roles:
            if role["name"] in MFA_ROLES:
                role["composite"] = True
                role["composites"] = {"realm": [MFA_ROLE]}
    return realm


def render(realm_text: str, origin: str, mfa: str = "admins") -> str:
    """Return the realm JSON text with the local origin replaced by `origin`, and the sign-in of a
    deployed environment hardened (`harden`)."""
    parts = urlsplit(origin)
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(f"origin must be https://<host> with no path, got {origin!r}")
    if parts.hostname in ("localhost", "127.0.0.1") or not re.fullmatch(
        r"[A-Za-z0-9.-]+(:\d+)?", parts.netloc
    ):
        raise ValueError(f"origin must be a public hostname, got {origin!r}")
    rendered = realm_text.replace(LOCAL_ORIGIN, f"https://{parts.netloc}")
    if "localhost" in rendered:
        raise ValueError(
            "the realm still references localhost after rendering; update LOCAL_ORIGIN handling"
        )
    return (
        json.dumps(harden(json.loads(rendered), mfa), indent=2) + "\n"
    )  # fail here, not at import


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--origin",
        required=True,
        help="public portal origin, e.g. https://portal.example.com",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="file to write (world-readable: it holds no secrets)",
    )
    parser.add_argument(
        "--mfa",
        choices=MFA_MODES,
        default="admins",
        help="who signs in with a second factor: admins (default), all users, or nobody",
    )
    args = parser.parse_args(argv)
    try:
        text = render(REALM.read_text(encoding="utf-8"), args.origin, args.mfa)
    except ValueError as exc:
        print(f"render_realm: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(text, encoding="utf-8")
    args.out.chmod(0o644)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
