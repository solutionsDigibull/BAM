#!/usr/bin/env python3
"""Apply the deployed sign-in hardening to a realm that already exists (A-18d, #543).

`--import-realm` skips a realm that exists, so the password policy, the TOTP flow and the marker
role that `render_realm.py` writes reach a running staging or production realm only through
this script.
It uses the same definitions (`render_realm.harden`'s pieces: `PASSWORD_POLICY`, `mfa_flows`,
`MFA_ROLE`), so there is no second copy to drift, and it only talks to the Keycloak admin REST API.

    KEYCLOAK_ADMIN_PASSWORD=... python compose/keycloak/harden_realm.py \\
        --url http://localhost:8080 --blacklist-mounted [--mfa admins|all|off] [--dry-run]
    ... --rollback          # back to Keycloak's own browser flow, no policy, no marker role

* Idempotent: a second run finds the realm as wanted and changes nothing. A different `--mfa`
  rebuilds the `browser-mfa` flows. `--dry-run` prints what would change and changes nothing.
* The password policy names `common-passwords.txt`, which Keycloak reads from its own
  `data/password-blacklists/`: compose.deploy.yaml mounts it. A policy that names a missing file
  rejects every password change, so the script will not apply the policy unless you say the file
  is there (`--blacklist-mounted`).
* Order matters: the `browser-mfa` flows are built and checked first and only then bound as the
  realm's browser flow, so a failure half way never leaves the realm on a half-built flow.
* Users who hold `sbam-admin` or `super-admin` enrol TOTP at their next sign-in (see the runbook).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from importlib import util
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
spec = util.spec_from_file_location("render_realm", HERE / "render_realm.py")
if spec is None or spec.loader is None:  # pragma: no cover
    raise SystemExit("cannot load render_realm.py")
render_realm = util.module_from_spec(spec)
spec.loader.exec_module(render_realm)

STOCK_BROWSER = "browser"


class KeycloakError(Exception):
    pass


class Admin:
    """The few admin REST calls this needs, on urllib (no dependency on the operator's host)."""

    def __init__(self, url: str, realm: str, user: str, password: str) -> None:
        self.base = url.rstrip("/")
        self.realm = realm
        self.token = self._login(user, password)

    def _call(self, method: str, url: str, body: Any = None, form: bool = False) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
        if getattr(self, "token", None):
            headers["Authorization"] = f"Bearer {self.token}"
        if not url.startswith(("http://", "https://")):
            raise KeycloakError(f"not an http(s) URL: {url}")
        req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310
                raw = response.read()
                return (json.loads(raw) if raw else None), dict(response.headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise KeycloakError(f"{method} {url}: {exc.code} {detail}") from None
        except urllib.error.URLError as exc:
            raise KeycloakError(f"{method} {url}: {exc.reason}") from None

    def _login(self, user: str, password: str) -> str:
        url = f"{self.base}/realms/master/protocol/openid-connect/token"
        form = {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": user,
            "password": password,
        }
        body, _ = self._call("POST", url, form, form=True)
        return str(body["access_token"])

    def api(self, method: str, path: str, body: Any = None) -> Any:
        result, _ = self._call(method, f"{self.base}/admin/realms/{self.realm}{path}", body)
        return result

    def post_location(self, path: str, body: Any) -> str:
        _, headers = self._call("POST", f"{self.base}/admin/realms/{self.realm}{path}", body)
        return headers.get("Location", "")


class Plan:
    """What was (or, in a dry run, would be) done, in order."""

    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.steps: list[str] = []

    def do(self, text: str) -> bool:
        """Record a step; True when it should really be performed."""
        self.steps.append(text)
        return not self.dry_run


def quoted(alias: str) -> str:
    return urllib.parse.quote(alias, safe="")


def aliases_of_ours() -> list[str]:
    """Every flow alias this script may create, whatever the mode (what a rebuild removes)."""
    names: list[str] = []
    for mode in ("admins", "all"):
        names += [f["alias"] for f in render_realm.mfa_flows(mode)[0]]
    return [*dict.fromkeys(names)]


def fill(
    kc: Admin,
    definitions: dict[str, dict[str, Any]],
    configs: dict[str, dict[str, Any]],
    alias: str,
) -> None:
    """The steps of the existing flow `alias`: create each, set its requirement and config (the
    order of creation is the order of the flow), then fill the sub-flows."""
    steps = definitions[alias]["authenticationExecutions"]
    for step in steps:
        if step["authenticatorFlow"]:
            sub = step["flowAlias"]
            body = {
                "alias": sub,
                "type": "basic-flow",
                "provider": "registration-page-form",
                "description": sub,
            }
            kc.api("POST", f"/authentication/flows/{quoted(alias)}/executions/flow", body)
        else:
            body = {"provider": step["authenticator"]}
            kc.api(
                "POST",
                f"/authentication/flows/{quoted(alias)}/executions/execution",
                body,
            )
    listed = kc.api("GET", f"/authentication/flows/{quoted(alias)}/executions")
    direct = [e for e in listed if e["level"] == 0]
    if len(direct) != len(steps):
        raise KeycloakError(f"{alias}: Keycloak lists {len(direct)} steps, expected {len(steps)}")
    for info, step in zip(direct, steps, strict=True):
        info["requirement"] = step["requirement"]
        kc.api("PUT", f"/authentication/flows/{quoted(alias)}/executions", info)
        if step.get("authenticatorConfig"):
            kc.api(
                "POST",
                f"/authentication/executions/{info['id']}/config",
                configs[step["authenticatorConfig"]],
            )
    for step in steps:
        if step["authenticatorFlow"]:
            fill(kc, definitions, configs, step["flowAlias"])


def flow_matches(kc: Admin, definitions: dict[str, dict[str, Any]], alias: str) -> bool:
    """Does the live flow have exactly these steps and requirements (recursively)?"""
    steps = definitions[alias]["authenticationExecutions"]
    try:
        listed = kc.api("GET", f"/authentication/flows/{quoted(alias)}/executions")
    except KeycloakError:
        return False
    direct = [e for e in listed if e["level"] == 0]
    if len(direct) != len(steps):
        return False
    for info, step in zip(direct, steps, strict=True):
        name = step["flowAlias"] if step["authenticatorFlow"] else step["authenticator"]
        live = info.get("displayName") if step["authenticatorFlow"] else info.get("providerId")
        if live != name or info["requirement"] != step["requirement"]:
            return False
        if step["authenticatorFlow"] and not flow_matches(kc, definitions, step["flowAlias"]):
            return False
    return True


def role_state(kc: Admin) -> tuple[bool, list[str]]:
    """(does the marker role exist, which of the two admin roles include it)."""
    marker = render_realm.MFA_ROLE
    try:
        kc.api("GET", f"/roles/{quoted(marker)}")
    except KeycloakError:
        return False, []
    carriers = []
    for name in render_realm.MFA_ROLES:
        composites = kc.api("GET", f"/roles/{quoted(name)}/composites") or []
        if any(c["name"] == marker for c in composites):
            carriers.append(name)
    return True, carriers


def apply(kc: Admin, mode: str, plan: Plan) -> None:
    realm = kc.api("GET", "")
    marker = render_realm.MFA_ROLE
    # 1. password policy
    wanted_policy = render_realm.PASSWORD_POLICY if mode != "rollback" else ""
    if (realm.get("passwordPolicy") or "") != wanted_policy and plan.do(
        f"set passwordPolicy to {wanted_policy!r}"
    ):
        kc.api("PUT", "", {**realm, "passwordPolicy": wanted_policy})
    if mode == "off":
        return
    # 2. the marker role and the roles that include it
    exists, carriers = role_state(kc)
    want_marker = mode == "admins"
    if want_marker and not exists and plan.do(f"create role {marker!r}"):
        kc.api(
            "POST",
            "/roles",
            {"name": marker, "description": "Marker: sign in with a second factor"},
        )
    for name in render_realm.MFA_ROLES:
        has = name in carriers
        if want_marker and not has and plan.do(f"add {marker!r} to role {name!r}"):
            role = kc.api("GET", f"/roles/{quoted(marker)}")
            kc.api("POST", f"/roles/{quoted(name)}/composites", [role])
        if not want_marker and has and plan.do(f"remove {marker!r} from role {name!r}"):
            role = kc.api("GET", f"/roles/{quoted(marker)}")
            kc.api("DELETE", f"/roles/{quoted(name)}/composites", [role])
    # 3. the flows, then (only then) the binding
    bound = realm.get("browserFlow")
    if mode == "rollback":
        if bound != STOCK_BROWSER and plan.do(f"bind browser flow {STOCK_BROWSER!r}"):
            kc.api("PUT", "", {**kc.api("GET", ""), "browserFlow": STOCK_BROWSER})
        existing = {f["alias"]: f["id"] for f in kc.api("GET", "/authentication/flows")}
        for alias in reversed(aliases_of_ours()):
            if alias in existing and plan.do(f"delete flow {alias!r}"):
                kc.api("DELETE", f"/authentication/flows/{existing[alias]}")
        if exists and plan.do(f"delete role {marker!r}"):
            kc.api("DELETE", f"/roles/{quoted(marker)}")
        return
    flows, config_list = render_realm.mfa_flows(mode)
    definitions = {f["alias"]: f for f in flows}
    configs = {c["alias"]: c for c in config_list}
    top = render_realm.FLOW
    rebuilt = False
    if not (bound == top and flow_matches(kc, definitions, top)):
        rebuilt = True
        if bound == top and plan.do(f"unbind {top!r} (rebuilding it)"):
            kc.api("PUT", "", {**kc.api("GET", ""), "browserFlow": STOCK_BROWSER})
        existing = {f["alias"]: f["id"] for f in kc.api("GET", "/authentication/flows")}
        for alias in reversed(aliases_of_ours()):
            if alias in existing and plan.do(f"delete flow {alias!r}"):
                kc.api("DELETE", f"/authentication/flows/{existing[alias]}")
        if plan.do(f"build flow {top!r} ({mode})"):
            kc.api(
                "POST",
                "/authentication/flows",
                {
                    "alias": top,
                    "description": definitions[top]["description"],
                    "providerId": "basic-flow",
                    "topLevel": True,
                    "builtIn": False,
                },
            )
            fill(kc, definitions, configs, top)
            if not flow_matches(kc, definitions, top):  # never bind what is not what was asked
                raise KeycloakError(f"flow {top!r} was built but does not match: not bound")
    if (rebuilt or bound != top) and plan.do(f"bind browser flow {top!r}"):
        kc.api("PUT", "", {**kc.api("GET", ""), "browserFlow": top})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url", required=True, help="Keycloak base URL, e.g. http://localhost:8080"
    )
    parser.add_argument("--realm", default="bullm")
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--mfa", choices=render_realm.MFA_MODES, default="admins")
    parser.add_argument("--rollback", action="store_true", help="undo this script's changes")
    parser.add_argument("--dry-run", action="store_true", help="print the changes, make none")
    parser.add_argument(
        "--blacklist-mounted",
        action="store_true",
        help=f"confirm {render_realm.BLACKLIST} is in Keycloak's data/password-blacklists/",
    )
    args = parser.parse_args(argv)
    mode = "rollback" if args.rollback else args.mfa
    if mode != "rollback" and not args.blacklist_mounted:
        print(
            f"refusing: the policy names {render_realm.BLACKLIST}; mount it (compose.deploy.yaml) "
            "and pass --blacklist-mounted, or every password change is rejected",
            file=sys.stderr,
        )
        return 2
    password = os.environ.get("KEYCLOAK_ADMIN_PASSWORD")
    if not password:
        print("KEYCLOAK_ADMIN_PASSWORD is not set", file=sys.stderr)
        return 2
    plan = Plan(args.dry_run)
    try:
        kc = Admin(args.url, args.realm, args.admin_user, password)
        apply(kc, mode, plan)
    except KeycloakError as exc:
        print(f"harden_realm: {exc}", file=sys.stderr)
        return 1
    for step in plan.steps:
        print(("would " if args.dry_run else "") + step)
    if not plan.steps:
        print("nothing to change: the realm is already as wanted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
