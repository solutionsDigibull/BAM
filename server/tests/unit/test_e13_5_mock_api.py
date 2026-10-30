"""E13.5: the sbam-mock-api service, run in-process on an ephemeral port (no Docker, stdlib client).

Test IDs follow the issue: T01 reaches a stub, T03 unmatched, T04 sequence, T05 per-run isolation,
T02 (security) is the part this service owns: it never forwards, and a tool cannot reach the control
surface. The sandbox's egress policy (the other half of T02) is the sandbox adapter's.
"""

import copy
import importlib.util
import json
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts import mocks as contract

SERVER_PY = Path(__file__).resolve().parents[3] / "compose" / "docker" / "mock-api" / "server.py"
_spec = importlib.util.spec_from_file_location("mock_api_server", SERVER_PY)
assert _spec and _spec.loader
srv: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

TOKEN = "control-token-0123456789"  # noqa: S105 - a test value
RUN_A, RUN_B = "run-aaaaaaaa-0001", "run-bbbbbbbb-0002"


def stub(**over: Any) -> dict[str, Any]:
    return {
        "id": "get-account",
        "method": "GET",
        "path": "/crm/accounts/{id}",
        "responses": [{"status": 200, "body": {"id": 42, "name": "Acme"}}],
        **over,
    }


def setup(*stubs: dict[str, Any], name: str = "crm") -> dict[str, Any]:
    return {"definitions": [{"name": name, "stubs": list(stubs) or [stub()]}]}


class Service:
    def __init__(self, **limits: Any) -> None:
        self.store = srv.Mocks(**limits)
        self.http = srv.make_server("127.0.0.1", 0, self.store, TOKEN)
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], Any]:
        data = (
            None
            if body is None
            else (body if isinstance(body, bytes) else json.dumps(body).encode())
        )
        req = urllib.request.Request(  # noqa: S310 - the test client, http only
            self.base + path, data=data, method=method, headers=headers or {}
        )
        try:
            res = urllib.request.urlopen(req, timeout=5)  # noqa: S310 - 127.0.0.1
        except urllib.error.HTTPError as err:
            res = err
        raw = res.read()
        return (
            res.status,
            {k.lower(): v for k, v in res.headers.items()},
            json.loads(raw) if raw else None,
        )

    def control(self, method: str, run: str, body: Any = None, journal: bool = False) -> Any:
        path = f"/_control/runs/{run}" + ("/journal" if journal else "")
        return self.call(method, path, body, {"Authorization": f"Bearer {TOKEN}"})

    def setup(self, run: str, payload: dict[str, Any] | None = None) -> None:
        status, _, body = self.control("PUT", run, payload or setup())
        assert status == 204, body

    def tool(self, run: str, method: str, path: str, case: str | None = None, **extra: str) -> Any:
        headers = {contract.RUN_HEADER: run, **extra}
        if case:
            headers[contract.CASE_HEADER] = case
        return self.call(method, path, None, headers)

    def journal(self, run: str) -> list[dict[str, Any]]:
        status, _, body = self.control("GET", run, journal=True)
        assert status == 200
        entries: list[dict[str, Any]] = body["entries"]
        return entries

    def raw(self, request: str) -> str:
        """Send a hand-written request and read the whole reply (HTTP/1.0: to the end)."""
        with socket.create_connection(
            ("127.0.0.1", self.http.server_address[1]), timeout=5
        ) as sock:
            sock.sendall(request.encode())
            chunks = []
            while chunk := sock.recv(4096):
                chunks.append(chunk)
            return b"".join(chunks).decode()

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture
def svc() -> Iterator[Service]:
    service = Service()
    yield service
    service.close()


def test_e13_5_health_needs_no_credentials(svc: Service) -> None:
    assert svc.call("GET", "/health")[::2] == (200, {"status": "ok"})


def test_e13_5_t01_a_tool_reaches_the_stub_and_the_journal_says_which(svc: Service) -> None:
    svc.setup(RUN_A)
    status, headers, body = svc.tool(RUN_A, "GET", "/crm/accounts/42")
    assert (status, body) == (200, {"id": 42, "name": "Acme"})
    assert (
        headers["x-sbam-mock-stub"] == "get-account"
        and headers["content-type"] == "application/json"
    )
    assert svc.journal(RUN_A) == [
        {
            "n": 1,
            "case": None,
            "method": "GET",
            "path": "/crm/accounts/42",
            "stub_id": "get-account",
            "response_index": 0,
            "status": 200,
        }
    ]


