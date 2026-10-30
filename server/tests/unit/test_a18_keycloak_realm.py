"""A-18 (#438): the realm hardens sign-in for a deployed environment and leaves dev alone.

The structure is checked here. Whether Keycloak accepts it and enforces it is checked against a real
Keycloak in tests/integration/test_a18_keycloak_hardening.py.
"""

import copy
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

COMPOSE = Path(__file__).resolve().parents[3] / "compose"
spec = importlib.util.spec_from_file_location(
    "render_realm", COMPOSE / "keycloak" / "render_realm.py"
)
assert spec and spec.loader
render_realm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_realm)

SOURCE = (COMPOSE / "keycloak" / "realm-bullm.json").read_text(encoding="utf-8")
ORIGIN = "https://portal.example.com"
# Keycloak's own ids: an unknown one stops the server from starting (found the hard way).
POLICY_IDS = {"length", "passwordHistory", "notUsername", "notEmail", "passwordBlacklist"}
AUTHENTICATORS = {
    "auth-cookie",
    "identity-provider-redirector",
    "auth-username-password-form",
    "conditional-user-role",
    "auth-otp-form",
    "webauthn-authenticator",
    "conditional-user-configured",
    "conditional-credential",
}


def realm(mfa: str = "admins") -> dict[str, Any]:
    return json.loads(render_realm.render(SOURCE, ORIGIN, mfa))  # type: ignore[no-any-return]


def policy(text: str) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for part in text.split(" and "):
        name, _, arg = part.partition("(")
        found[name] = arg.rstrip(")") or None
    return found


