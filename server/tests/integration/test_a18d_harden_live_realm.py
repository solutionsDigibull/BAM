"""A-18d (#543): `harden_realm.py` brings an already-imported realm to the state `render_realm.py`
would have imported, against a real Keycloak (the pinned image, a throwaway container, never the
stack). The scenario runs in order on one realm: refuse, dry run, apply, no-op, switch mode, roll
back. Skipped when Docker or the pinned image is not already local."""

import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from .test_a18_keycloak_hardening import (
    ADMIN_PASSWORD,
    KEYCLOAK,
    STRONG,
    Keycloak,
    available,
    rendered,
    start,
)

pytestmark = pytest.mark.integration

spec = importlib.util.spec_from_file_location("harden_realm", KEYCLOAK / "harden_realm.py")
assert spec and spec.loader
harden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harden)


def dev_realm() -> dict[str, Any]:
    """The committed realm as a laptop imports it: no policy, no flows, no marker role."""
    text = (KEYCLOAK / "realm-bullm.json").read_text().replace("${WEB_PORT:3000}", "3000")
    realm: dict[str, Any] = json.loads(text.replace("http://localhost:3000", "https://x.example"))
    realm["sslRequired"] = "none"
    return realm


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Keycloak]:
    if not available():
        pytest.skip("Docker or the pinned Keycloak image is not available locally")
    yield from start(tmp_path_factory.mktemp("kc"), dev_realm())


@pytest.fixture(autouse=True)
def admin_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", ADMIN_PASSWORD)


def run(kc: Keycloak, *args: str, mounted: bool = True) -> int:
    flags = ["--blacklist-mounted"] if mounted else []
    return int(harden.main(["--url", kc.base, *flags, *args]))


def structure(kc: Keycloak, alias: str = "browser-mfa") -> list[tuple[Any, ...]]:
    """(level, what the step is, requirement) for every step of a flow, nested ones included."""
    steps = kc.admin("GET", f"/authentication/flows/{alias}/executions").json()
    return [(s["level"], s.get("providerId") or s["displayName"], s["requirement"]) for s in steps]


def state(kc: Keycloak) -> dict[str, Any]:
    realm = kc.admin("GET", "").json()
    return {"policy": realm.get("passwordPolicy") or "", "browser": realm["browserFlow"]}


def test_a18d_t01_before_the_realm_is_the_dev_realm(live: Keycloak) -> None:
    assert state(live) == {"policy": "", "browser": "browser"}
    live.user("admin-one", "sbam-admin", password="short-seed")  # noqa: S106 - no policy yet
    assert live.sign_in("sbam-admin", "admin-one", "short-seed") == "code"


def test_a18d_t02_it_refuses_without_the_blacklist_confirmation_or_a_password(
    live: Keycloak, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(live, mounted=False) == 2
    assert "common-passwords.txt" in capsys.readouterr().err
    monkeypatch.delenv("KEYCLOAK_ADMIN_PASSWORD")
    assert run(live) == 2
    assert state(live) == {"policy": "", "browser": "browser"}  # nothing was touched


def test_a18d_t03_a_dry_run_says_what_it_would_do_and_changes_nothing(
    live: Keycloak, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(live, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "would set passwordPolicy" in out and "would build flow 'browser-mfa'" in out
    assert state(live) == {"policy": "", "browser": "browser"}
    assert live.sign_in("sbam-admin", "admin-one", "short-seed") == "code"


def test_a18d_t04_apply_makes_admins_enrol_totp_and_enforces_the_policy(live: Keycloak) -> None:
    assert run(live) == 0
    assert state(live) == {"policy": harden.render_realm.PASSWORD_POLICY, "browser": "browser-mfa"}
    live.user("fde-one", "sbam-fde")
    live.user("root-one", "super-admin")
    assert live.sign_in("sbam-admin", "admin-one", "short-seed") == "otp"
    assert live.sign_in("sbam-admin", "root-one", STRONG) == "otp"
    assert live.sign_in("sbam-fde", "fde-one", STRONG) == "code"
    uid = live.user("pw-check-user-1", "sbam-fde")
    assert live.password(uid, "short1!").status_code == 400
    assert live.password(uid, STRONG + "-b").status_code == 204


def test_a18d_t05_a_second_run_changes_nothing(
    live: Keycloak, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (state(live), structure(live))
    capsys.readouterr()
    assert run(live) == 0
    assert "nothing to change" in capsys.readouterr().out
    assert (state(live), structure(live)) == before


def test_a18d_t06_the_marker_role_reaches_admins_only(live: Keycloak) -> None:
    mine = live.admin("GET", "/users?username=admin-one&exact=true").json()[0]["id"]
    theirs = live.admin("GET", "/users?username=fde-one&exact=true").json()[0]["id"]
    assert "mfa-required" in {
        r["name"] for r in live.admin("GET", f"/users/{mine}/role-mappings/realm/composite").json()
    }
    assert "mfa-required" not in {
        r["name"]
        for r in live.admin("GET", f"/users/{theirs}/role-mappings/realm/composite").json()
    }


def composites(kc: Keycloak, role: str) -> set[str]:
    return {c["name"] for c in kc.admin("GET", f"/roles/{role}/composites").json()}


def test_a18d_t07_it_ends_in_the_state_an_imported_rendered_realm_has(
    live: Keycloak, tmp_path: Path
) -> None:
    """No second copy to drift: the live realm and one imported from `render_realm` match."""
    for imported in start(tmp_path, rendered("admins")):
        assert structure(live) == structure(imported)
        assert state(live) == state(imported)
        for sub in ("browser-mfa forms", "browser-mfa admins"):
            assert structure(live, sub) == structure(imported, sub)
        for name in ("sbam-admin", "super-admin"):
            assert composites(live, name) == composites(imported, name)


def test_a18d_t08_switching_to_everyone_rebuilds_the_flow_and_drops_the_marker(
    live: Keycloak, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(live, "--mfa", "all") == 0
    assert live.sign_in("sbam-fde", "fde-one", STRONG) == "otp"
    names = {f["alias"] for f in live.admin("GET", "/authentication/flows").json()}
    assert "browser-mfa admins" not in names and "browser-mfa" in names
    capsys.readouterr()
    assert run(live, "--mfa", "all") == 0 and "nothing to change" in capsys.readouterr().out


def test_a18d_t09_and_back_to_admins_only(live: Keycloak) -> None:
    assert run(live) == 0
    assert live.sign_in("sbam-fde", "fde-one", STRONG) == "code"
    assert live.sign_in("sbam-admin", "root-one", STRONG) == "otp"


def test_a18d_t10_rollback_restores_the_stock_sign_in_and_is_repeatable(
    live: Keycloak, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(live, "--rollback", mounted=False) == 0  # undoing needs no blacklist
    assert state(live) == {"policy": "", "browser": "browser"}
    names = {f["alias"] for f in live.admin("GET", "/authentication/flows").json()}
    assert not {n for n in names if n.startswith("browser-mfa")}
    assert live.admin("GET", "/roles/mfa-required").status_code == 404
    assert live.sign_in("sbam-admin", "root-one", STRONG) == "code"
    capsys.readouterr()
    assert run(live, "--rollback", mounted=False) == 0
    assert "nothing to change" in capsys.readouterr().out