def test_e13_5_t03_an_unmatched_request_is_404_mock_unmatched_and_journalled(svc: Service) -> None:
    svc.setup(RUN_A)
    status, headers, body = svc.tool(RUN_A, "GET", "/crm/unknown")
    assert status == 404 and body["code"] == "MOCK_UNMATCHED"
    assert headers["content-type"] == "application/problem+json"
    assert (
        svc.tool(RUN_A, "POST", "/crm/accounts/42")[2]["code"] == "MOCK_UNMATCHED"
    )  # wrong method
    assert [e["stub_id"] for e in svc.journal(RUN_A)] == ["UNMATCHED", "UNMATCHED"]
    assert svc.journal(RUN_A)[0]["response_index"] is None


def test_e13_5_t04_a_sequence_answers_503_then_200_and_the_last_repeats(svc: Service) -> None:
    retry = stub(
        id="retry",
        case="retry-case",
        responses=[{"status": 503, "body": {"e": 1}}, {"status": 200, "body": {"ok": True}}],
    )
    svc.setup(RUN_A, setup(stub(), retry))
    got = [svc.tool(RUN_A, "GET", "/crm/accounts/7", case="retry-case")[0] for _ in range(4)]
    assert got == [503, 200, 200, 200]
    assert [e["response_index"] for e in svc.journal(RUN_A)] == [0, 1, 1, 1]
    # another case, and a call with no case, see the plain stub and its own counter
    assert svc.tool(RUN_A, "GET", "/crm/accounts/7", case="other")[0] == 200
    assert svc.tool(RUN_A, "GET", "/crm/accounts/7")[0] == 200


