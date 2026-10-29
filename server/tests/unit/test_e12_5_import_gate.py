"""E12.5: `import_gate` imports every module of a bundle in a fresh interpreter, side-effect free.

The probe really imports modules (`UncontainedImportRunner`, trusted test modules in a scratch
directory). The container is checked as an argv, and for real against a local Docker image when one
is there (integration). The stage runs against SQLite and a disk backend with a fake runner.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

import pytest
from sbam_api.manifests.models import GateResultRow
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import Finding, GateName, GateStatus
from sbam_workers.build.codegen import LoadedTemplate
from sbam_workers.build.gates.ast_gate import GATES as AST_GATES
from sbam_workers.build.gates.errors import GATE_INPUT_MISMATCH, IMPORT_GATE_INFRA
from sbam_workers.build.gates.import_gate import (
    PROBE,
    DockerImportRunner,
    ImportGate,
    ImportOutcome,
    UncontainedImportRunner,
    check_image,
    docker_argv,
    import_findings,
    module_names,
    outcome_of,
    read_result,
    write_package,
)
from sbam_workers.build.gates.static_gate import GATES as STATIC_GATES
from sbam_workers.build.gates.stored_bundle import (
    Verdict,
    find_assembled,
    read_verified,
    record_verdicts,
)
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_codegen_assemble import stage_input
from test_e12_2_ast_gate_stage import assembled, db, gate_stage, results, template  # noqa: F401

DIGEST = "sha256:" + "ab" * 32
IMAGE = f"registry.local/skill-runtime@{DIGEST}"


def probe(files: dict[str, str], timeout_s: int = 20) -> list[ImportOutcome]:
    runner = UncontainedImportRunner(sys.executable, timeout_s=timeout_s)
    encoded = {k: v.encode() for k, v in files.items()}
    return runner.run(encoded, module_names(encoded), IMAGE)


def found(files: dict[str, str], **kw: int) -> list[dict[str, str]]:
    return import_findings(probe(files, **kw))


def codes(files: dict[str, str], **kw: int) -> list[str]:
    return [f["code"] for f in found(files, **kw)]


# --- the probe --------------------------------------------------------------------------------


def test_e12_5_module_names() -> None:
    names = module_names({k: b"" for k in ("a.py", "pkg/__init__.py", "pkg/b.py", "x.json")})
    assert names == ["a", "pkg", "pkg.b"]


def test_e12_5_t02_modules_with_relative_imports_pass() -> None:
    """The bundle's modules say `from . import state`: they only import as part of a package."""
    files = {
        "state.py": "X = 1\n",
        "models.py": "from __future__ import annotations\nimport json\n",
        "action_tool.py": "from . import state\nfrom .models import json\nY = state.X\n",
        "server.py": "from . import action_tool, models\n",
    }
    assert probe(files) and codes(files) == []


def test_e12_5_t01_unresolved_import_is_a_module_not_found_finding() -> None:
    (hit,) = found({"a.py": "import pandas_ta\n"})
    assert (hit["code"], hit["file"], hit["gate"]) == (
        "ModuleNotFoundError",
        "a.py",
        "IMPORT_RESOLUTION",
    )
    assert "pandas_ta" in hit["message"]
    Finding.model_validate(hit)  # what the gates endpoint reads back


def test_e12_5_syntax_error_and_other_exceptions() -> None:
    assert codes({"a.py": "def f(:\n"}) == ["SyntaxError"]
    assert codes({"a.py": "raise ValueError('boom')\n"}) == ["IMPORT_ERROR"]
    assert codes({"a.py": "from json import nope\n"}) == ["ImportError"]


def test_e12_5_t04_a_circular_import_is_an_import_error() -> None:
    files = {"a.py": "from .b import B\nA = 1\n", "b.py": "from .a import A\nB = 2\n"}
    assert "ImportError" in codes(files)


def test_e12_5_t03_a_network_call_at_import_is_blocked_and_flagged() -> None:
    source = "import socket\nsocket.create_connection(('127.0.0.1', 9))\n"
    outcomes = probe({"a.py": source})  # the name is resolved first, so the hook fires there
    assert not outcomes[0].ok and set(outcomes[0].events) <= {
        "socket.getaddrinfo",
        "socket.connect",
    }
    assert outcomes[0].events and "IMPORT_SIDE_EFFECT" in codes({"a.py": source})
    direct = probe({"b.py": "import socket\nsocket.socket().connect(('127.0.0.1', 9))\n"})
    assert direct[0].events == ("socket.connect",)


def test_e12_5_t03_a_module_cannot_hide_the_side_effect_by_catching_the_block() -> None:
    source = (
        "import socket\ntry:\n    socket.create_connection(('127.0.0.1', 9))\n"
        "except BaseException:\n    pass\n"
    )
    assert codes({"a.py": source}) == ["IMPORT_SIDE_EFFECT"]