def executions(r: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {f["alias"]: f["authenticationExecutions"] for f in r["authenticationFlows"]}


def test_a18_t01_the_committed_dev_realm_has_events_but_no_policy_and_no_mfa() -> None:
    dev = json.loads(SOURCE.replace("${WEB_PORT:3000}", "3000"))
    assert dev["eventsEnabled"] and dev["adminEventsEnabled"] and dev["adminEventsDetailsEnabled"]
    assert {"LOGIN_ERROR", "UPDATE_PASSWORD", "REMOVE_TOTP"} <= set(dev["enabledEventTypes"])
    assert "jboss-logging" in dev["eventsListeners"] and dev["eventsExpiration"] > 0
    assert not {"passwordPolicy", "authenticationFlows", "browserFlow"} & set(
        dev
    )  # seed users work


def test_a18_t02_the_rendered_realm_has_the_password_policy() -> None:
    got = policy(realm()["passwordPolicy"])
    assert set(got) == POLICY_IDS
    assert int(got["length"] or 0) >= 12 and int(got["passwordHistory"] or 0) >= 5
    assert got["passwordBlacklist"] == render_realm.BLACKLIST


FACTOR_FLOWS = {
    "browser-mfa second factor",
    "browser-mfa choose factor",
    "browser-mfa enrol factor",
}


def by_name(steps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e.get("authenticator") or e["flowAlias"]: e for e in steps}


def test_a18_t03_admins_mode_gates_the_second_factor_on_the_marker_role() -> None:
    r = realm("admins")
    assert r["browserFlow"] == "browser-mfa"
    steps = executions(r)
    assert set(steps) == {"browser-mfa", "browser-mfa forms", "browser-mfa admins"} | FACTOR_FLOWS
    gate = by_name(steps["browser-mfa admins"])
    assert gate["conditional-user-role"]["requirement"] == "REQUIRED"
    assert gate["conditional-user-role"]["authenticatorConfig"] == "mfa-required"
    assert gate["browser-mfa second factor"]["requirement"] == "REQUIRED"
    forms = by_name(steps["browser-mfa forms"])
    assert forms["browser-mfa admins"]["requirement"] == "CONDITIONAL"
    assert forms["auth-username-password-form"]["requirement"] == "REQUIRED"
    assert {c["alias"] for c in r["authenticatorConfig"]} == {
        "mfa-required",
        "mfa-required-no-factor-used",
    }


def test_a18_t03b_the_second_factor_offers_totp_and_a_key_and_enrols_totp() -> None:
    steps = executions(realm("admins"))
    second = by_name(steps["browser-mfa second factor"])
    assert second["browser-mfa choose factor"]["requirement"] == "CONDITIONAL"
    assert second["browser-mfa enrol factor"]["requirement"] == "CONDITIONAL"
    choose = by_name(steps["browser-mfa choose factor"])
    assert choose["conditional-user-configured"]["requirement"] == "REQUIRED"
    assert choose["webauthn-authenticator"]["requirement"] == "ALTERNATIVE"
    assert choose["auth-otp-form"]["requirement"] == "ALTERNATIVE"
    assert choose["webauthn-authenticator"]["priority"] < choose["auth-otp-form"]["priority"]
    enrol = by_name(steps["browser-mfa enrol factor"])
    # REQUIRED + user setup is the only shape that enrols; ALTERNATIVE would fail the sign-in
    assert enrol["auth-otp-form"]["requirement"] == "REQUIRED"
    assert enrol["auth-otp-form"]["userSetupAllowed"] is True
    assert enrol["conditional-credential"]["authenticatorConfig"] == "mfa-required-no-factor-used"


def test_a18_t03c_the_enrol_step_runs_only_when_no_second_factor_was_used() -> None:
    config = next(
        c
        for c in realm("admins")["authenticatorConfig"]
        if c["alias"] == "mfa-required-no-factor-used"
    )["config"]
    assert config["included"] == "false"  # true only when none of the credentials below was used
    assert set(config["credentials"].split("##")) == {"otp", "webauthn", "webauthn-passwordless"}


def test_a18_t04_only_the_two_admin_roles_include_the_marker() -> None:
    roles = {x["name"]: x for x in realm("admins")["roles"]["realm"]}
    assert roles["mfa-required"].get("composite") is not True
    composite = {n for n, x in roles.items() if x.get("composite")}
    assert composite == {"sbam-admin", "super-admin"}
    assert all(roles[n]["composites"] == {"realm": ["mfa-required"]} for n in composite)


def test_a18_t05_all_mode_asks_everyone_with_no_role_condition() -> None:
    r = realm("all")
    steps = executions(r)
    assert set(steps) == {"browser-mfa", "browser-mfa forms"} | FACTOR_FLOWS
    forms = [e.get("authenticator") or e["flowAlias"] for e in steps["browser-mfa forms"]]
    assert forms == ["auth-username-password-form", "browser-mfa second factor"]
    assert [c["alias"] for c in r["authenticatorConfig"]] == ["mfa-required-no-factor-used"]
    assert r["browserFlow"] == "browser-mfa"
    assert not any(x.get("composite") for x in r["roles"]["realm"])  # no marker needed


def test_a18_t06_off_mode_keeps_the_stock_browser_flow() -> None:
    r = realm("off")
    assert "passwordPolicy" in r
    assert not {"authenticationFlows", "browserFlow", "authenticatorConfig"} & set(r)


def test_a18_t07_every_step_is_a_known_keycloak_authenticator() -> None:
    for mode in ("admins", "all"):
        used = {
            e["authenticator"]
            for steps in executions(realm(mode)).values()
            for e in steps
            if "authenticator" in e
        }
        assert used <= AUTHENTICATORS, used - AUTHENTICATORS


def test_a18_t08_a_bad_mode_is_refused_and_rendering_changes_nothing_else() -> None:
    with pytest.raises(ValueError, match="--mfa"):
        render_realm.render(SOURCE, ORIGIN, "sometimes")
    before = copy.deepcopy(json.loads(SOURCE.replace("${WEB_PORT:3000}", "3000")))
    render_realm.render(SOURCE, ORIGIN, "admins")
    assert before == json.loads(SOURCE.replace("${WEB_PORT:3000}", "3000"))
    assert "localhost" not in render_realm.render(SOURCE, ORIGIN, "admins")


def test_a18_t09_the_cli_takes_the_mode(tmp_path: Path) -> None:
    out = tmp_path / "realm.json"
    assert render_realm.main(["--origin", ORIGIN, "--out", str(out), "--mfa", "all"]) == 0
    assert "browser-mfa admins" not in out.read_text()
    assert render_realm.main(["--origin", ORIGIN, "--out", str(out), "--mfa", "off"]) == 0
    assert "browserFlow" not in out.read_text()


def test_a18_t10_the_blacklist_is_mounted_where_keycloak_reads_it() -> None:
    deploy = (COMPOSE / "compose.deploy.yaml").read_text(encoding="utf-8")
    mount = re.search(
        r"- \./keycloak/password-blacklist\.txt:(/opt/keycloak/data/password-blacklists/\S+):ro",
        deploy,
    )
    assert mount, "compose.deploy.yaml must mount the blacklist the policy names"
    assert mount.group(1).endswith("/" + render_realm.BLACKLIST)


def test_a18_t11_the_blacklist_file_is_usable() -> None:
    entries = (
        (COMPOSE / "keycloak" / "password-blacklist.txt").read_text(encoding="utf-8").split("\n")
    )
    entries = [e for e in entries if e]
    assert len(entries) > 400 and len(set(entries)) == len(entries)
    assert all(
        len(e) >= 12 and e.isascii() and e == e.lower() for e in entries
    )  # length(12) refuses shorter
