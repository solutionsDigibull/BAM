"""The HTTP egress reader: only a 200 with valid events of this VM is evidence. Everything else is
EGRESS_LOG_UNAVAILABLE, never an empty list (which would mean "nothing was attempted")."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sbam_contracts.sandbox import EgressEvent, EgressSource, EgressVerdict, InfraReason
from sbam_workers.sandbox.backend import SandboxInfraError
from sbam_workers.sandbox.egress import MAX_EVENTS, HttpEgressReader
from sbam_workers.sandbox.factory import make_sandbox, problem
from sbam_workers.settings import WorkerSettings

VM = "a" * 32
TOKEN = "t" * 24
AT = datetime(2026, 11, 7, tzinfo=UTC)


def line(vm: str = VM, host: str = "api.example.com") -> str:
    return EgressEvent(
        vm_id=vm,
        at=AT,
        dst_host=host,
        dst_port=443,
        verdict=EgressVerdict.ALLOW,
        source=EgressSource.HOST_TAP,
    ).model_dump_json()


def reader(handler: Any) -> HttpEgressReader:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpEgressReader("http://collector:29000/", TOKEN, client=client)


def read(r: HttpEgressReader, vm: str = VM) -> list[EgressEvent]:
    return asyncio.run(r(vm))


def test_a_200_returns_the_events_and_sends_the_token_to_this_vms_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=line() + "\n" + line(host="b.example.com") + "\n")

    events = read(reader(handler))
    assert [e.dst_host for e in events] == ["api.example.com", "b.example.com"]
    assert str(seen[0].url) == f"http://collector:29000/egress/{VM}"  # no double slash
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_an_empty_200_is_the_one_way_to_say_nothing_was_attempted() -> None:
    assert read(reader(lambda r: httpx.Response(200, text=""))) == []


@pytest.mark.parametrize("status", [401, 404, 409, 500, 503, 302])
def test_any_other_status_is_no_evidence_never_an_empty_list(status: int) -> None:
    with pytest.raises(SandboxInfraError) as caught:
        read(reader(lambda r: httpx.Response(status, text="")))
    assert caught.value.reason is InfraReason.EGRESS_LOG_UNAVAILABLE
    assert TOKEN not in str(caught.value)


def test_a_dead_collector_or_a_timeout_is_no_evidence() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to 172.25.0.1")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    for handler in (refused, slow):
        with pytest.raises(SandboxInfraError) as caught:
            read(reader(handler))
        assert caught.value.reason is InfraReason.EGRESS_LOG_UNAVAILABLE
        assert "172.25.0.1" not in str(caught.value)  # no host in the reason


def test_a_body_that_is_not_events_is_no_evidence_not_a_skipped_line() -> None:
    for body in ("not json\n", line() + "\n{broken\n", '{"vm_id": 1}\n'):
        with pytest.raises(SandboxInfraError) as caught:
            read(reader(lambda r, b=body: httpx.Response(200, text=b)))
        assert caught.value.reason is InfraReason.EGRESS_LOG_UNAVAILABLE


def test_an_event_of_another_vm_is_no_evidence() -> None:
    with pytest.raises(SandboxInfraError):
        read(reader(lambda r: httpx.Response(200, text=line(vm="b" * 32) + "\n")))


def test_a_vm_id_that_could_climb_the_path_is_refused_before_any_request() -> None:
    called: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(1)
        return httpx.Response(200, text="")

    for bad in ("../admin", "a/b", "a b", "", "a?b=1"):
        with pytest.raises(SandboxInfraError):
            read(reader(handler), bad)
    assert called == []


def test_the_event_count_is_capped() -> None:
    body = "\n".join(line() for _ in range(MAX_EVENTS + 50)) + "\n"
    assert len(read(reader(lambda r: httpx.Response(200, text=body)))) == MAX_EVENTS


# --- wiring ---------------------------------------------------------------------------------


def settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> WorkerSettings:
    for key in (
        "SANDBOX_MODE", "CUBE_API_URL", "CUBE_API_KEY", "CUBE_PROXY_NODE_IP", "SBAM_ENV",
        "SANDBOX_EGRESS_LOG_PATH", "SANDBOX_EGRESS_URL", "SANDBOX_EGRESS_TOKEN",
    ):  # fmt: skip
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    for key, value in {
        "SANDBOX_MODE": "CUBESANDBOX",
        "CUBE_API_URL": "http://cube:3000",
        "CUBE_PROXY_NODE_IP": "10.0.0.9",
        **env,
    }.items():
        monkeypatch.setenv(key, value)
    return WorkerSettings()  # type: ignore[call-arg]


def test_the_collector_url_needs_its_token_and_then_makes_an_http_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert problem(settings(monkeypatch)) == "SANDBOX_EGRESS_LOG_PATH not set"
    url_only = settings(monkeypatch, SANDBOX_EGRESS_URL="http://c:29000")
    assert problem(url_only) == "SANDBOX_EGRESS_LOG_PATH, SANDBOX_EGRESS_TOKEN not set"
    both = settings(monkeypatch, SANDBOX_EGRESS_URL="http://c:29000", SANDBOX_EGRESS_TOKEN=TOKEN)
    assert problem(both) is None
    assert isinstance(make_sandbox(both).egress_reader, HttpEgressReader)


def test_the_collector_url_wins_over_a_leftover_file_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A placeholder path was set only to pass the startup check; it must not shadow the URL."""
    both = settings(
        monkeypatch,
        SANDBOX_EGRESS_LOG_PATH="/tmp/egress-placeholder.jsonl",  # noqa: S108
        SANDBOX_EGRESS_URL="http://c:29000",
        SANDBOX_EGRESS_TOKEN=TOKEN,
    )
    assert isinstance(make_sandbox(both).egress_reader, HttpEgressReader)


def test_the_file_is_used_when_there_is_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from sbam_workers.sandbox.egress import JsonlEgressReader

    only_file = settings(monkeypatch, SANDBOX_EGRESS_LOG_PATH="/var/e.jsonl")
    assert isinstance(make_sandbox(only_file).egress_reader, JsonlEgressReader)
