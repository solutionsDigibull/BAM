"""`make up PROFILES=...` accepts only known profiles (E1.11-T07). Dry run: no Docker needed."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MAKE = shutil.which("make")

pytestmark = pytest.mark.skipif(MAKE is None, reason="GNU make not installed (CI has it)")


def make_n(*args: str) -> subprocess.CompletedProcess[str]:
    assert MAKE
    return subprocess.run(  # noqa: S603 - fixed make argv
        [MAKE, "-n", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )


def test_e1_11_t07_unknown_profile_fails_and_lists_valid_ones() -> None:
    out = make_n("up", "PROFILES=obs,foo")
    assert out.returncode != 0
    assert "unknown PROFILES 'foo'" in out.stderr
    assert "obs analytics sandbox-local" in out.stderr


def test_e1_11_known_profiles_add_their_compose_files() -> None:
    out = make_n("up", "PROFILES=obs,sandbox-local")
    assert out.returncode == 0, out.stderr
    assert "-f compose/compose.obs.yaml -f compose/compose.sandbox-local.yaml" in out.stdout
    assert "compose.analytics.yaml" not in out.stdout


def test_e1_11_no_profiles_is_core_only() -> None:
    out = make_n("ps")
    assert out.returncode == 0, out.stderr
    assert "compose.obs.yaml" not in out.stdout
