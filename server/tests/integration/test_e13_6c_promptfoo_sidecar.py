"""E13.6c against the real `promptfoo` container of the Compose stack (ADR 0024, #638).

Grading jobs go in through the shared job directory, from inside the container, the way the build
worker submits them; the grading model is the stack's Bifrost alias `sbam-rubric` (the mock upstream
under `LLM_PROVIDER=mock`, which passes a request unless it carries `MOCK_RUBRIC_FAIL`). The same
behaviour is covered without Docker by `test_e13_6_grader.py` and `test_llm_mock_rubric.py`; this
proves the built image, its read-only filesystem, its Compose wiring and the Bifrost route.

A Promptfoo outage (Bifrost down) is not run here: it would take the shared stack's gateway down. It
was checked by hand on 2026-11-02 and is covered at the unit level by the measured result shape
(`test_e13_6c_an_api_call_error_written_as_an_assertion_failure_is_still_infrastructure`).
"""

import json
import subprocess
from pathlib import Path

import pytest

from .conftest import Compose

pytestmark = pytest.mark.integration

PROBE = Path(__file__).parent / "probes" / "rubric_probe.mjs"


@pytest.fixture(scope="module")
def container(compose: Compose) -> str:
    cid = compose("ps", "-q", "promptfoo").stdout.strip()
    if not cid:
        pytest.skip("the promptfoo service is not part of this stack (rebuild with make up)")
    return cid


def test_e13_6c_t07_the_sidecar_gives_verdicts_through_bifrost_and_refuses_other_aliases(
    compose: Compose, container: str
) -> None:
    result = compose(
        "exec", "-T", "promptfoo", "node", "--input-type=module", "-e", PROBE.read_text("utf-8"),
        timeout=300,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr[-2000:]
    got = json.loads(result.stdout.strip().splitlines()[-1])
    assert got["graded"]["error"] is None
    assert got["graded"]["verdicts"] == {"polite": True, "rude": False}
    assert got["refused"]["verdicts"] == {}
    assert got["refused"]["error"] == "refused: the alias is not allowed"


def test_e13_6c_the_container_is_hardened_and_publishes_no_port(container: str) -> None:
    inspect = subprocess.run(  # noqa: S603 - fixed docker argv, no shell
        ["docker", "inspect", container],  # noqa: S607 - docker from PATH, like conftest
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(inspect.stdout)[0]
    assert info["HostConfig"]["ReadonlyRootfs"] is True
    assert info["HostConfig"]["CapDrop"] == ["ALL"]
    assert info["Config"]["User"] == "10001:10001"
    assert not any(info["NetworkSettings"]["Ports"].values()), "the sidecar must not be published"
    assert list(info["NetworkSettings"]["Networks"]) == [
        next(n for n in info["NetworkSettings"]["Networks"] if n.endswith("sbam_internal"))
    ]  # on the internal network only: no route to the internet
