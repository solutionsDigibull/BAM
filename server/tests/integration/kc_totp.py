"""A TOTP-capable browser-flow driver for the throwaway-Keycloak tests (A-18): signs in over HTTP,
enrols a TOTP when Keycloak asks for it, and answers a TOTP prompt with a code it computes."""

import hashlib
import hmac
import html
import re
import struct
import time
from typing import Any

import httpx

CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def totp(secret: bytes, at: float | None = None) -> str:
    """RFC 6238, SHA-1, 6 digits, 30 s (Keycloak's defaults)."""
    counter = int((at if at is not None else time.time()) // 30)
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0xF
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


class Browser:
    """One cookie jar, carried by hand (Keycloak marks its cookies Secure even over http)."""

    def __init__(self, base: str, realm: dict[str, Any], client_id: str) -> None:
        self.base = base
        client = next(c for c in realm["clients"] if c["clientId"] == client_id)
        self.redirect = client["redirectUris"][0].replace("*", "callback")
        self.client_id = client_id
        self.http = httpx.Client(timeout=30, follow_redirects=False)
        self.page: httpx.Response | None = None

    def _cookies(self) -> dict[str, str]:
        return {"Cookie": "; ".join(f"{c.name}={c.value or ''}" for c in self.http.cookies.jar)}

    def start(self) -> httpx.Response:
        self.page = self.http.get(
            f"{self.base}/realms/bullm/protocol/openid-connect/auth",
            params={
                "client_id": self.client_id,
                "redirect_uri": self.redirect,
                "response_type": "code",
                "scope": "openid",
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
            },
        )
        return self.page

    def action(self) -> str:
        assert self.page is not None
        found = re.search(r'action="([^"]+)"', self.page.text)
        assert found, "no form on the page"
        return html.unescape(found.group(1))

    def post(self, data: dict[str, str]) -> httpx.Response:
        step = self.http.post(self.action(), data=data, headers=self._cookies())
        for _ in range(8):  # Keycloak's own redirects (a required action, the next step)
            where = step.headers.get("location", "")
            if step.status_code != 302 or where.startswith(self.redirect):
                break
            step = self.http.get(where, headers=self._cookies())
        self.page = step
        return step

    def landed(self) -> bool:
        assert self.page is not None
        return self.page.status_code == 302 and "code=" in self.page.headers.get("location", "")

    def kind(self) -> str:
        """What the page is: code (signed in), setup (enrol TOTP), totp (asked for a code),
        webauthn (asked to use a security key), login, or other."""
        assert self.page is not None
        if self.landed():
            return "code"
        text = self.page.text
        if 'name="totpSecret"' in text:
            return "setup"
        if 'name="otp"' in text:
            return "totp"
        if "webauthn" in text.lower() and "authenticate" in text.lower():
            return "webauthn"
        if 'name="username"' in text:
            return "login"
        return "other"

    def login(self, username: str, password: str) -> str:
        self.start()
        self.post({"username": username, "password": password})
        return self.kind()

    def enrol(self) -> bytes:
        """On the setup page: register the TOTP Keycloak offers; returns its key."""
        assert self.page is not None and self.kind() == "setup"
        secret = re.search(r'name="totpSecret" value="([^"]+)"', self.page.text)
        assert secret
        key = secret.group(1).encode()
        self.post(
            {
                "totp": totp(key),
                "totpSecret": secret.group(1),
                "userLabel": "test",
                "saveTOTPBtn": "",
            }
        )
        return key

    def answer(self, key: bytes, window: int = 1) -> str:
        """Answer a TOTP prompt. Keycloak refuses a code it already accepted (the enrolment's), so a
        test answers with a later window's code (its look-ahead accepts the next one)."""
        assert self.kind() == "totp"
        self.post({"otp": totp(key, time.time() + 30 * window)})
        return self.kind()
