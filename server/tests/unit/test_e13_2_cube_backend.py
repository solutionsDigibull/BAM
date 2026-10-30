"""E13.2 CubeSandbox backend against a fake SDK, plus a drift test against the real one.

The fake replaces only `Sandbox` and `Config`; the exception classes are the SDK's own, so the error
mapping is tested against the real types. The drift test pins every SDK signature the backend uses
(ADR 0023, decision 2): an SDK upgrade that renames one fails here, not on the host.
"""

import asyncio
import inspect
import time
import types
import uuid
from collections.abc import Callable
from typing import Any, ClassVar

import cubesandbox  # type: ignore[import-untyped]  # the SDK ships no stubs
import httpx
import pytest
import sbam_workers.sandbox.cube_backend as cube_module
from sbam_contracts.sandbox import InfraReason, SandboxLimits, SandboxMode
from sbam_workers.sandbox.backend import SandboxInfraError, VmHandle, VmSpec
from sbam_workers.sandbox.cube_backend import (
    RUN_USER,
    STDERR_PATH,
    STDOUT_PATH,
    SYSTEM_USER,
    CubeSandboxBackend,
    CubeSettings,
    redirected_command,
    tail_command,
)

DIGEST = "sha256:" + "a" * 64


class FakeCommands:
    def __init__(self, box: "FakeBox") -> None:
        self.box = box

    def run(self, cmd: str, **kwargs: Any) -> cubesandbox.CommandResult:
        self.box.runs.append((cmd, kwargs))
        return self.box.on_run(cmd, kwargs)


class FakeFiles:
    def __init__(self, box: "FakeBox") -> None:
        self.box = box

    def write(self, path: str, data: bytes, *, user: str | None = None) -> None:
        self.box.writes.append((path, data, user))

    def read(self, path: str, *, user: str | None = None) -> str:
        return self.box.fs[path]

    def exists(self, path: str, *, user: str | None = None) -> bool:
        return path in self.box.fs


class FakeBox:
    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.runs: list[tuple[str, dict[str, Any]]] = []
        self.writes: list[tuple[str, bytes, str | None]] = []
        self.fs: dict[str, str] = {"/etc/sbam-runtime": DIGEST + "\n"}
        self.killed = 0
        self.kill_error: Exception | None = None
        self.on_run: Callable[[str, dict[str, Any]], cubesandbox.CommandResult] = self.default_run
        self.commands = FakeCommands(self)
        self.files = FakeFiles(self)

    def default_run(self, cmd: str, kwargs: dict[str, Any]) -> cubesandbox.CommandResult:
        if cmd.startswith("head -c"):
            return cubesandbox.CommandResult("", "", 0)
        return cubesandbox.CommandResult("", "", 0)

    def kill(self) -> None:
        self.killed += 1
        if self.kill_error is not None:
            raise self.kill_error


class FakeSandbox:
    boxes: ClassVar[dict[str, FakeBox]] = {}
    created: ClassVar[list[dict[str, Any]]] = []
    create_error: ClassVar[Exception | None] = None
    listing: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.boxes, cls.created, cls.create_error, cls.listing = {}, [], None, []

    @classmethod
    def create(cls, template: str | None = None, **kwargs: Any) -> FakeBox:
        if cls.create_error is not None:
            raise cls.create_error
        cls.created.append({"template": template, **kwargs})
        box = FakeBox(f"vm-{len(cls.boxes) + 1}")
        cls.boxes[box.sandbox_id] = box
        return box

    @classmethod
    def connect(cls, sandbox_id: str, timeout: int | None = None, *, config: Any = None) -> FakeBox:
        if sandbox_id not in cls.boxes:
            raise cubesandbox.SandboxNotFoundError("gone", 404)
        return cls.boxes[sandbox_id]

    @classmethod
    def list_v2(cls, config: Any = None) -> list[dict[str, Any]]:
        return cls.listing


class FakeConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def sdk() -> Any:
    FakeSandbox.reset()
    return types.SimpleNamespace(
        Sandbox=FakeSandbox,
        Config=FakeConfig,
        CubeSandboxError=cubesandbox.CubeSandboxError,
        SandboxNotFoundError=cubesandbox.SandboxNotFoundError,
        TemplateNotFoundError=cubesandbox.TemplateNotFoundError,
        FilesystemNotFoundError=cubesandbox.FilesystemNotFoundError,
    )


