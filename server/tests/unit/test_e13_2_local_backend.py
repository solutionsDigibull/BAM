"""E13.2 local-docker backend against the real agent process loop (no Docker needed).

The agent runs in a thread on 127.0.0.1 and the backend talks to it over real HTTP, so the
protocol, the path rules and the process handling are all exercised. Test code under the sandbox is
untrusted, so most of this file is attacks from inside a VM.
"""

import asyncio
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sbam_contracts.sandbox import InfraReason, SandboxLimits, SandboxMode
from sbam_workers.sandbox.backend import SandboxInfraError, VmHandle, VmSpec
from sbam_workers.sandbox.local_backend import LocalDockerBackend

AGENT_PATH = Path(__file__).resolve().parents[3] / "compose" / "sandbox-runner" / "agent.py"
TOKEN = "t" * 32


def load_agent() -> Any:
    spec = importlib.util.spec_from_file_location("sbam_sandbox_agent", AGENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["sbam_sandbox_agent"] = module
    spec.loader.exec_module(module)
    # RLIMIT_NPROC counts every process of the test user, not just ours
    setattr(module, "NPROC", 0)  # noqa: B010
    return module


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, Any]]:
    mod = load_agent()
    agent = mod.Agent(tmp_path / "vms", TOKEN, "sha256:" + "a" * 64)
    (tmp_path / "vms").mkdir()
    server = mod.ThreadingHTTPServer(("127.0.0.1", 0), mod.handler_for(agent))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", agent
    server.shutdown()
    server.server_close()


def spec() -> VmSpec:
    return VmSpec(
        run_id=uuid.UUID(int=7),
        template_alias="sbam-rt-x",
        runtime_digest="sha256:" + "a" * 64,
        limits=SandboxLimits(),
        allow_out=(),
        hosts={},
        ttl_seconds=60,
        metadata={},
    )


def backend(url: str, token: str = TOKEN) -> LocalDockerBackend:
    return LocalDockerBackend(url, token)


def sh(code: str) -> tuple[str, ...]:
    return ("python3", "-c", code)


async def ex(
    b: LocalDockerBackend,
    vm: VmHandle,
    *argv: str,
    timeout: float = 20,
    cap: int = 4096,
) -> Any:
    return await b.exec(vm, argv, timeout_s=timeout, max_output_bytes=cap)


def test_e13_2_the_five_verbs_work_end_to_end(served: tuple[str, Any]) -> None:
    url, _ = served

    async def go() -> None:
        b = backend(url)
        assert b.mode is SandboxMode.LOCAL_DOCKER_NONCERT and not b.mode.certifying
        assert await b.read_runtime_digest(VmHandle("x")) == "sha256:" + "a" * 64
        vm = await b.create(spec())
        await b.put_files(vm, {"/work/in.txt": b"hello"})
        done = await ex(
            b,
            vm,
            "python3",
            "-c",
            "import sys;print(open(sys.argv[1]).read().upper())",
            "/work/in.txt",
        )
        assert done.exit_code == 0 and done.stdout.strip() == b"HELLO"
        await ex(
            b,
            vm,
            "python3",
            "-c",
            "import os,sys;d=os.path.dirname(sys.argv[1]);"
            "os.makedirs(d+'/out');open(d+'/out/o.json','w').write('{}')",
            "/work/in.txt",
        )
        assert await b.get_file(vm, "/work/out/o.json", max_bytes=100) == b"{}"
        assert await b.get_file(vm, "/work/missing", max_bytes=100) is None
        assert [v.vm_id for v in await b.list_vms()] == [vm.vm_id]
        await b.destroy(vm.vm_id)
        await b.destroy(vm.vm_id)  # idempotent
        assert await b.list_vms() == []

    asyncio.run(go())


