"""A-18 (#438): the rendered realm really hardens sign-in, against a real Keycloak (not the stack).

Each group of cases starts its own throwaway Keycloak (the pinned image, H2, a random host port):
never the Compose stack. They skip when Docker or the pinned image is not already there, so nothing
is pulled. Plain http reaches it through the Docker port, so the test copy of the realm sets
`sslRequired: none`; everything else is exactly what `render_realm.py` writes.
"""

import html
import importlib.util
import json
import re
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]
KEYCLOAK = REPO / "compose" / "keycloak"
_IMAGE = re.search(
    r"quay\.io/keycloak/keycloak:[^\s\"']+", (REPO / "compose" / "compose.yaml").read_text()
)
assert _IMAGE
IMAGE = _IMAGE.group(0)  # the pinned image the stack runs
ADMIN_PASSWORD = "admin-pass-" + secrets.token_hex(4)
STRONG = "correct-horse-battery-staple-9"

spec = importlib.util.spec_from_file_location("render_realm", KEYCLOAK / "render_realm.py")
assert spec and spec.loader
render_realm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_realm)

pytestmark = pytest.mark.integration


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )


def available() -> bool:
    if shutil.which("docker") is None:
        return False
    return docker("image", "inspect", IMAGE).returncode == 0


class Keycloak:
    def __init__(self, base: str, realm: dict[str, Any]) -> None:
        self.base, self.realm = base, realm
        self.http = httpx.Client(timeout=30)
        token = self.http.post(
            f"{base}/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": "admin",
                "password": ADMIN_PASSWORD,
            },
        ).json()["access_token"]
        self.http.headers["Authorization"] = f"Bearer {token}"

    def admin(self, method: str, path: str, **kw: Any) -> httpx.Response:
        return self.http.request(method, f"{self.base}/admin/realms/bullm{path}", **kw)

    def user(self, name: str, role: str, password: str = STRONG) -> str:
        made = self.admin(
            "POST",
            "/users",
            json={
                "username": name,
                "email": f"{name}@sbam.test",
                "emailVerified": True,
                "enabled": True,
                "firstName": name,
                "lastName": "Test",
            },
        )
        assert made.status_code == 201, made.text
        uid = made.headers["Location"].rsplit("/", 1)[1]
        assert self.password(uid, password).status_code == 204
        role_rep = self.admin("GET", f"/roles/{role}").json()
        done = self.admin("POST", f"/users/{uid}/role-mappings/realm", json=[role_rep])
        assert done.status_code == 204, done.text
        return uid

    def password(self, uid: str, value: str) -> httpx.Response:
        body = {"type": "password", "value": value, "temporary": False}
        return self.admin("PUT", f"/users/{uid}/reset-password", json=body)

    def sign_in(self, client_id: str, username: str, password: str) -> str:
        """Drive the browser flow with plain HTTP. Returns "code" (signed in), "otp" (asked to
        set up or enter a second factor) or "denied"."""
        client = next(c for c in self.realm["clients"] if c["clientId"] == client_id)
        redirect = client["redirectUris"][0].replace("*", "callback")
        http = httpx.Client(timeout=30, follow_redirects=False)
        page = http.get(
            f"{self.base}/realms/bullm/protocol/openid-connect/auth",
            params={
                "client_id": client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": "openid",
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
            },
        )
        assert page.status_code == 200, page.text[:300]
        action = html.unescape(re.search(r'action="([^"]+)"', page.text).group(1))  # type: ignore[union-attr]

        # Keycloak marks its cookies Secure even in dev mode; httpx will not send those over http,
        # so the cookies are carried by hand (the jar keeps what each answer sets).
        def jar() -> dict[str, str]:
            return {c.name: c.value or "" for c in http.cookies.jar}

        def headers() -> dict[str, str]:
            return {"Cookie": "; ".join(f"{k}={v}" for k, v in jar().items())}

        step = http.post(
            action, data={"username": username, "password": password}, headers=headers()
        )
        for _ in range(6):  # follow Keycloak's own redirects (a required action, the next step)
            where = step.headers.get("location", "")
            if step.status_code == 302 and where.startswith(redirect):
                return "code" if "code=" in where else "denied"
            if step.status_code != 302:
                break
            step = http.get(where, headers=headers())
        text = re.sub(r"<script.*?</script>", " ", step.text, flags=re.S).lower()
        if step.status_code == 200 and re.search(r"totp|authenticator|one-time|otp", text):
            return "otp"
        return "denied"


