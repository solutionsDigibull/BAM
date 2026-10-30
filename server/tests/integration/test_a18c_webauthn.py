"""A-18c (#542): TOTP and a WebAuthn security key side by side for admins, against a real Keycloak
(the pinned image, a throwaway container) and a real Chromium with a virtual authenticator (a
software security key), so a real WebAuthn registration and sign-in happen. TOTP is driven over
HTTP with a code computed here. Skipped when Docker, the pinned image, Node or Playwright's
Chromium is missing.
"""

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from .kc_totp import Browser, totp
from .test_a18_keycloak_hardening import KEYCLOAK, STRONG, Keycloak, available, rendered, start

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
PACKAGE_JSON = REPO / "client" / "package.json"
SCRIPT = Path(__file__).with_name("kc_webauthn.mjs")


def browser_ready() -> bool:
    if shutil.which("node") is None or not (REPO / "client/node_modules/@playwright/test").is_dir():
        return False
    probe = (
        "const {createRequire}=require('node:module');"
        f"const r=createRequire({json.dumps(str(PACKAGE_JSON))});"
        "const {chromium}=r('@playwright/test');"
        "process.exit(require('fs').existsSync(chromium.executablePath())?0:1)"
    )
    return subprocess.run(["node", "-e", probe], check=False).returncode == 0  # noqa: S603, S607


@pytest.fixture(scope="module")
def kc(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Keycloak]:
    if not available() or not browser_ready():
        pytest.skip("needs Docker, the pinned Keycloak image, Node and Playwright's Chromium")
    yield from start(tmp_path_factory.mktemp("kc"), rendered("admins"))


def key_browser(
    kc: Keycloak,
    user: str,
    *,
    otp: str | None = None,
    use_key: bool = False,
    keys: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One Chromium sign-in with a virtual security key holding `keys`."""
    client = next(c for c in kc.realm["clients"] if c["clientId"] == "sbam-admin")
    args = {
        # WebAuthn needs a real host name for its RP id: `localhost`, not an IP address
        "base": kc.base.replace("127.0.0.1", "localhost"),
        "clientId": "sbam-admin",
        "redirect": client["redirectUris"][0].replace("*", "callback"),
        "username": user,
        "password": STRONG,
        "otp": otp,
        "useKey": use_key,
    }
    env = {**os.environ, "WEBAUTHN_STATE": json.dumps(keys or [])}
    done = subprocess.run(  # noqa: S603
        ["node", str(SCRIPT), str(PACKAGE_JSON), json.dumps(args)],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-1500:]
    result: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    return result


def credentials(kc: Keycloak, uid: str) -> list[str]:
    return [c["type"] for c in kc.admin("GET", f"/users/{uid}/credentials").json()]


def enrolled_totp(kc: Keycloak, name: str) -> tuple[str, bytes]:
    """A new admin who has signed in once and enrolled TOTP, as Keycloak makes them."""
    uid = kc.user(name, "sbam-admin")
    web = Browser(kc.base, kc.realm, "sbam-admin")
    assert web.login(name, STRONG) == "setup"
    return uid, web.enrol()


def with_key(kc: Keycloak, name: str) -> tuple[str, bytes, list[dict[str, Any]]]:
    uid, secret = enrolled_totp(kc, name)
    kc.admin("PUT", f"/users/{uid}", json={"requiredActions": ["webauthn-register"]})
    done = key_browser(kc, name, otp=totp(secret, time.time() + 30))
    assert done["outcome"] == "code" and "register-page" in done["steps"], done
    assert "webauthn" in credentials(kc, uid)
    return uid, secret, done["credentials"]


def test_a18c_t01_an_admin_with_no_factor_is_sent_to_enrol_totp_and_cannot_skip_it(
    kc: Keycloak,
) -> None:
    kc.user("none-yet", "sbam-admin")
    web = Browser(kc.base, kc.realm, "sbam-admin")
    assert web.login("none-yet", STRONG) == "setup"
    web.post({"totp": "000000", "totpSecret": "x", "userLabel": "t", "saveTOTPBtn": ""})
    assert web.kind() != "code"  # a wrong code does not enrol, and does not let them in
    assert web.login("none-yet", STRONG) == "setup"  # nor does starting over


def test_a18c_t02_totp_only_admin_is_asked_for_a_code_and_a_wrong_one_is_refused(
    kc: Keycloak,
) -> None:
    _, secret = enrolled_totp(kc, "totp-only")
    web = Browser(kc.base, kc.realm, "sbam-admin")
    assert web.login("totp-only", STRONG) == "totp"
    web.post({"otp": "000000"})
    assert web.kind() == "totp"
    assert web.answer(secret) == "code"


def test_a18c_t03_a_security_key_is_registered_and_then_used_to_sign_in(kc: Keycloak) -> None:
    _, _, keys = with_key(kc, "key-and-totp")
    signed_in = key_browser(kc, "key-and-totp", use_key=True, keys=keys)
    assert signed_in["outcome"] == "code"
    assert "authenticate-page" in signed_in["steps"]  # the key, not a code


def test_a18c_t04_with_both_the_totp_code_still_works(kc: Keycloak) -> None:
    _, secret, _ = with_key(kc, "both-factors")
    web = Browser(kc.base, kc.realm, "sbam-admin")
    assert web.login("both-factors", STRONG) == "totp"
    assert web.answer(secret, window=-1) == "code"  # a code window nobody has used yet


def test_a18c_t05_a_key_only_admin_is_asked_for_the_key_straight_away(kc: Keycloak) -> None:
    uid, _, keys = with_key(kc, "key-only")
    for credential in kc.admin("GET", f"/users/{uid}/credentials").json():
        if credential["type"] == "otp":
            kc.admin("DELETE", f"/users/{uid}/credentials/{credential['id']}")
    assert credentials(kc, uid).count("otp") == 0
    done = key_browser(kc, "key-only", keys=keys)
    assert done["outcome"] == "code"
    assert "authenticate-page" in done["steps"] and "other-way" not in done["steps"]


def test_a18c_t06_a_different_key_is_not_accepted(kc: Keycloak) -> None:
    with_key(kc, "owner-of-key")
    _, _, someone_elses = with_key(kc, "someone-else")
    refused = key_browser(kc, "owner-of-key", use_key=True, keys=someone_elses)
    assert refused["outcome"] != "code"


def test_a18c_t07_fdes_never_see_a_second_factor(kc: Keycloak) -> None:
    kc.user("plain-fde", "sbam-fde")
    web = Browser(kc.base, kc.realm, "sbam-fde")
    assert web.login("plain-fde", STRONG) == "code"


def test_a18c_t08_the_committed_realm_file_still_signs_in_without_any_of_this() -> None:
    text = (KEYCLOAK / "realm-bullm.json").read_text()
    assert "webauthn" not in text and "browser-mfa" not in text  # dev realm: no second factor