def test_e13_2_t06_a_timeout_kills_the_whole_process_group(served: tuple[str, Any]) -> None:
    url, _ = served
    spawn = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen(['sleep','600']);"
        "open(sys.argv[1],'w').write(str(p.pid));time.sleep(600)"
    )
    pids: list[int] = []

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        await b.put_files(vm, {"/work/seed": b"x"})  # puts create /work, as in a real run
        done = await ex(b, vm, "python3", "-c", spawn, "/work/child.pid", timeout=1)
        assert done.timed_out
        recorded = await b.get_file(vm, "/work/child.pid", max_bytes=20)
        assert recorded
        pids.append(int(recorded))
        await b.destroy(vm.vm_id)

    asyncio.run(go())
    # SIGKILL is delivered at once but the child can linger as a zombie for a moment
    for _ in range(40):
        try:
            os.kill(pids[0], 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError("a process of the timed-out run is still alive")


def test_e13_2_output_over_the_cap_is_truncated_and_flagged(
    served: tuple[str, Any],
) -> None:
    url, _ = served

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        done = await ex(b, vm, *sh("import sys;sys.stdout.write('x'*5_000_000)"), cap=1024)
        assert done.truncated and len(done.stdout) <= 1024
        await b.destroy(vm.vm_id)

    asyncio.run(go())


def test_e13_2_a_wrong_token_is_refused_as_infra(served: tuple[str, Any]) -> None:
    url, _ = served
    with pytest.raises(SandboxInfraError) as caught:
        asyncio.run(backend(url, "w" * 32).create(spec()))
    assert caught.value.reason is InfraReason.VM_CREATE_FAILED


def test_e13_2_an_unreachable_agent_is_host_unreachable() -> None:
    with pytest.raises(SandboxInfraError) as caught:
        asyncio.run(backend("http://127.0.0.1:1").create(spec()))
    assert caught.value.reason is InfraReason.HOST_UNREACHABLE


@pytest.mark.parametrize(
    "path",
    [
        "/work/../../etc/passwd",
        "work/x",
        "/work/a b",
        "/work/\x00x",
        "/",
        "//etc/x",
        "/work/./x/../..",
    ],
)
def test_e13_2_paths_that_could_escape_are_refused(served: tuple[str, Any], path: str) -> None:
    url, _ = served

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        with pytest.raises(SandboxInfraError):
            await b.put_files(vm, {path: b"x"})
        with pytest.raises(SandboxInfraError):
            await b.get_file(vm, path, max_bytes=10)

    asyncio.run(go())


def test_e13_2_a_symlink_planted_by_test_code_cannot_leak_or_redirect(
    served: tuple[str, Any], tmp_path: Path
) -> None:
    """Test code runs in the VM directory and can make links. A read or write through one must stay
    inside the VM, or generated code could read the agent's environment or write outside it.
    """
    url, _ = served
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET")

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        await b.put_files(vm, {"/work/seed": b"x"})
        await ex(
            b,
            vm,
            "python3",
            "-c",
            "import os,sys;os.symlink(sys.argv[1], os.path.dirname(sys.argv[2])+'/link')",
            str(outside),
            "/work/seed",
        )
        with pytest.raises(SandboxInfraError):
            await b.get_file(vm, "/work/link/secret.txt", max_bytes=100)
        with pytest.raises(SandboxInfraError):
            await b.put_files(vm, {"/work/link/planted.txt": b"pwn"})
        await b.destroy(vm.vm_id)

    asyncio.run(go())
    assert not (outside / "planted.txt").exists()


def test_e13_2_a_file_cannot_be_overwritten_after_the_platform_wrote_it(
    served: tuple[str, Any],
) -> None:
    url, _ = served

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        await b.put_files(vm, {"/work/request.json": b"{}"})
        with pytest.raises(SandboxInfraError):
            await b.put_files(vm, {"/work/request.json": b"{evil}"})
        await b.destroy(vm.vm_id)

    asyncio.run(go())


def test_e13_2_the_agent_refuses_to_start_without_a_real_token(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        load_agent().Agent(tmp_path, "short")


def test_e13_2_a_vm_never_sees_the_agents_secrets(
    served: tuple[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SANDBOX_AGENT_TOKEN", TOKEN)
    url, _ = served

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        done = await ex(b, vm, *sh("import os;print(sorted(os.environ))"))
        assert "SANDBOX_AGENT_TOKEN" not in done.stdout.decode()
        await b.destroy(vm.vm_id)

    asyncio.run(go())


def test_e13_2_unauthenticated_requests_never_reach_a_route(
    served: tuple[str, Any],
) -> None:
    url, _ = served
    assert httpx.get(f"{url}/v1/vms").status_code == 401
    assert httpx.post(f"{url}/v1/vms", json={}).status_code == 401
    assert httpx.get(f"{url}/v1/runtime").status_code == 401


def test_e13_2_a_background_child_does_not_outlive_its_command(served: tuple[str, Any]) -> None:
    """A command that exits 0 after starting a daemon must not leave the daemon behind: it would
    keep running after `destroy`, in a container that the next run shares."""
    url, _ = served
    spawn = (
        "import subprocess,sys;"
        "p=subprocess.Popen(['sleep','601']);"
        "open(sys.argv[1],'w').write(str(p.pid))"
    )
    pids: list[int] = []

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        await b.put_files(vm, {"/work/seed": b"x"})
        done = await ex(b, vm, "python3", "-c", spawn, "/work/child.pid")
        assert done.exit_code == 0 and not done.timed_out
        recorded = await b.get_file(vm, "/work/child.pid", max_bytes=20)
        assert recorded
        pids.append(int(recorded))
        await b.destroy(vm.vm_id)

    asyncio.run(go())
    for _ in range(40):
        try:
            os.kill(pids[0], 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(pids[0], 9)  # do not leave it behind for the next run of the suite
    raise AssertionError("a background child outlived its command")


@pytest.fixture
def agent_process(tmp_path: Path) -> Iterator[tuple[str, int]]:
    """The agent as its own process, the way the container runs it (not a thread of the test)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    env = {
        **os.environ,
        "SANDBOX_AGENT_TOKEN": TOKEN,
        "SANDBOX_AGENT_PORT": str(port),
        "SANDBOX_AGENT_HOST": "127.0.0.1",
        "SANDBOX_AGENT_ROOT": str(tmp_path / "vms"),
        "SANDBOX_AGENT_NPROC": "0",
    }
    proc = subprocess.Popen([sys.executable, "-I", str(AGENT_PATH)], env=env)  # noqa: S603
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        yield f"http://127.0.0.1:{port}", proc.pid
    finally:
        proc.kill()
        proc.wait()


def test_e13_2_test_code_cannot_read_the_agents_token_from_proc(
    agent_process: tuple[str, int],
) -> None:
    """The agent and the code it runs share a uid, so `/proc/<agent>/environ` is readable unless the
    agent marks itself non-dumpable. The token would let generated code drive the agent."""
    url, agent_pid = agent_process

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        probe = (
            f"p='/proc/{agent_pid}/';"
            "import sys\n"
            "for f in ('environ','mem'):\n"
            "    try:\n"
            "        d=open(p+f,'rb').read(4096)\n"
            f"        print('LEAK',f,{TOKEN!r}.encode() in d)\n"
            "    except OSError as e:\n"
            "        print('denied',f)\n"
        )
        done = await ex(b, vm, "python3", "-c", probe)
        out = done.stdout.decode()
        assert "LEAK" not in out and "denied environ" in out, out
        await b.destroy(vm.vm_id)

    asyncio.run(go())


def test_e13_2_runs_never_overlap_in_one_runner(agent_process: tuple[str, int]) -> None:
    """VM directories share a uid, so two live runs could read each other's files. One runner holds
    one run at a time; the second create waits its turn as a refused (retryable) infra error."""
    url, _ = agent_process

    async def go() -> None:
        b = backend(url)
        first = await b.create(spec())
        with pytest.raises(SandboxInfraError):
            await b.create(spec())
        await b.destroy(first.vm_id)
        await b.destroy((await b.create(spec())).vm_id)

    asyncio.run(go())


def test_e13_2_a_command_runs_with_the_runtimes_own_path(agent_process: tuple[str, int]) -> None:
    url, _ = agent_process

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        done = await ex(b, vm, "python3", "-c", "import os;print(os.environ['PATH'])")
        assert done.stdout.decode().strip() == os.environ["PATH"]
        await b.destroy(vm.vm_id)

    asyncio.run(go())


def test_e13_2_a_survivor_of_a_destroyed_run_is_swept(agent_process: tuple[str, int]) -> None:
    """A process that leaves its session escapes the group kill. `destroy` sweeps every other
    process of the sandbox uid, so it cannot reach the next run's files."""
    url, _ = agent_process
    daemon = (
        "import os,subprocess,sys;"
        "p=subprocess.Popen(['sleep','602'],start_new_session=True);"
        "open(sys.argv[1],'w').write(str(p.pid))"
    )
    pids: list[int] = []

    async def go() -> None:
        b = backend(url)
        vm = await b.create(spec())
        await b.put_files(vm, {"/work/seed": b"x"})
        await ex(b, vm, "python3", "-c", daemon, "/work/d.pid")
        recorded = await b.get_file(vm, "/work/d.pid", max_bytes=20)
        assert recorded
        pids.append(int(recorded))
        os.kill(pids[0], 0)  # it escaped the group kill and is alive now
        await b.destroy(vm.vm_id)

    asyncio.run(go())
    for _ in range(40):
        try:
            os.kill(pids[0], 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(pids[0], 9)
    raise AssertionError("a session-escaping child survived destroy")