@pytest.mark.parametrize(
    ("source", "event"),
    [
        ("open({t!r}, 'w').write('x')\n", "open:write"),
        ("import os\nos.open({t!r}, os.O_WRONLY | os.O_CREAT)\n", "open:write"),
        ("import os\nos.mkdir({t!r})\n", "os.mkdir"),
        ("import os\nos.system('touch ' + {t!r})\n", "os.system"),
        ("import subprocess\nsubprocess.run(['touch', {t!r}])\n", "subprocess.Popen"),
        ("import os\nos.symlink('/etc/hostname', {t!r})\n", "os.symlink"),
        ("import shutil\nshutil.rmtree('nothing')\n", "shutil.rmtree"),
        ("import os\nos.remove('nothing')\n", "os.remove"),
    ],
)
def test_e12_5_side_effects_are_blocked_and_nothing_is_written(
    source: str, event: str, tmp_path: Path
) -> None:
    target = tmp_path / "leak"
    outcomes = probe({"a.py": source.format(t=str(target))})
    assert event in outcomes[0].events and not outcomes[0].ok
    assert any(f["code"] == "IMPORT_SIDE_EFFECT" for f in import_findings(outcomes))
    assert not list(tmp_path.iterdir())  # the hook blocked it: no file, directory or link


def test_e12_5_reading_a_file_is_not_a_side_effect() -> None:
    assert codes({"a.py": "data = open(__file__).read()\n"}) == []


def test_e12_5_t05_a_module_that_never_finishes_times_out() -> None:
    files = {"a.py": "import time\ntime.sleep(30)\n", "b.py": "X = 1\n"}
    assert codes(files, timeout_s=1) == ["IMPORT_TIMEOUT"]  # b still passes


def test_e12_5_a_module_that_kills_the_interpreter_is_a_failure_not_a_pass() -> None:
    (hit,) = found({"a.py": "import os\nos._exit(0)\n"})
    assert hit["code"] == "IMPORT_ERROR" and "without reporting" in hit["message"]


def test_e12_5_a_module_cannot_forge_the_result_without_the_nonce() -> None:
    forged = (
        'import os\nprint(\'{"ok": true, "type": "", "error": "", "events": []}\')\nos._exit(0)\n'
    )
    assert codes({"a.py": forged}) == ["IMPORT_ERROR"]


def test_e12_5_read_result_takes_the_last_marked_line_only() -> None:
    nonce = "n0nce"
    row = '{"ok": true, "type": "", "error": "", "events": []}'
    assert read_result(f"x\n{row}\n", nonce) is None
    assert read_result(f"{nonce} {{broken\n", nonce) is None
    assert read_result(f"{nonce} {row}\n", nonce) is not None
    assert outcome_of("m", None, 9).error_type == "InterpreterExit"


def test_e12_5_a_bundle_path_that_escapes_the_package_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ApplicationError):
        write_package(tmp_path / "b", {"../evil.py": b"x = 1\n"})
    assert not (tmp_path / "evil.py").exists()


# --- the container ----------------------------------------------------------------------------


def argv() -> list[str]:
    return docker_argv(IMAGE, "sbam-import-x", Path("/tmp/b"), "nonce", "skill_mod")  # noqa: S108


def test_e12_5_the_container_is_locked_down() -> None:
    cmd = argv()
    pairs = {cmd[i]: cmd[i + 1] for i in range(len(cmd) - 1)}
    assert pairs["--network"] == "none" and "--read-only" in cmd
    assert pairs["--user"] == "65534:65534" and pairs["--cap-drop"] == "ALL"
    assert pairs["--security-opt"] == "no-new-privileges"
    assert int(pairs["--pids-limit"]) <= 64 and pairs["--memory"] == pairs["--memory-swap"]
    assert pairs["--volume"].endswith(":/bundle:ro") and pairs["--entrypoint"] == "python"
    assert cmd[cmd.index(IMAGE) + 1 :][:3] == ["-I", "-B", "-c"] and "--privileged" not in cmd
    assert "--rm" in cmd and not any(a.startswith(("--publish", "-p", "--cap-add")) for a in cmd)


@pytest.mark.parametrize(
    "image",
    [
        "skill-runtime",
        "skill-runtime:latest",
        "reg/skill-runtime:1.2",
        "x@sha256:abc",
        f"@{DIGEST}",
        "",
    ],
)
def test_e12_5_an_image_that_is_not_pinned_by_digest_is_refused(image: str) -> None:
    with pytest.raises(ApplicationError) as err:
        check_image(image)
    assert err.value.non_retryable and err.value.type == GATE_INPUT_MISMATCH
    with pytest.raises(ApplicationError):
        docker_argv(image, "n", Path("/b"), "nonce", "m")
    with pytest.raises(ApplicationError):
        DockerImportRunner().run({"a.py": b""}, ["a"], image)


