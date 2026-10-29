"""E13.5 against the real `sbam-mock-api` container of the Compose stack (TD §15.2, §19.3).

The service is on the internal network only, so the checks run inside it with its own interpreter
(stdlib `urllib`), with the control token from its own environment: what a tool under test lacks.
The same behaviours are covered in-process by `tests/unit/test_e13_5_mock_api.py`; this proves the
built image, its read-only filesystem and its Compose wiring.
"""

import json
import subprocess
from pathlib import Path

import pytest

from .conftest import Compose

pytestmark = pytest.mark.integration

PROBE = Path(__file__).parent / "probes" / "mock_api_probe.py"


def test_e13_5_t01_to_t05_the_container_serves_isolated_stubs_and_guards_its_control(
    compose: Compose,
) -> None:
    result = compose("exec", "-T", "sbam-mock-api", "python", "-c", PROBE.read_text("utf-8"))
    assert result.returncode == 0, result.stderr[-2000:]
    got = json.loads(result.stdout.strip().splitlines()[-1])
    assert got["health"] == 200 and got["put_a"] == 204 and got["put_b"] == 204
    assert got["t01"] == [503, {"n": 503}]  # the first call of the sequence
    assert got["t03"] == "MOCK_UNMATCHED"
    assert got["t05"]["run-integ-aaaa-0001"] == [200, 200, 200]  # T01 already used the 503
    assert got["t05"]["run-integ-bbbb-0002"] == [429, 200, 200]  # its own sequence, no cross-talk
    assert got["journal_a"][0] == ["s1", 503] and got["journal_a"][1] == ["UNMATCHED", 404]
    assert got["no_token"] == 401  # a tool with no token cannot read a journal
    assert got["bad_stub"] == 422  # a forwarding stub is refused by the service itself
    assert got["cleanup"] == [204, 204]


def test_e13_5_the_container_is_hardened_and_publishes_no_port(compose: Compose) -> None:
    cid = compose("ps", "-q", "sbam-mock-api").stdout.strip()
    assert cid
    inspect = subprocess.run(  # noqa: S603 - fixed docker argv, no shell
        ["docker", "inspect", cid],  # noqa: S607 - docker from PATH, like conftest
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(inspect.stdout)[0]
    assert info["HostConfig"]["ReadonlyRootfs"] is True
    assert info["HostConfig"]["CapDrop"] == ["ALL"]
    assert not any(info["NetworkSettings"]["Ports"].values()), "the mock API must not be published"