def test_e13_5_t05_two_concurrent_runs_get_their_own_sequences_and_journals(svc: Service) -> None:
    def seq(first: int) -> dict[str, Any]:
        return setup(stub(responses=[{"status": first}, {"status": 200}]))

    svc.setup(RUN_A, seq(503))
    svc.setup(RUN_B, seq(429))
    seen: dict[str, list[int]] = {RUN_A: [], RUN_B: []}

    def worker(run: str) -> None:
        for _ in range(3):
            seen[run].append(svc.tool(run, "GET", "/crm/accounts/1")[0])

    threads = [threading.Thread(target=worker, args=(r,)) for r in (RUN_A, RUN_B)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen[RUN_A] == [503, 200, 200] and seen[RUN_B] == [429, 200, 200]
    assert [e["status"] for e in svc.journal(RUN_A)] == [503, 200, 200]
    assert [e["status"] for e in svc.journal(RUN_B)] == [429, 200, 200]


def test_e13_5_a_run_that_was_never_set_up_is_404_and_creates_nothing(svc: Service) -> None:
    status, _, body = svc.tool("run-unknown-0000", "GET", "/crm/accounts/1")
    assert status == 404 and body["code"] == "MOCK_RUN_UNKNOWN"
    assert svc.control("GET", "run-unknown-0000", journal=True)[0] == 404
    assert svc.store._runs == {}


@pytest.mark.parametrize("run", ["", "short", "has space 12345", "../../etc/passwd", "x" * 65])
def test_e13_5_a_missing_or_malformed_run_header_is_400(svc: Service, run: str) -> None:
    status, _, body = svc.call(
        "GET", "/crm/accounts/1", None, {contract.RUN_HEADER: run} if run else {}
    )
    assert status == 400 and body["code"] == "MOCK_RUN_REQUIRED"


def test_e13_5_matching_prefers_the_case_then_the_literal_path_and_ignores_the_query(
    svc: Service,
) -> None:
    plain = stub(id="templated", path="/items/{id}", responses=[{"status": 201}])
    literal = stub(id="literal", path="/items/special", responses=[{"status": 202}])
    mine = stub(id="mine", path="/items/{id}", case="c1", responses=[{"status": 203}])
    svc.setup(RUN_A, setup(plain, literal, mine))
    assert svc.tool(RUN_A, "GET", "/items/9?x=1")[0] == 201
    assert svc.tool(RUN_A, "GET", "/items/special?x=1")[0] == 202  # literal beats a template
    assert (
        svc.tool(RUN_A, "GET", "/items/special", case="c1")[0] == 203
    )  # the case's own beats both
    assert svc.tool(RUN_A, "GET", "/items/9/extra")[0] == 404  # a template is one segment
    assert svc.tool(RUN_A, "GET", "/items/")[0] == 404  # and not an empty one


def test_e13_5_no_content_statuses_send_no_body_and_a_missing_body_is_empty(svc: Service) -> None:
    svc.setup(
        RUN_A,
        setup(
            stub(path="/a", responses=[{"status": 204}]),
            stub(id="b", path="/b", responses=[{"status": 200}]),
        ),
    )
    assert svc.tool(RUN_A, "GET", "/a")[::2] == (204, None)
    assert svc.tool(RUN_A, "GET", "/b")[::2] == (200, None)


def test_e13_5_authorization_headers_and_request_bodies_are_accepted_and_ignored(
    svc: Service,
) -> None:
    svc.setup(RUN_A, setup(stub(method="POST", path="/crm/accounts")))
    headers = {contract.RUN_HEADER: RUN_A, "Authorization": "Bearer whatever", "Cookie": "s=1"}
    assert svc.call("POST", "/crm/accounts", {"name": "x"}, headers)[0] == 200
    assert svc.journal(RUN_A)[0]["stub_id"] == "get-account"  # and the body was never recorded
    assert "name" not in json.dumps(svc.journal(RUN_A))


# --- safety ---------------------------------------------------------------------------------------


def test_e13_5_t02_the_service_never_opens_an_outbound_connection(
    svc: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc.setup(RUN_A)
    targets: list[Any] = []
    real = socket.socket.connect

    def spy(self: socket.socket, address: Any) -> Any:
        targets.append(address)
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    svc.tool(RUN_A, "GET", "/crm/accounts/1")
    svc.tool(RUN_A, "GET", "/nothing")
    svc.call("GET", "/health")
    # every connect() here was the test client dialling the service: nothing else was dialled
    assert set(targets) == {("127.0.0.1", svc.http.server_address[1])}


def test_e13_5_t02_the_source_has_no_client_library_or_forwarding_mode() -> None:
    text = SERVER_PY.read_text(encoding="utf-8")
    for needle in (
        "urllib.request",
        "http.client",
        "requests",
        "httpx",
        "create_connection",
        "ssl",
        "socket",
    ):
        assert f"import {needle}" not in text and f"from {needle}" not in text, needle
    assert "proxy_base_url" in srv.FORBIDDEN_MODES  # refused, never implemented


def test_e13_5_an_absolute_form_request_is_refused_not_matched(svc: Service) -> None:
    svc.setup(RUN_A)
    reply = svc.raw(
        f"GET http://api.realcrm.com/x HTTP/1.0\r\n{contract.RUN_HEADER}: {RUN_A}\r\n\r\n"
    )
    assert reply.startswith("HTTP/1.0 400") and "MOCK_BAD_TARGET" in reply
    assert svc.journal(RUN_A) == []


@pytest.mark.parametrize(
    "auth", [None, "Bearer wrong-token-0123456789", "Bearer ", "control-token-0123456789"]
)
def test_e13_5_the_control_surface_refuses_anyone_without_the_token(
    svc: Service, auth: str | None
) -> None:
    svc.setup(RUN_A)
    headers = {"Authorization": auth} if auth is not None else {}
    for method, path, body in (
        ("PUT", f"/_control/runs/{RUN_A}", setup()),
        ("DELETE", f"/_control/runs/{RUN_A}", None),
        ("GET", f"/_control/runs/{RUN_A}/journal", None),
        ("GET", "/_control/anything", None),
    ):
        status, _, problem = svc.call(method, path, body, {**headers, contract.RUN_HEADER: RUN_A})
        if (
            auth == "Bearer wrong-token-0123456789"
            or auth is None
            or auth == "Bearer "
            or auth.startswith("control")
        ):
            assert status == 401 and problem["code"] == "UNAUTHENTICATED", (method, path)
    assert svc.journal(RUN_A) == [] and RUN_A in svc.store._runs  # nothing was changed or removed


def test_e13_5_a_tool_cannot_stub_the_control_paths_or_read_another_runs_journal(
    svc: Service,
) -> None:
    svc.setup(RUN_A)
    svc.tool(RUN_A, "GET", "/crm/accounts/1")
    assert (
        svc.call("GET", f"/_control/runs/{RUN_A}/journal", None, {contract.RUN_HEADER: RUN_A})[0]
        == 401
    )
    bad = setup(stub(path="/_control/runs/x"))
    assert svc.control("PUT", RUN_B, bad)[0] == 422
    assert svc.control("PUT", RUN_B, setup(stub(path="/health")))[0] == 422


def test_e13_5_t06_the_service_refuses_a_forwarding_stub_too(svc: Service) -> None:
    bad = setup(stub(proxy_base_url="https://api.realcrm.com"))
    status, _, body = svc.control("PUT", RUN_A, bad)
    assert status == 422 and "never forward or record" in json.dumps(body)
    assert RUN_A not in svc.store._runs


def test_e13_5_a_failed_replacement_leaves_the_old_setup_in_place(svc: Service) -> None:
    svc.setup(RUN_A)
    assert svc.control("PUT", RUN_A, setup(stub(path="nope")))[0] == 422
    assert svc.tool(RUN_A, "GET", "/crm/accounts/1")[0] == 200


def test_e13_5_putting_a_run_again_starts_it_fresh(svc: Service) -> None:
    svc.setup(RUN_A, setup(stub(responses=[{"status": 503}, {"status": 200}])))
    svc.tool(RUN_A, "GET", "/crm/accounts/1")
    svc.setup(RUN_A, setup(stub(responses=[{"status": 503}, {"status": 200}])))
    assert svc.journal(RUN_A) == []
    assert svc.tool(RUN_A, "GET", "/crm/accounts/1")[0] == 503  # the counter restarted


def test_e13_5_delete_forgets_the_run(svc: Service) -> None:
    svc.setup(RUN_A)
    assert svc.control("DELETE", RUN_A)[0] == 204
    assert svc.tool(RUN_A, "GET", "/crm/accounts/1")[2]["code"] == "MOCK_RUN_UNKNOWN"
    assert svc.control("DELETE", RUN_A)[0] == 404


def test_e13_5_limits_bodies_runs_journal_and_lifetime() -> None:
    small = Service(max_runs=2, journal_max=3, run_ttl=100)
    try:
        small.setup(RUN_A)
        small.setup(RUN_B)
        assert small.control("PUT", "run-cccccccc-0003", setup())[0] == 429  # full
        assert small.control("PUT", RUN_A, setup())[0] == 204  # replacing is not growing
        for _ in range(5):
            small.tool(RUN_A, "GET", "/crm/accounts/1")
        _, _, body = small.control("GET", RUN_A, journal=True)
        assert [e["n"] for e in body["entries"]] == [3, 4, 5] and body["dropped"] == 2
        # an oversized body is refused from its Content-Length alone, before it is read
        control = f"PUT /_control/runs/{RUN_B} HTTP/1.0\r\nAuthorization: Bearer {TOKEN}\r\n"
        assert small.raw(
            f"{control}Content-Length: {srv.MAX_CONTROL_BYTES + 1}\r\n\r\n"
        ).startswith("HTTP/1.0 413")
        data = f"POST /crm/accounts HTTP/1.0\r\n{contract.RUN_HEADER}: {RUN_A}\r\n"
        assert small.raw(f"{data}Content-Length: {srv.MAX_BODY_BYTES + 1}\r\n\r\n").startswith(
            "HTTP/1.0 413"
        )
        # lifetime on an injected clock: the real monotonic clock is the host's uptime
        small.store.put(RUN_A, setup(), now=0.0)  # expires at 100
        answer = small.store.answer
        assert answer(RUN_A, None, "GET", "/crm/accounts/1", now=99.0) is not None
        assert answer(RUN_A, None, "GET", "/crm/accounts/1", now=100.0) is None  # expired
    finally:
        small.close()


def test_e13_5_bad_json_is_400_not_a_crash(svc: Service) -> None:
    status, _, body = svc.call(
        "PUT", f"/_control/runs/{RUN_A}", b"{nope", {"Authorization": f"Bearer {TOKEN}"}
    )
    assert status == 400 and body["code"] == "BAD_REQUEST"
    assert svc.call("GET", "/health")[0] == 200


def test_e13_5_the_service_needs_a_long_enough_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MOCK_CONTROL_TOKEN_FILE", raising=False)
    monkeypatch.setenv("MOCK_CONTROL_TOKEN", "short")
    with pytest.raises(SystemExit):
        srv.read_token()
    file = tmp_path / "token"
    file.write_text(TOKEN + "\n")
    monkeypatch.setenv("MOCK_CONTROL_TOKEN_FILE", str(file))
    assert srv.read_token() == TOKEN


# --- the service and the contract agree -------------------------------------------------------


def _bad(mutate: Any) -> dict[str, Any]:
    data = setup()
    mutate(data["definitions"][0]["stubs"][0], data)
    return data


# pieces, so the source holds no literal a secret scanner would flag
JWT = ".".join(
    ["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "dozjgNryP4J3jVmNHl0w5N_XgL0"]
)
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
CORPUS: list[tuple[str, dict[str, Any]]] = [
    ("plain", setup()),
    ("two stubs, two cases", setup(stub(), stub(id="o", case="c"))),
    ("sequence of ten", setup(stub(responses=[{"status": 503}] * 9 + [{"status": 200}]))),
    ("sequence of eleven", setup(stub(responses=[{"status": 503}] * 11))),
    ("status 99", setup(stub(responses=[{"status": 99}]))),
    ("status 600", setup(stub(responses=[{"status": 600}]))),
    ("status string", setup(stub(responses=[{"status": "200"}]))),
    ("status bool", setup(stub(responses=[{"status": True}]))),
    ("status float", setup(stub(responses=[{"status": 200.0}]))),
    ("no responses", setup(stub(responses=[]))),
    ("bad method", setup(stub(method="HEAD"))),
    ("lower method", setup(stub(method="get"))),
    ("path no slash", setup(stub(path="crm"))),
    ("path dotdot", setup(stub(path="/a/../b"))),
    ("path double slash", setup(stub(path="/a//b"))),
    ("path query", setup(stub(path="/a?b=1"))),
    ("path health", setup(stub(path="/health"))),
    ("path control", setup(stub(path="/_control/x"))),
    ("path long", setup(stub(path="/" + "a" * 600))),
    ("bad id", setup(stub(id="-x"))),
    ("long id", setup(stub(id="a" * 65))),
    ("empty case", setup(stub(case=""))),
    ("long case", setup(stub(case="c" * 121))),
    ("proxy key", setup(stub(proxy_base_url="https://x"))),
    ("record key upper", setup(stub(RECORD=True))),
    ("unknown key", setup(stub(headers={}))),
    (
        "jwt in body",
        setup(
            stub(
                responses=[
                    {
                        "status": 200,
                        "body": {"a": JWT},
                    }
                ]
            )
        ),
    ),
    (
        "aws key in key",
        setup(stub(responses=[{"status": 200, "body": {AWS_KEY: 1}}])),
    ),
    ("made-up token", setup(stub(responses=[{"status": 200, "body": {"token": "abc"}}]))),
    ("huge body", setup(stub(responses=[{"status": 200, "body": "a" * (1024 * 1024 + 8)}]))),
    ("dup id", setup(stub(), stub(path="/other"))),
    ("dup route", setup(stub(), stub(id="o"))),
    (
        "dup route across definitions",
        {
            "definitions": [
                {"name": "a", "stubs": [stub()]},
                {"name": "b", "stubs": [stub(id="o")]},
            ]
        },
    ),
    ("empty definitions", {"definitions": []}),
    ("empty name", {"definitions": [{"name": " ", "stubs": [stub()]}]}),
    ("no stubs", {"definitions": [{"name": "x", "stubs": []}]}),
    ("extra top key", {**setup(), "mode": "x"}),
    ("not an object", {"definitions": ["x"]}),
]


@pytest.mark.parametrize(("label", "data"), CORPUS, ids=[c[0] for c in CORPUS])
def test_e13_5_the_service_and_the_contract_accept_and_refuse_the_same_setups(
    label: str, data: dict[str, Any]
) -> None:
    try:
        contract.MockRunSetup.model_validate(copy.deepcopy(data))
        contract_ok = True
    except ValidationError:
        contract_ok = False
    assert (srv.validate_setup(copy.deepcopy(data)) == []) == contract_ok, label


def test_e13_5_the_service_and_the_contract_share_their_limits_and_patterns() -> None:
    assert [p.pattern for p in srv.CREDENTIAL_PATTERNS] == [
        p.pattern for p in contract.CREDENTIAL_PATTERNS
    ]
    assert srv.FORBIDDEN_MODES == contract.FORBIDDEN_MODES
    assert (srv.MAX_BODY_BYTES, srv.MAX_SEQUENCE, srv.MAX_STUBS, srv.MAX_RUN_STUBS) == (
        contract.MAX_BODY_BYTES,
        contract.MAX_SEQUENCE,
        contract.MAX_STUBS,
        contract.MAX_RUN_STUBS,
    )
    assert srv.RESERVED_PATHS == contract.RESERVED_PATHS


def test_e13_5_the_journal_keeps_only_a_prefix_of_a_very_long_path(svc: Service) -> None:
    svc.setup(RUN_A)
    long = "/crm/" + "a" * 5000
    assert svc.tool(RUN_A, "GET", long)[0] == 404
    [entry] = svc.journal(RUN_A)
    assert len(entry["path"]) == srv.JOURNAL_PATH_CHARS and entry["stub_id"] == "UNMATCHED"
