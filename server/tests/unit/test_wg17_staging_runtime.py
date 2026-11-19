"""WG-17 (#765): the egress allowlist and proxy (T03), and the Docker deployer's command line (T05).

No Docker daemon is needed: `subprocess.run` is replaced and records what the deployer asked for.
The proxy test runs a real proxy and a real upstream on loopback.
"""

import asyncio
import base64
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sbam_api.staging.egress import EgressPolicy, normalise
from sbam_workers.staging.deployer import (
    DeployError,
    DeploySpec,
    DisabledDeployer,
    DockerCliDeployer,
    make_deployer,
)
from sbam_workers.staging.egress_proxy import EgressProxy, PolicyDir
from sbam_workers.staging.runtime import container_name

DIGEST = "sha256:" + "a" * 64
SECRET = "fixture_only_not_a_real_1716"  # noqa: S105 - a test value


# --- the policy -------------------------------------------------------------------------------


def test_wg17_t03_an_undeclared_host_is_blocked_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    policy = EgressPolicy(["api.example.com", "*.cdn.example.com"])
    assert policy.check("API.Example.com.:443", deployment="stg-1").allowed
    assert policy.check("a.b.cdn.example.com", deployment="stg-1").allowed
    for host in (
        "cdn.example.com",
        "evil.com",
        "api.example.com.evil.com",
        "10.0.0.1",
        "[::1]",
        "",
    ):
        assert not policy.check(host, deployment="stg-1").allowed
    assert "host=evil.com" in caplog.text and "deployment=stg-1" in caplog.text


def test_wg17_an_empty_allowlist_allows_nothing() -> None:
    assert not EgressPolicy([]).decide("example.com").allowed
    assert normalise("Example.COM.") == "example.com"


# --- the proxy ------------------------------------------------------------------------------------


async def _roundtrip(proxy_port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(request)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(4096), 5)
    writer.close()
    return data


def _auth(token: str) -> str:
    return "Proxy-Authorization: Basic " + base64.b64encode(f"{token}:".encode()).decode()


