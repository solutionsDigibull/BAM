"""E13.2 egress evidence reader and the sandbox factory: the mode comes from the worker's settings
and nowhere else, and a certifying worker without evidence or host settings refuses to run."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sbam_contracts.sandbox import (
    EgressEvent,
    EgressSource,
    EgressVerdict,
    InfraReason,
    SandboxMode,
)
from sbam_workers.sandbox.backend import SandboxInfraError
from sbam_workers.sandbox.cube_backend import CubeSandboxBackend
from sbam_workers.sandbox.egress import MAX_EVENTS, JsonlEgressReader, read_events
from sbam_workers.sandbox.factory import make_sandbox, problem
from sbam_workers.sandbox.local_backend import LocalDockerBackend
from sbam_workers.settings import WorkerSettings

AT = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def event(
    vm: str, host: str = "evil.example.com", verdict: EgressVerdict = EgressVerdict.BLOCK
) -> str:
    return EgressEvent(
        vm_id=vm, at=AT, dst_host=host, dst_port=443, verdict=verdict, source=EgressSource.L7_PROXY
    ).model_dump_json()


def log(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "egress.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_e13_2_only_this_vms_events_are_returned(tmp_path: Path) -> None:
    path = log(tmp_path, event("vm-1"), event("vm-2"), event("vm-1", "api.example.com"))
    found = read_events(path, "vm-1")
    assert [e.dst_host for e in found] == ["evil.example.com", "api.example.com"]


def test_e13_2_a_line_that_merely_mentions_the_id_is_not_this_vms_event(tmp_path: Path) -> None:
    other = json.loads(event("vm-2", "vm-1.example.com"))  # the id appears in another field
    path = log(tmp_path, json.dumps(other))
    assert read_events(path, "vm-1") == []


def test_e13_2_garbage_lines_are_skipped_never_trusted(tmp_path: Path) -> None:
    path = log(tmp_path, "not json", '{"vm_id": "vm-1"}', event("vm-1"), "\x00\x01 vm-1")
    assert len(read_events(path, "vm-1")) == 1


def test_e13_2_a_missing_evidence_file_is_an_infrastructure_error(tmp_path: Path) -> None:
    with pytest.raises(SandboxInfraError) as caught:
        read_events(tmp_path / "nope.jsonl", "vm-1")
    assert caught.value.reason is InfraReason.EGRESS_LOG_UNAVAILABLE
    assert str(tmp_path) not in str(caught.value)  # no path leaks into the run row


def test_e13_2_the_events_of_one_vm_are_capped(tmp_path: Path) -> None:
    path = log(tmp_path, *[event("vm-1")] * (MAX_EVENTS + 50))
    assert len(read_events(path, "vm-1")) == MAX_EVENTS


def test_e13_2_the_async_reader_reads_the_same_file(tmp_path: Path) -> None:
    import asyncio

    reader = JsonlEgressReader(str(log(tmp_path, event("vm-1"))))
    assert len(asyncio.run(reader("vm-1"))) == 1


def settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> WorkerSettings:
    for key in (
        "SANDBOX_MODE", "CUBE_API_URL", "CUBE_API_KEY", "CUBE_PROXY_NODE_IP",
        "SANDBOX_EGRESS_LOG_PATH", "SANDBOX_AGENT_URL", "SANDBOX_AGENT_TOKEN",
    ):  # fmt: skip
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return WorkerSettings()  # type: ignore[call-arg]


CUBE = {
    "SANDBOX_MODE": "CUBESANDBOX",
    "CUBE_API_URL": "http://cube:3000",
    "CUBE_PROXY_NODE_IP": "10.0.0.9",
    "SANDBOX_EGRESS_LOG_PATH": "/var/egress.jsonl",
}
LOCAL = {
    "SANDBOX_MODE": "LOCAL_DOCKER_NONCERT",
    "SANDBOX_AGENT_URL": "http://sandbox-runner:8091",
    "SANDBOX_AGENT_TOKEN": "t" * 24,
}


def test_e13_2_no_mode_means_nothing_runs_and_the_reason_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = settings(monkeypatch)
    assert s.sandbox_mode is None and problem(s) == "SANDBOX_MODE is not set"
    with pytest.raises(SandboxInfraError) as caught:
        make_sandbox(s)
    assert caught.value.reason is InfraReason.NOT_CONFIGURED


def test_e13_2_empty_compose_values_read_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    blank = {"SANDBOX_MODE": "", "CUBE_API_URL": "", "SANDBOX_AGENT_TOKEN": " "}
    s = settings(monkeypatch, **blank)
    assert s.sandbox_mode is None and s.cube_api_url is None and s.sandbox_agent_token is None


def test_e13_2_the_certifying_mode_builds_the_cube_backend_with_its_evidence_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = make_sandbox(settings(monkeypatch, **CUBE))
    assert sandbox.mode is SandboxMode.CUBESANDBOX and sandbox.mode.certifying
    assert isinstance(sandbox.backend, CubeSandboxBackend) and sandbox.egress_reader is not None
    assert sandbox.backend.mode is sandbox.mode  # backend and run can never disagree


def test_e13_2_t02_the_local_mode_builds_the_non_certifying_backend_without_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = make_sandbox(settings(monkeypatch, **LOCAL))
    assert sandbox.mode is SandboxMode.LOCAL_DOCKER_NONCERT and not sandbox.mode.certifying
    assert isinstance(sandbox.backend, LocalDockerBackend) and sandbox.egress_reader is None


@pytest.mark.parametrize(
    "missing", ["CUBE_API_URL", "CUBE_PROXY_NODE_IP", "SANDBOX_EGRESS_LOG_PATH"]
)
def test_e13_2_a_certifying_worker_missing_a_host_setting_refuses_and_names_only_the_variable(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    s = settings(monkeypatch, **{k: v for k, v in CUBE.items() if k != missing})
    assert problem(s) == f"{missing} not set"
    with pytest.raises(SandboxInfraError) as caught:
        make_sandbox(s)
    assert caught.value.reason is InfraReason.NOT_CONFIGURED and "cube:3000" not in str(
        caught.value
    )


@pytest.mark.parametrize("missing", ["SANDBOX_AGENT_URL", "SANDBOX_AGENT_TOKEN"])
def test_e13_2_the_local_mode_needs_its_agent(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    s = settings(monkeypatch, **{k: v for k, v in LOCAL.items() if k != missing})
    with pytest.raises(SandboxInfraError):
        make_sandbox(s)


def test_e13_2_the_mode_is_read_from_the_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """No constructor path takes a mode from a request: `WorkerSettings` is read from the env."""
    s = settings(monkeypatch, **LOCAL)
    assert s.sandbox_mode is SandboxMode.LOCAL_DOCKER_NONCERT
    assert "sandbox_mode" in WorkerSettings.model_fields