def backend() -> CubeSandboxBackend:
    return CubeSandboxBackend(
        CubeSettings("http://cube:3000", proxy_node_ip="10.0.0.9", api_key="k"), sdk()
    )


def spec(**over: Any) -> VmSpec:
    base: dict[str, Any] = {
        "run_id": uuid.UUID(int=5),
        "template_alias": "sbam-rt-" + "a" * 56,
        "runtime_digest": DIGEST,
        "limits": SandboxLimits(),
        "allow_out": ("203.0.113.7",),
        "hosts": {},
        "ttl_seconds": 600,
        "metadata": {"sbam_run": str(uuid.UUID(int=5))},
    }
    return VmSpec(**{**base, **over})


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_e13_2_create_pins_template_egress_and_a_backstop_ttl() -> None:
    b = backend()
    vm = run(b.create(spec()))
    made = FakeSandbox.created[0]
    assert vm.vm_id == "vm-1"
    assert made["template"] == "sbam-rt-" + "a" * 56
    assert made["allow_internet_access"] is False  # allow-listed IPs only
    assert made["network"] == {"allow_out": ["203.0.113.7"]}
    assert made["timeout"] == 600
    assert made["metadata"]["sbam_run"] == str(uuid.UUID(int=5))
    assert b.mode is SandboxMode.CUBESANDBOX and b.mode.certifying


def test_e13_2_no_allowlist_means_no_network_block_at_all() -> None:
    b = backend()
    run(b.create(spec(allow_out=())))
    assert FakeSandbox.created[0]["network"] is None
    assert FakeSandbox.created[0]["allow_internet_access"] is False


def test_e13_2_a_missing_template_is_a_digest_mismatch_not_a_stale_run() -> None:
    b = backend()
    FakeSandbox.create_error = cubesandbox.TemplateNotFoundError("template not found", 404)
    with pytest.raises(SandboxInfraError) as caught:
        run(b.create(spec()))
    assert caught.value.reason is InfraReason.TEMPLATE_DIGEST_MISMATCH


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ConnectionRefusedError("refused"), InfraReason.HOST_UNREACHABLE),
        (httpx.ConnectError("down"), InfraReason.HOST_UNREACHABLE),
        (cubesandbox.ApiError("boom", 500), InfraReason.VM_CREATE_FAILED),
        (cubesandbox.CubeSandboxError("no key", 401), InfraReason.VM_CREATE_FAILED),
    ],
)
def test_e13_2_host_trouble_is_infrastructure_with_a_reason(
    error: Exception, reason: InfraReason
) -> None:
    b = backend()
    FakeSandbox.create_error = error
    with pytest.raises(SandboxInfraError) as caught:
        run(b.create(spec()))
    assert caught.value.reason is reason
    assert "http://cube" not in str(caught.value)  # the host's address is not echoed


def test_e13_2_files_are_written_as_the_run_user_and_the_hosts_file_as_root() -> None:
    b = backend()
    vm = run(b.create(spec()))
    run(b.put_files(vm, {"/etc/hosts": b"1.2.3.4 x\n"}, system=True))
    run(b.put_files(vm, {"/work/request.json": b"{}"}))
    writes = FakeSandbox.boxes["vm-1"].writes
    assert writes[0] == ("/etc/hosts", b"1.2.3.4 x\n", SYSTEM_USER)
    assert writes[1] == ("/work/request.json", b"{}", RUN_USER)
    assert (
        len({RUN_USER, SYSTEM_USER}) == 2
    )  # tests never run as the user that writes the hosts file


def test_e13_2_the_vms_own_digest_is_read_as_root() -> None:
    b = backend()
    vm = run(b.create(spec()))
    assert run(b.read_runtime_digest(vm)).strip() == DIGEST


def test_e13_2_argv_is_quoted_so_nothing_in_it_is_shell() -> None:
    line = redirected_command(["python", "-I", "/x y/run.py", "a; rm -rf /", "$(id)"])
    assert line.startswith("python -I '/x y/run.py' 'a; rm -rf /' '$(id)' >")
    assert line.endswith(f"> {STDOUT_PATH} 2> {STDERR_PATH}")
    assert tail_command("/f", 10) == "head -c 11 /f"


