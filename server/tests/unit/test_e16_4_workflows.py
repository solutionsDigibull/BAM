"""E16.4: the deploy workflows (shape) and scripts/ci-deploy.sh against fake docker/ssh/scp/curl."""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
BASH = shutil.which("bash")


def workflow(name: str) -> dict[str, Any]:
    data: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads the `on:` key as True
    return data


def test_e16_4_t01_main_goes_to_dev_and_tags_to_staging_then_production() -> None:
    deploy = workflow("deploy.yml")
    assert deploy["on"]["workflow_run"] == {
        "workflows": ["ci"],
        "types": ["completed"],
        "branches": ["main"],
    }
    assert deploy["on"]["push"] == {"tags": ["v*"]}
    jobs = deploy["jobs"]
    assert "conclusion == 'success'" in jobs["release"]["if"]
    assert jobs["dev"]["if"] == "github.event_name == 'workflow_run'"
    assert jobs["staging"]["if"] == "github.event_name == 'push'"
    assert jobs["production"]["needs"] == ["release", "staging"]  # prod only after staging passed
    called = {name: job["with"]["environment"] for name, job in jobs.items() if "uses" in job}
    assert called == {"dev": "dev", "staging": "staging", "production": "production"}
    assert deploy["concurrency"]["cancel-in-progress"] is False


def test_e16_4_t02_every_deploy_job_runs_in_its_github_environment() -> None:
    job = workflow("deploy-env.yml")["jobs"]["deploy"]
    assert job["environment"] == "${{ inputs.environment }}"  # `production` has required reviewers
    step = job["steps"][-1]
    assert step["run"].startswith("bash scripts/ci-deploy.sh")
    assert step["env"]["DEPLOY_SSH_KEY"] == "${{ secrets.DEPLOY_SSH_KEY }}"  # never in the repo


def test_e16_4_a_tag_promotes_the_digests_built_on_main() -> None:
    script = workflow("deploy.yml")["jobs"]["release"]["steps"][0]["run"]
    assert "--branch main" in script and "--status success" in script and "--commit" in script
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in script  # semver tags only
    assert "gh run download" in script and "--name release" in script