def test_wg17_t03_the_proxy_passes_a_declared_host_and_blocks_the_rest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    policies = PolicyDir(tmp_path)
    token = uuid.uuid4().hex
    policies.write(token, "stg-1", ["localhost"])

    async def scenario() -> tuple[bytes, bytes, bytes, bytes]:
        async def upstream(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            w.write(b"hello from upstream")
            await w.drain()
            w.close()

        up = await asyncio.start_server(upstream, "127.0.0.1", 0)
        up_port = up.sockets[0].getsockname()[1]
        proxy = EgressProxy(policies, allowed_ports=[up_port])
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with up, server:
            ok = await _roundtrip(
                port, f"CONNECT localhost:{up_port} HTTP/1.1\r\n{_auth(token)}\r\n\r\n".encode()
            )
            blocked = await _roundtrip(
                port, f"CONNECT evil.example.com:443 HTTP/1.1\r\n{_auth(token)}\r\n\r\n".encode()
            )
            wrong_port = await _roundtrip(
                port, f"CONNECT localhost:25 HTTP/1.1\r\n{_auth(token)}\r\n\r\n".encode()
            )
            stranger = await _roundtrip(
                port,
                f"CONNECT localhost:{up_port} HTTP/1.1\r\n{_auth('f' * 32)}\r\n\r\n".encode(),
            )
        return ok, blocked, wrong_port, stranger

    caplog.set_level(logging.INFO)
    ok, blocked, wrong_port, stranger = asyncio.run(scenario())
    assert ok.startswith(b"HTTP/1.1 200")
    assert blocked.startswith(b"HTTP/1.1 403") and wrong_port.startswith(b"HTTP/1.1 403")
    assert stranger.startswith(b"HTTP/1.1 403")  # an unknown token has no policy
    assert "host=evil.example.com" in caplog.text and "deployment=stg-1" in caplog.text


def test_wg17_a_policy_token_must_be_a_token(tmp_path: Path) -> None:
    policies = PolicyDir(tmp_path)
    with pytest.raises(ValueError):
        policies.write("../../etc/passwd", "x", [])
    assert policies.read("../../x") is None and policies.read("a" * 32) is None


# --- the Docker deployer -------------------------------------------------------------------------


class FakeDocker:
    def __init__(self, image_id: str | None = DIGEST) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.image_id = image_id

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((argv, kwargs["env"]))
        sub = argv[1]
        if sub == "image":
            if self.image_id is None:
                return subprocess.CompletedProcess(argv, 1, b"", b"Error: No such image")
            return subprocess.CompletedProcess(argv, 0, self.image_id.encode(), b"")
        if sub == "network" and argv[2] == "inspect":
            return subprocess.CompletedProcess(argv, 1, b"", b"No such network")
        return subprocess.CompletedProcess(argv, 0, b"abc123\n", b"")


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def spec(**over: Any) -> DeploySpec:
    base: dict[str, Any] = {
        "name": "stg-0190f3a2-wf-ledger-v1-0-1-stg",
        "image_digest": DIGEST,
        "env": {"LEDGER_API_KEY": SecretStr(SECRET)},
        "plain_env": {"HTTPS_PROXY": "http://t@egress:3128"},
    }
    return DeploySpec(**{**base, **over})


def test_wg17_t05_the_secret_is_in_the_clients_environment_and_in_no_argument(
    docker: FakeDocker,
) -> None:
    done = DockerCliDeployer().start(spec())
    assert done.endpoint == "http://stg-0190f3a2-wf-ledger-v1-0-1-stg:8000"
    verbs = [argv[1] for argv, _ in docker.calls]
    assert "build" not in verbs and "pull" not in verbs  # the image is never rebuilt or fetched
    for argv, _ in docker.calls:
        assert SECRET not in " ".join(argv)
    run_argv, run_env = next((a, e) for a, e in docker.calls if a[1] == "run")
    assert run_env["LEDGER_API_KEY"] == SECRET
    assert "LEDGER_API_KEY" in run_argv and run_argv[-1] == DIGEST  # started by digest
    assert run_argv[run_argv.index("--network") + 1] == "sbam-staging"
    assert "--cap-drop" in run_argv and "no-new-privileges" in run_argv
    assert any(a[:3] == ["/usr/bin/docker", "network", "create"] and "--internal" in a
               for a, _ in docker.calls)  # fmt: skip
    other_calls = [e for a, e in docker.calls if a[1] != "run"]
    assert all("LEDGER_API_KEY" not in e for e in other_calls)


def test_wg17_a_secret_cannot_shadow_the_clients_own_environment(docker: FakeDocker) -> None:
    for name in ("PATH", "DOCKER_HOST", "HTTPS_PROXY", "bad-name", "1BAD"):
        with pytest.raises(DeployError) as caught:
            DockerCliDeployer().start(spec(env={name: SecretStr("x" * 20)}))
        assert caught.value.code == "ENV_NAME_REFUSED"
    assert docker.calls == []  # nothing ran


def test_wg17_an_image_the_host_does_not_hold_is_missing_not_rebuilt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(image_id=None)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(DeployError) as caught:
        DockerCliDeployer().start(spec())
    assert caught.value.code == "IMAGE_MISSING"
    assert [argv[1] for argv, _ in fake.calls] == ["image"]


def test_wg17_a_different_image_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker(image_id="sha256:" + "b" * 64)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(DeployError) as caught:
        DockerCliDeployer().start(spec())
    assert caught.value.code == "IMAGE_MISSING"


def test_wg17_bad_names_and_digests_never_reach_docker(docker: FakeDocker) -> None:
    for bad in (spec(name="Bad Name; rm -rf /"), spec(image_digest="latest"), spec(network="a b")):
        with pytest.raises(DeployError):
            DockerCliDeployer().start(bad)
    assert docker.calls == []


def test_wg17_stop_keeps_the_container_and_tolerates_a_missing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, b"", b"Error: No such container: x")

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", run)
    DockerCliDeployer().stop("stg-x")
    assert calls == [["/usr/bin/docker", "stop", "stg-x"]]  # stop, never `rm`: kept on record


def test_wg17_no_docker_client_is_retryable_and_the_default_runs_nothing() -> None:
    assert isinstance(make_deployer("none", 120), DisabledDeployer)
    assert isinstance(make_deployer("docker", 120), DockerCliDeployer)
    with pytest.raises(DeployError) as caught:
        DisabledDeployer().start(spec())
    assert caught.value.code == "DEPLOYER_DISABLED"


def test_wg17_container_names_differ_per_workflow_and_fit_docker() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    long = "wf_" + "x" * 60 + "_v1_0_1_stg"
    assert container_name(a, "wf_ledger_v1_0_1_stg") != container_name(b, "wf_ledger_v1_0_1_stg")
    assert len(container_name(a, long)) <= 63 and "_" not in container_name(a, long)