def test_e13_2_exec_runs_as_the_run_user_with_env_and_reads_both_streams_back() -> None:
    b = backend()
    vm = run(b.create(spec()))
    box = FakeSandbox.boxes["vm-1"]

    def on_run(cmd: str, kwargs: dict[str, Any]) -> cubesandbox.CommandResult:
        if cmd == f"head -c 101 {STDOUT_PATH}":
            return cubesandbox.CommandResult("out", "", 0)
        if cmd == f"head -c 101 {STDERR_PATH}":
            return cubesandbox.CommandResult("err", "", 0)
        return cubesandbox.CommandResult("", "", 3)

    box.on_run = on_run
    result = run(b.exec(vm, ["python", "r.py"], timeout_s=60, env={"A": "1"}, max_output_bytes=100))
    assert (result.exit_code, result.stdout, result.stderr) == (3, b"out", b"err")
    assert not result.truncated and not result.timed_out
    first_cmd, first_kwargs = box.runs[0]
    assert first_cmd.startswith("python r.py >")
    assert first_kwargs["user"] == RUN_USER and first_kwargs["envs"] == {"A": "1"}
    assert first_kwargs["timeout"] == 60


def test_e13_2_output_past_the_cap_is_cut_and_flagged() -> None:
    b = backend()
    vm = run(b.create(spec()))
    box = FakeSandbox.boxes["vm-1"]

    def on_run(cmd: str, kwargs: dict[str, Any]) -> cubesandbox.CommandResult:
        if cmd.startswith("head -c"):
            return cubesandbox.CommandResult("x" * 11, "", 0)  # cap + 1: it was cut
        return cubesandbox.CommandResult("", "", 0)

    box.on_run = on_run
    result = run(b.exec(vm, ["x"], timeout_s=10, max_output_bytes=10))
    assert result.truncated and len(result.stdout) == 10 and len(result.stderr) == 10


@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("slow"), RuntimeError("deadline_exceeded: the deadline passed")],
)
def test_e13_2_a_passed_deadline_is_a_timeout_not_infrastructure(error: Exception) -> None:
    b = backend()
    vm = run(b.create(spec()))
    box = FakeSandbox.boxes["vm-1"]

    def on_run(cmd: str, kwargs: dict[str, Any]) -> cubesandbox.CommandResult:
        if cmd.startswith("head -c"):
            return cubesandbox.CommandResult("partial", "", 0)
        raise error

    box.on_run = on_run
    result = run(b.exec(vm, ["sleep", "70"], timeout_s=60, max_output_bytes=100))
    assert result.timed_out and result.exit_code == 124 and result.stdout == b"partial"


def test_e13_2_other_runtime_errors_in_exec_are_infrastructure() -> None:
    b = backend()
    vm = run(b.create(spec()))
    FakeSandbox.boxes["vm-1"].on_run = lambda cmd, kw: (_ for _ in ()).throw(
        RuntimeError("process stream ended without EndEvent")
    )
    with pytest.raises(SandboxInfraError) as caught:
        run(b.exec(vm, ["x"], timeout_s=10, max_output_bytes=10))
    assert caught.value.reason is InfraReason.RUNNER_FAILED


def test_e13_2_get_file_is_none_when_missing_and_capped_when_large() -> None:
    b = backend()
    vm = run(b.create(spec()))
    box = FakeSandbox.boxes["vm-1"]
    assert run(b.get_file(vm, "/work/out/outputs.json", max_bytes=100)) is None
    box.fs["/work/out/outputs.json"] = '{"a":1}'
    assert run(b.get_file(vm, "/work/out/outputs.json", max_bytes=100)) == b'{"a":1}'
    with pytest.raises(SandboxInfraError):
        run(b.get_file(vm, "/work/out/outputs.json", max_bytes=3))


def test_e13_2_destroy_kills_and_forgets_and_is_idempotent() -> None:
    b = backend()
    vm = run(b.create(spec()))
    run(b.destroy(vm.vm_id))
    assert FakeSandbox.boxes["vm-1"].killed == 1
    run(b.destroy(vm.vm_id))  # a second call reattaches by id; the host says it is gone
    assert FakeSandbox.boxes["vm-1"].killed == 2


