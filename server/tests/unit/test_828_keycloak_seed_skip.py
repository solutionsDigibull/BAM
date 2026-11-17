"""#828: the dev Keycloak seed is skipped when it already ran with the same script and inputs."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "compose/keycloak/seed.sh"
needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("sha256sum") is None,
    reason="needs bash and sha256sum",
)
INPUTS = {"SEED_USER_PASSWORD": "pw", "KEYCLOAK_ADMIN_CLIENT_SECRET": "sec", "API_PORT": "8000"}


def fingerprint(**over: str) -> str:
    env = {**INPUTS, **over}
    values = (env["SEED_USER_PASSWORD"], env["KEYCLOAK_ADMIN_CLIENT_SECRET"], env["API_PORT"])
    tail = "".join(f"{value}\n" for value in values)  # what seed.sh pipes in with printf
    return hashlib.sha256(SEED.read_bytes() + tail.encode()).hexdigest()


def fake_kcadm(tmp_path: Path, stored: str | None) -> tuple[Path, Path]:
    """A kcadm that logs its calls and answers `get realms/bullm` with the stored fingerprint."""
    log = tmp_path / "calls.log"
    attrs = f'{{"attributes": {{"sbamSeedFingerprint" : "{stored}"}}}}' if stored else "{}"
    script = tmp_path / "kcadm.sh"
    script.write_text(
        f"#!/bin/bash\necho \"$*\" >> '{log}'\n"
        f'if [ "$1" = get ] && [ "$2" = realms/bullm ]; then echo \'{attrs}\'; fi\n',
        "utf-8",
    )
    script.chmod(0o755)
    return script, log


def run_seed(tmp_path: Path, stored: str | None, **env: str) -> tuple[str, list[str]]:
    kcadm, log = fake_kcadm(tmp_path, stored)
    full_env = {**INPUTS, "KEYCLOAK_ADMIN_PASSWORD": "admin", "KCADM": str(kcadm)}
    full_env.update(env)
    full_env["PATH"] = "/usr/bin:/bin"
    done = subprocess.run(  # noqa: S603 - fixed argv
        ["bash", str(SEED)],  # noqa: S607
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout, log.read_text("utf-8").splitlines() if log.exists() else []


@needs_bash
def test_828_the_same_script_and_inputs_skip_the_whole_seed(tmp_path: Path) -> None:
    out, calls = run_seed(tmp_path, stored=fingerprint())
    assert "seed up to date" in out
    assert len(calls) == 2  # the login and the one lookup, nothing else


@needs_bash
@pytest.mark.parametrize(
    "changed",
    [{"SEED_USER_PASSWORD": "other"}, {"KEYCLOAK_ADMIN_CLIENT_SECRET": "x"}, {"API_PORT": "9000"}],
)
def test_828_a_changed_input_runs_the_full_seed(tmp_path: Path, changed: dict[str, str]) -> None:
    out, calls = run_seed(tmp_path, stored=fingerprint(), **changed)
    assert "seed up to date" not in out
    assert len(calls) > 2


@needs_bash
def test_828_a_fresh_realm_runs_the_full_seed(tmp_path: Path) -> None:
    out, calls = run_seed(tmp_path, stored=None)  # no fingerprint stored yet
    assert "seed up to date" not in out
    assert len(calls) > 2


@needs_bash
def test_828_seed_force_runs_it_anyway(tmp_path: Path) -> None:
    out, calls = run_seed(tmp_path, stored=fingerprint(), SEED_FORCE="1")
    assert "seed up to date" not in out
    assert len(calls) > 2