@pytest.mark.parametrize("name", ["deploy.yml", "deploy-env.yml", "ci.yml"])
def test_e16_4_actions_are_pinned_by_commit(name: str) -> None:
    uses = re.findall(r"uses: (\S+)", (WORKFLOWS / name).read_text(encoding="utf-8"))
    assert uses
    for ref in uses:
        assert ref.startswith("./") or re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_e16_4_ci_writes_the_release_manifest_for_every_deployed_image() -> None:
    ci = workflow("ci.yml")
    names = [m["name"] for m in ci["jobs"]["image-scan"]["strategy"]["matrix"]["include"]]
    assert "sbam-llm-mock" in names
    job = ci["jobs"]["release-manifest"]
    assert job["needs"] == ["image-scan"]
    # runs on every build (#575): a real release only on a push to main or a v* tag, else a dry run
    assert "if" not in job
    publish = job["env"]["PUBLISH"]
    assert "push" in publish and "refs/heads/main" in publish and "refs/tags/v" in publish
    script = next(s["run"] for s in job["steps"] if s.get("name") == "Write release.env")
    assert "DRY RUN" in script and "$PUBLISH" in script  # a dry run says so in the file itself
    uploads = [s["with"]["name"] for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert len(uploads) == 1 and "release-dry-run" in uploads[0] and "PUBLISH" in uploads[0]
    listed = re.search(r"for name in ([\w -]+); do", script)
    assert listed and "compose_lint.py --release release.env" in script
    made = {
        f"SBAM_{n.removeprefix('sbam-').upper().replace('-', '_')}_IMAGE" for n in listed[1].split()
    }
    template = (REPO / "compose" / "release.env.example").read_text(encoding="utf-8")
    assert made == set(re.findall(r"^(SBAM_\w+_IMAGE)=", template, re.MULTILINE))
    assert set(listed[1].split()) <= set(names)


def test_e16_4_ci_deploy_pins_netbird_and_checks_host_keys() -> None:
    text = (REPO / "scripts" / "ci-deploy.sh").read_text(encoding="utf-8")
    assert re.search(r"netbirdio/netbird:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", text)
    assert "StrictHostKeyChecking=yes" in text and "BatchMode=yes" in text
    assert not re.search(r"echo[^\n]*\$\{?(DEPLOY_SSH_KEY|NB_SETUP_KEY)", text)


# --- scripts/ci-deploy.sh with fakes -----------------------------------------------------------

FAKES = {
    "docker": 'echo "docker $*" >>"$FAKE_DIR/calls"; [[ $* == *"netbird status"* ]] && '
    'echo "Management: Connected"; exit 0',
    "ssh": 'echo "ssh $*" >>"$FAKE_DIR/calls"; cat >/dev/null; '
    '[[ $* == *"scripts/deploy.sh"* ]] && exit "${FAKE_DEPLOY_RC:-0}"; exit 0',
    "scp": 'echo "scp $*" >>"$FAKE_DIR/calls"',
    "curl": 'echo "curl $*" >>"$FAKE_DIR/calls"; printf %s "${FAKE_HTTP:-200}"',
}


def ci_deploy(tmp: Path, rc: int = 0, http: str = "200") -> tuple[int, str, str]:
    fake = tmp / "bin"
    fake.mkdir(exist_ok=True)
    for name, body in FAKES.items():
        (fake / name).write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
        (fake / name).chmod(0o755)
    release = tmp / "release.env"
    release.write_text(f"SBAM_API_IMAGE=ghcr.io/x/sbam-api@sha256:{'b' * 64}\n", encoding="utf-8")
    summary = tmp / "summary.md"
    out = subprocess.run(  # noqa: S603 - fixed argv, test-owned paths
        [BASH or "bash", "scripts/ci-deploy.sh", "staging", "v1.2.0", str(release)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp),
            "FAKE_DIR": str(tmp),
            "FAKE_DEPLOY_RC": str(rc),
            "FAKE_HTTP": http,
            "GITHUB_STEP_SUMMARY": str(summary),
            "NB_SETUP_KEY": "nb-secret",
            "DEPLOY_HOST": "vm-staging.netbird",
            "DEPLOY_USER": "deploy",
            "DEPLOY_SSH_KEY": "-----BEGIN KEY-----",
            "DEPLOY_KNOWN_HOSTS": "vm-staging.netbird ssh-ed25519 AAAA",
            "SBAM_PROFILES": "obs",
            "SBAM_DEPLOY_ACTOR": "rel'ease manager",
            "SMOKE_URLS": "https://portal.staging/ https://auth.staging/realms/bullm",
        },
    )
    calls = (tmp / "calls").read_text() if (tmp / "calls").exists() else ""
    return out.returncode, calls, summary.read_text() if summary.exists() else ""


needs_bash = pytest.mark.skipif(BASH is None or os.name == "nt", reason="bash on Linux (CI)")


@needs_bash
def test_e16_4_ci_deploy_runs_deploy_sh_on_the_vm_and_smoke_checks(tmp_path: Path) -> None:
    rc, calls, summary = ci_deploy(tmp_path)

    assert rc == 0, calls
    remote = next(c for c in calls.splitlines() if "scripts/deploy.sh" in c)
    assert "deploy@vm-staging.netbird" in remote
    assert "--env staging --id v1.2.0 --release release.env --bundle ." in remote
    assert "SBAM_PROFILES=obs" in remote and r"SBAM_DEPLOY_ACTOR=rel\'ease\ manager" in remote
    assert calls.count("curl ") == 2 and "docker rm -f sbam-netbird" in calls
    assert f"sha256:{'b' * 64}" in summary and "deployed" in summary
    assert "nb-secret" not in summary and "BEGIN KEY" not in summary
    assert not (tmp_path / ".ssh" / "sbam-deploy").exists()  # the key is removed afterwards


@needs_bash
def test_e16_4_t03_a_rolled_back_deploy_fails_the_job(tmp_path: Path) -> None:
    rc, calls, summary = ci_deploy(tmp_path, rc=2)

    assert rc == 2 and "rolled back to the previous release" in summary
    assert "curl " not in calls  # no smoke check after a failed deploy


@needs_bash
def test_e16_4_a_failed_smoke_check_fails_the_job(tmp_path: Path) -> None:
    rc, _, summary = ci_deploy(tmp_path, http="502")

    assert rc == 1 and "502" in summary