def test_e12_5_no_docker_is_an_infrastructure_error_never_a_finding() -> None:
    runner = DockerImportRunner(docker="/nonexistent/docker")
    with pytest.raises(ApplicationError) as err:
        runner.run({"a.py": b"X = 1\n"}, ["a"], IMAGE)
    assert err.value.type == IMPORT_GATE_INFRA and not err.value.non_retryable


def local_image() -> str | None:
    """A pinned reference to a Python image Docker already has: no pull, no network."""
    if shutil.which("docker") is None:
        return None
    done = subprocess.run(
        ["docker", "image", "inspect", "python:3.12-slim", "--format", "{{.Id}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    ident = done.stdout.strip()
    return f"python@{ident}" if done.returncode == 0 and ident.startswith("sha256:") else None


@pytest.mark.integration
def test_e12_5_in_a_real_container_nothing_can_reach_out_or_write() -> None:
    image = local_image()
    if image is None:
        pytest.skip("no local python:3.12-slim image")
    files = {
        "clean.py": b"X = 1\n",
        "net.py": (
            b"import socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), 2)\n"
            b"except OSError:\n    pass\n"
        ),
        "write.py": b"try:\n    open('/bundle/skill/leak', 'w')\nexcept OSError:\n    pass\n",
        "missing.py": b"import not_installed_here\n",
        "sleep.py": b"import time\ntime.sleep(30)\n",
        "user.py": b"import os\nassert os.getuid() == 65534, os.getuid()\n",
    }
    out = {
        o.module: o for o in DockerImportRunner(timeout_s=8).run(files, module_names(files), image)
    }
    assert out["clean"].ok and out["user"].ok
    assert not out["missing"].ok and out["missing"].error_type == "ModuleNotFoundError"
    assert out["sleep"].timed_out
    # the module swallowed the PermissionError, the recorded event still fails the gate
    assert out["net"].events and "IMPORT_SIDE_EFFECT" in [
        f["code"] for f in import_findings([out["net"]])
    ]
    assert "open:write" in out["write"].events
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=sbam-import-"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert left.stdout.strip() == "", "a timed-out container must be removed"


@pytest.mark.integration
def test_e12_5_t02_the_ms01_bundle_imports_in_the_skill_runtime_image(
    template: LoadedTemplate,  # noqa: F811
) -> None:
    """`SKILL_RUNTIME_IMAGE=name@sha256:...` (the CI-built skill-runtime, or a local build of its
    `libs` stage with `SKILL_RUNTIME_PYTHON=/opt/venv/bin/python`): FastMCP and pydantic import
    without a single blocked side effect, and all six modules pass."""
    image = os.environ.get("SKILL_RUNTIME_IMAGE")
    if not image:
        pytest.skip("SKILL_RUNTIME_IMAGE is not set")
    from codegen_cases import ms01_bodies, ms01_loop_spec, ms01_params
    from sbam_workers.build.codegen import render_skeleton
    from sbam_workers.build.codegen.bundle import assemble_bundle

    params = ms01_params()
    out = assemble_bundle(
        template,
        render_skeleton(template, params),
        ms01_bodies(),
        params.get("fresh_state", []),
        ms01_loop_spec(),
    )
    assert out.bundle is not None
    files = dict(out.bundle.files)
    runner = DockerImportRunner(python=os.environ.get("SKILL_RUNTIME_PYTHON", "python"))
    outcomes = runner.run(files, module_names(files), image)
    assert len(outcomes) == 6 and import_findings(outcomes) == []


# --- the stage --------------------------------------------------------------------------------


class FakeRunner:
    def __init__(self, outcomes: list[ImportOutcome] | None = None) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[list[str], str]] = []

    def run(
        self, files: Mapping[str, bytes], modules: list[str], image: str
    ) -> list[ImportOutcome]:
        self.calls.append((modules, image))
        return (
            self.outcomes
            if self.outcomes is not None
            else [ImportOutcome(m, True) for m in modules]
        )


def gate(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    runner: FakeRunner,
    image: str = IMAGE,
) -> ImportGate:
    return ImportGate(storage, sessions, runner, lambda _inp: image)


def run(stage: ImportGate, inp: BuildStageInput) -> uuid.UUID:
    return asyncio.run(ActivityEnvironment().run(stage.run, inp)).id


def passed_earlier_gates(
    template: LoadedTemplate,  # noqa: F811
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    inp: BuildStageInput,
) -> uuid.UUID:
    attempt_id = assembled(template, sessions, storage, inp)
    asyncio.run(ActivityEnvironment().run(gate_stage(template, sessions, storage).run, inp))
    bundle = read_verified(storage, find_assembled(sessions, inp))
    verdicts = [Verdict(g, GateStatus.PASSED, [], "t/1", "c") for g in STATIC_GATES]
    record_verdicts(sessions, inp, bundle, verdicts)
    return attempt_id


def import_rows(sessions: sessionmaker[Session], inp: BuildStageInput) -> list[GateResultRow]:
    return [r for r in results(sessions, inp) if r.gate == GateName.IMPORT_RESOLUTION.value]


def test_e12_5_t02_stage_passes_and_records_one_verdict(
    template: LoadedTemplate, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = passed_earlier_gates(template, sessions, storage, inp)
    runner = FakeRunner()
    assert run(gate(sessions, storage, runner), inp) == attempt_id
    (row,) = import_rows(sessions, inp)
    assert row.status == "PASSED" and row.findings == [] and row.code_attempt_id == attempt_id
    modules, image = runner.calls[0]
    assert image == IMAGE and {"action_tool", "server", "state"} <= set(modules)
    assert row.tool_version and DIGEST[:20] in row.tool_version


def test_e12_5_t06_a_retry_leaves_a_single_gate_row(
    template: LoadedTemplate, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    passed_earlier_gates(template, sessions, storage, inp)
    stage = gate(sessions, storage, FakeRunner())
    run(stage, inp)
    first = [(r.id, r.status) for r in import_rows(sessions, inp)]
    run(stage, inp)
    assert [(r.id, r.status) for r in import_rows(sessions, inp)] == first and len(first) == 1


def test_e12_5_a_failing_import_raises_gate_failed_with_the_findings(
    template: LoadedTemplate, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = passed_earlier_gates(template, sessions, storage, inp)
    bad = [ImportOutcome("server", False, "ModuleNotFoundError", "No module named 'pandas_ta'")]
    with pytest.raises(ApplicationError) as err:
        run(gate(sessions, storage, FakeRunner(bad)), inp)
    assert err.value.type == GATE_FAILED and err.value.non_retryable
    assert err.value.details[0] == [str(attempt_id)]
    (row,) = import_rows(sessions, inp)
    assert row.status == "FAILED"
    assert [f["code"] for f in row.findings] == ["ModuleNotFoundError"]


@pytest.mark.parametrize("missing", [AST_GATES[0], STATIC_GATES[0], STATIC_GATES[-1]])
def test_e12_5_nothing_runs_unless_the_earlier_gates_passed_this_bundle(
    template: LoadedTemplate, tmp_path: Path, missing: GateName  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    passed_earlier_gates(template, sessions, storage, inp)
    with sessions() as session:  # one earlier verdict is not a pass
        row = next(r for r in session.query(GateResultRow) if r.gate == missing.value)
        row.status = "FAILED"
        session.commit()
    runner = FakeRunner()
    with pytest.raises(ApplicationError) as err:
        run(gate(sessions, storage, runner), inp)
    assert err.value.non_retryable and err.value.type == GATE_INPUT_MISMATCH
    assert runner.calls == [] and import_rows(sessions, inp) == []


def test_e12_5_a_verdict_about_another_bundle_does_not_count(
    template: LoadedTemplate, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    passed_earlier_gates(template, sessions, storage, inp)
    with sessions() as session:
        row = next(iter(session.query(GateResultRow)))
        row.bundle_sha256 = "f" * 64
        session.commit()
    runner = FakeRunner()
    with pytest.raises(ApplicationError):
        run(gate(sessions, storage, runner), inp)
    assert runner.calls == []


def test_e12_5_a_tag_instead_of_a_digest_stops_the_stage_before_anything_runs(
    template: LoadedTemplate, tmp_path: Path  # noqa: F811
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    passed_earlier_gates(template, sessions, storage, inp)
    runner = FakeRunner()
    with pytest.raises(ApplicationError) as err:
        run(gate(sessions, storage, runner, "skill-runtime:latest"), inp)
    assert err.value.non_retryable and runner.calls == []


def test_e12_5_infrastructure_trouble_is_retryable_and_records_nothing(
    template: LoadedTemplate, tmp_path: Path  # noqa: F811
) -> None:
    class Down:
        def run(
            self, files: Mapping[str, bytes], modules: list[str], image: str
        ) -> list[ImportOutcome]:
            raise ApplicationError("docker is down", type=IMPORT_GATE_INFRA)

    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    passed_earlier_gates(template, sessions, storage, inp)
    with pytest.raises(ApplicationError) as err:
        run(ImportGate(storage, sessions, Down(), lambda _i: IMAGE), inp)
    assert err.value.type == IMPORT_GATE_INFRA and not err.value.non_retryable
    assert import_rows(sessions, inp) == []


def test_e12_5_the_probe_is_part_of_the_recorded_config() -> None:
    from sbam_workers.build.gates.import_gate import CONFIG_SHA256

    assert CONFIG_SHA256.startswith("sha256:") and "addaudithook" in PROBE