def test_e13_2_destroying_a_vm_the_host_no_longer_has_succeeds() -> None:
    b = backend()
    run(b.destroy("vm-unknown"))  # connect raises SandboxNotFoundError: nothing to do


def test_e13_2_destroy_that_fails_for_real_is_reported() -> None:
    b = backend()
    vm = run(b.create(spec()))
    FakeSandbox.boxes["vm-1"].kill_error = cubesandbox.ApiError("boom", 500)
    with pytest.raises(SandboxInfraError) as caught:
        run(b.destroy(vm.vm_id))
    assert caught.value.reason is InfraReason.DESTROY_FAILED


def test_e13_2_a_worker_cannot_exec_in_a_vm_it_did_not_create() -> None:
    b = backend()
    with pytest.raises(SandboxInfraError):
        run(b.exec(VmHandle("vm-foreign"), ["x"], timeout_s=5, max_output_bytes=10))


def test_e13_2_list_vms_carries_the_tags_the_reaper_filters_on() -> None:
    b = backend()
    run(b.create(spec()))
    FakeSandbox.listing = [
        {"sandboxID": "vm-1", "startedAt": "2026-10-30T10:00:00Z", "metadata": {"sbam_run": "r"}},
        {"sandboxID": "vm-9", "startedAt": "garbage", "metadata": None},
    ]
    vms = run(b.list_vms())
    assert vms[0].vm_id == "vm-1" and vms[0].metadata == {"sbam_run": "r"}
    assert vms[0].started_at is not None and vms[0].started_at.tzinfo is not None
    assert vms[1].started_at is None and dict(vms[1].metadata) == {}


def test_e13_2_a_hung_host_call_ends_as_host_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    b = backend()
    monkeypatch.setattr(cube_module, "CONTROL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(
        FakeSandbox, "create", classmethod(lambda cls, template=None, **kw: time.sleep(0.5))
    )
    with pytest.raises(SandboxInfraError) as caught:
        run(b.create(spec()))
    assert caught.value.reason is InfraReason.HOST_UNREACHABLE


# --- drift against the real SDK (ADR 0023, decision 2) ------------------------------------------


def params(fn: Any) -> set[str]:
    return set(inspect.signature(fn).parameters)


def test_e13_2_sdk_drift_the_calls_we_make_still_exist() -> None:
    assert cubesandbox.__version__ == "0.7.3"  # a bump is a deliberate, reviewed change
    assert {
        "template",
        "timeout",
        "metadata",
        "allow_internet_access",
        "network",
        "config",
    } <= params(cubesandbox.Sandbox.create)
    assert {"sandbox_id", "config"} <= params(cubesandbox.Sandbox.connect)
    assert "config" in params(cubesandbox.Sandbox.list_v2)
    assert params(cubesandbox.Sandbox.kill) == {"self"}
    assert {"timeout", "envs", "user"} <= params(cubesandbox.sandbox.Commands.run)
    fs = cubesandbox._filesystem.Filesystem
    for name in ("read", "write", "exists"):
        assert "user" in params(getattr(fs, name))
    assert {"api_url", "api_key", "proxy_node_ip", "proxy_port", "sandbox_domain"} <= {
        f.name for f in __import__("dataclasses").fields(cubesandbox.Config)
    }
    for name in (
        "CubeSandboxError",
        "SandboxNotFoundError",
        "TemplateNotFoundError",
        "FilesystemNotFoundError",
        "ApiError",
    ):
        assert issubclass(getattr(cubesandbox, name), Exception)
    assert issubclass(cubesandbox.SandboxNotFoundError, cubesandbox.CubeSandboxError)
    assert issubclass(cubesandbox.TemplateNotFoundError, cubesandbox.CubeSandboxError)


def test_e13_2_the_real_sdk_builds_a_backend_without_touching_the_network() -> None:
    b = CubeSandboxBackend(CubeSettings("http://127.0.0.1:1", api_key="s3cret"))
    assert "s3cret" not in repr(b._config)  # the SDK masks the key itself
    assert "s3cret" not in repr(CubeSettings("http://x", api_key="s3cret"))
    with pytest.raises(SandboxInfraError) as caught:
        run(b.list_vms())
    assert caught.value.reason is InfraReason.HOST_UNREACHABLE