def start(tmp: Path, realm: dict[str, Any], blacklist: bool = True) -> Iterator[Keycloak]:
    name = "sbam-a18-" + secrets.token_hex(3)
    path = tmp / f"{name}.json"
    path.write_text(json.dumps(realm))
    args = [
        "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::8080",
        "-e", "KC_BOOTSTRAP_ADMIN_USERNAME=admin",
        "-e", f"KC_BOOTSTRAP_ADMIN_PASSWORD={ADMIN_PASSWORD}",
        "-e", "SEED_USER_PASSWORD=unused-seed-pass-1",
        "-v", f"{path}:/opt/keycloak/data/import/realm-bullm.json:ro",
    ]  # fmt: skip
    if blacklist:
        mount = f"{KEYCLOAK / 'password-blacklist.txt'}:/opt/keycloak/data/password-blacklists"
        args += ["-v", mount + "/common-passwords.txt:ro"]
    started = docker(*args, IMAGE, "start-dev", "--import-realm")
    assert started.returncode == 0, started.stderr
    try:
        port = docker("port", name, "8080/tcp").stdout.strip().rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                if httpx.get(f"{base}/realms/bullm", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(3)
        else:
            pytest.fail("Keycloak did not start:\n" + docker("logs", name).stdout[-1500:])
        yield Keycloak(base, realm)
    finally:
        docker("rm", "-f", name)


def rendered(mfa: str) -> dict[str, Any]:
    text = (KEYCLOAK / "realm-bullm.json").read_text()
    realm: dict[str, Any] = json.loads(render_realm.render(text, "https://portal.example.com", mfa))
    realm["sslRequired"] = "none"
    return realm


@pytest.fixture(scope="module")
def admins(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Keycloak]:
    if not available():
        pytest.skip("Docker or the pinned Keycloak image is not available locally")
    yield from start(tmp_path_factory.mktemp("kc"), rendered("admins"))


def test_a18_t01_password_policy_is_enforced(admins: Keycloak) -> None:
    uid = admins.user(
        "password-user-1", "sbam-fde"
    )  # 15 characters: only notUsername can refuse it
    blacklisted = (KEYCLOAK / "password-blacklist.txt").read_text().split()[0]
    assert len(blacklisted) >= 12
    for bad in (
        "short1!",
        "elevenchars",
        blacklisted,
        "password-user-1",
        "password-user-1@sbam.test",
    ):
        assert admins.password(uid, bad).status_code == 400, bad
    assert admins.password(uid, STRONG + "-b").status_code == 204
    assert admins.password(uid, STRONG + "-b").status_code == 400  # one of the last 5


def test_a18_t02_admins_must_set_up_a_second_factor_fdes_need_not(admins: Keycloak) -> None:
    admins.user("tenant-admin", "sbam-admin")
    admins.user("operator", "super-admin")
    admins.user("engineer", "sbam-fde")
    assert admins.sign_in("sbam-admin", "tenant-admin", STRONG) == "otp"
    assert admins.sign_in("sbam-admin", "operator", STRONG) == "otp"
    assert admins.sign_in("sbam-fde", "engineer", STRONG) == "code"


def test_a18_t03_a_wrong_password_never_reaches_the_second_factor(admins: Keycloak) -> None:
    admins.user("careful-admin", "sbam-admin")
    assert admins.sign_in("sbam-admin", "careful-admin", "wrong-password-1") == "denied"


def test_a18_t04_the_marker_role_reaches_admins_only(admins: Keycloak) -> None:
    uid = admins.user("marker-admin", "sbam-admin")
    other = admins.user("marker-fde", "sbam-fde")
    mine = admins.admin("GET", f"/users/{uid}/role-mappings/realm/composite").json()
    theirs = admins.admin("GET", f"/users/{other}/role-mappings/realm/composite").json()
    assert "mfa-required" in {r["name"] for r in mine}
    assert "mfa-required" not in {r["name"] for r in theirs}


def test_a18_t05_failed_logins_and_role_changes_are_recorded(admins: Keycloak) -> None:
    uid = admins.user("watched", "sbam-fde")
    admins.sign_in("sbam-fde", "watched", "wrong-password-2")
    events = admins.admin("GET", "/events", params={"type": "LOGIN_ERROR", "user": uid}).json()
    assert events and events[0]["type"] == "LOGIN_ERROR"
    role = admins.admin("GET", "/roles/sbam-admin").json()
    admins.admin("POST", f"/users/{uid}/role-mappings/realm", json=[role])
    changes = admins.admin(
        "GET",
        "/admin-events",
        params={"resourceTypes": "REALM_ROLE_MAPPING", "resourcePath": f"users/{uid}/*"},
    ).json()
    assert changes and "sbam-admin" in (changes[0].get("representation") or "")


def test_a18_t06_mfa_all_asks_fdes_too(tmp_path: Path) -> None:
    if not available():
        pytest.skip("Docker or the pinned Keycloak image is not available locally")
    for kc in start(tmp_path, rendered("all")):
        kc.user("everyone", "sbam-fde")
        assert kc.sign_in("sbam-fde", "everyone", STRONG) == "otp"


def test_a18_t07_the_dev_realm_keeps_seed_users_working(tmp_path: Path) -> None:
    """The committed realm has no policy and no MFA flow: a short seed password signs in plainly."""
    if not available():
        pytest.skip("Docker or the pinned Keycloak image is not available locally")
    text = (KEYCLOAK / "realm-bullm.json").read_text().replace("${WEB_PORT:3000}", "3000")
    realm = json.loads(text.replace("http://localhost:3000", "https://portal.example.com"))
    realm["sslRequired"] = "none"
    for kc in start(tmp_path, realm, blacklist=False):
        uid = kc.user("seed-admin", "sbam-admin", password="seed-pass")  # noqa: S106
        assert uid and kc.sign_in("sbam-admin", "seed-admin", "seed-pass") == "code"
