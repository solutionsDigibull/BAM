# ruff: noqa: S603, S607  (the test runs docker and curl with fixed arguments, by design)
"""A-08b (#554): the proxy refuses a flooding client address with 429 + Retry-After before anything
is proxied. Runs the real sbam-proxy image (Caddy + caddy-ratelimit) with the real Caddyfile in
front of stub upstreams on a throwaway Docker network. Opt-in like the Caddyfile validation:
SBAM_VALIDATE_PROXY=1 (needs docker; builds the proxy image).
"""

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DOCKER = shutil.which("docker")
PORT = 18443

pytestmark = pytest.mark.skipif(
    os.environ.get("SBAM_VALIDATE_PROXY") != "1" or DOCKER is None,
    reason="set SBAM_VALIDATE_PROXY=1 (needs docker and builds the proxy image)",
)

STUB = """
import http.server, threading
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers()
        self.wfile.write(b"ok")
    do_POST = do_PATCH = do_GET
    def log_message(self, *a): pass
for port in (3000, 1080, 8080):
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
threading.Event().wait()
"""


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    assert DOCKER
    return subprocess.run([DOCKER, *args], capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def proxy() -> Iterator[str]:
    tag, suffix = "sbam-proxy:a08b", uuid.uuid4().hex[:6]
    net, stub, name = f"a08b-{suffix}", f"a08b-stub-{suffix}", f"a08b-proxy-{suffix}"
    built = docker(
        "build", "-q", "-f", str(REPO / "compose/docker/proxy.Dockerfile"), "-t", tag, str(REPO)
    )
    assert built.returncode == 0, built.stderr
    docker("network", "create", net)
    try:
        docker(
            "run", "-d", "--name", stub, "--network", net, "--network-alias", "sbam-web",
            "--network-alias", "tusd", "--network-alias", "keycloak",
            "python:3.12-slim", "python", "-c", STUB,
        )  # fmt: skip
        started = docker(
            "run", "-d", "--name", name, "--network", net, "-p", f"127.0.0.1:{PORT}:443",
            "-e", "SBAM_PORTAL_HOST=portal.test", "-e", "SBAM_AUTH_HOST=auth.test",
            "-e", "SBAM_ACME_EMAIL=ops@x.test", "-e", "SBAM_TLS=internal",
            "-e", "SBAM_RATE_AUTH_HOST_EVENTS=5",
            "-v", f"{REPO / 'compose/proxy/Caddyfile'}:/etc/caddy/Caddyfile:ro", tag,
        )  # fmt: skip
        assert started.returncode == 0, started.stderr
        for _ in range(60):  # ready when the proxy answers 200: TLS up and its upstream healthy
            if get("portal.test", "/projects") == "200":  # not auth.test: its budget is a test
                break
            time.sleep(1)
        else:
            pytest.fail("the proxy never became ready:\n" + docker("logs", name).stdout[-1500:])
        yield name
    finally:
        docker("rm", "-f", name, stub)
        docker("network", "rm", net)


def get(host: str, path: str, *headers: str) -> str:
    cmd = [
        "curl",
        "-sk",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--resolve",
        f"{host}:{PORT}:127.0.0.1",
    ]
    for header in headers:
        cmd += ["-H", header]
    done = subprocess.run(
        [*cmd, f"https://{host}:{PORT}{path}"], capture_output=True, text=True, check=False
    )
    return done.stdout


def burst(host: str, path: str, count: int, *headers: str) -> list[str]:
    return [get(host, path, *headers) for _ in range(count)]


def test_a08b_t01_sign_in_paths_allow_30_a_minute_then_429(proxy: str) -> None:
    codes = burst("portal.test", "/api/auth/session", 35)
    assert codes[:30] == ["200"] * 30 and codes[30:] == ["429"] * 5


def test_a08b_t02_a_spoofed_forwarded_for_does_not_dodge_the_limit(proxy: str) -> None:
    codes = [
        get("portal.test", "/api/auth/session", f"X-Forwarded-For: 9.9.9.{n}") for n in range(3)
    ]
    assert codes == ["429"] * 3  # the address is the TCP peer: no trusted proxy is configured


def test_a08b_t03_other_paths_are_not_caught_by_the_sign_in_zone(proxy: str) -> None:
    assert burst("portal.test", "/projects", 40) == ["200"] * 40


def test_a08b_t04_the_keycloak_host_has_its_own_limit_from_the_environment(proxy: str) -> None:
    codes = burst("auth.test", "/realms/bullm/protocol/openid-connect/auth", 8)
    assert codes == ["200"] * 5 + ["429"] * 3


def test_a08b_t05_a_refusal_carries_retry_after(proxy: str) -> None:
    done = subprocess.run(
        ["curl", "-sk", "-D", "-", "-o", "/dev/null", "--resolve", f"portal.test:{PORT}:127.0.0.1",
         f"https://portal.test:{PORT}/api/auth/session"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert " 429" in done.stdout.splitlines()[0]
    assert any(line.lower().startswith("retry-after:") for line in done.stdout.splitlines())


def test_a08b_t06_the_keycloak_admin_console_is_still_404_not_429(proxy: str) -> None:
    assert get("portal.test", "/admin/master/console/") == "404"


def test_a15_a_signed_url_is_not_in_the_proxy_log(proxy: str) -> None:
    secret = "SIGNED" + uuid.uuid4().hex  # what a download URL's `sig` would hold
    get("portal.test", f"/api/v1/files/some/key?exp=1999999999&sig={secret}")
    logs = docker("logs", proxy)
    assert secret not in logs.stdout + logs.stderr
