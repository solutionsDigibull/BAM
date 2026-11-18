"""WG-16 (#764): the SSRF guard behind Test connection (ADR 0030, decision 7), rule by rule.

No test makes a real call: the resolver and the transport are fakes, and the one test of the real
connection class replaces the socket and the TLS wrap.
"""

import ipaddress
import socket
from collections.abc import Sequence
from typing import Any

import pytest
from sbam_api.connections import ssrf
from sbam_api.connections.ssrf import (
    BlockedHost,
    CallTimeout,
    PeerMismatch,
    PinnedRequest,
    TlsFailure,
    Unreachable,
    UnsendableValue,
)
from sbam_contracts.connections import (
    AuthScheme,
    ConnectionTestConfig,
    ConnectionTestOutcome,
    ProbeMethod,
)

KEY = "fixture_only_not_a_real_1716"
PUBLIC = "93.184.216.34"
OUTCOME = ConnectionTestOutcome


class FakeResolver:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers) or [PUBLIC]
        self.asked: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> Sequence[str]:
        self.asked.append((host, port))
        return self.answers


class FakeTransport:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.status, self.error = status, error
        self.sent: list[PinnedRequest] = []

    def send(self, request: PinnedRequest) -> int:
        self.sent.append(request)
        if self.error is not None:
            raise self.error
        return self.status


def config(url: str = "https://api.example.com/v1/ping", **kw: Any) -> ConnectionTestConfig:
    return ConnectionTestConfig(url=url, **kw)


def run(
    url: str = "https://api.example.com/v1/ping",
    resolver: FakeResolver | None = None,
    transport: FakeTransport | None = None,
    value: str = KEY,
    **kw: Any,
) -> tuple[ssrf.ProbeResult, FakeResolver, FakeTransport]:
    res, tr = resolver or FakeResolver(), transport or FakeTransport()
    return ssrf.probe(config(url, **kw), value, resolve=res, transport=tr), res, tr


# --- which addresses are public -------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "127.255.255.254", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
        "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.127.255.254", "0.0.0.0", "0.1.2.3",  # noqa: S104 - a refused address, not a bind
        "224.0.0.1", "239.255.255.255", "240.0.0.1", "255.255.255.255", "192.0.0.8", "192.0.2.1",
        "198.18.0.1", "198.51.100.1", "203.0.113.1",
        "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1", "2001::1", "2001:db8::1",
        "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254", "::ffff:192.168.0.1",
        "64:ff9b::7f00:1", "64:ff9b::a00:1", "2002:7f00:1::1", "2002:a9fe:a9fe::1",
    ],
)  # fmt: skip
def test_wg16_non_public_addresses_are_refused(address: str) -> None:
    assert ssrf.is_public(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize(
    "address",
    ["93.184.216.34", "8.8.8.8", "1.1.1.1", "172.15.255.255", "172.32.0.1", "2606:4700:4700::1111",
     "::ffff:8.8.8.8", "64:ff9b::808:808", "2002:808:808::1"],
)  # fmt: skip
def test_wg16_public_addresses_are_allowed(address: str) -> None:
    assert ssrf.is_public(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/", "https://[::1]/", "https://10.1.2.3:8443/x", "https://169.254.169.254/latest",
        "https://[::ffff:127.0.0.1]/", "https://[::ffff:7f00:1]/", "https://[fe80::1]/",
        "https://100.64.1.1/", "https://0.0.0.0/", "https://[fd00::1]/",
    ],
)  # fmt: skip
def test_wg16_an_address_literal_that_is_not_public_is_blocked_and_nothing_is_sent(
    url: str,
) -> None:
    result, resolver, transport = run(url)
    assert result.outcome is OUTCOME.BLOCKED_HOST and result.http_status_class is None
    assert transport.sent == [] and resolver.asked == []


@pytest.mark.parametrize(
    "host",
    [
        "2130706433",  # decimal 127.0.0.1
        "0x7f000001",  # hex
        "0x7f.0.0.1",
        "0177.0.0.1",  # octal
        "017700000001",
        "127.1",  # short forms
        "127.0.1",
        "0",
        "2852039166",  # decimal 169.254.169.254
        "0xA9FEA9FE",
        "1.2.3",
        "4294967295",
    ],
)
def test_wg16_an_address_in_decimal_octal_hex_or_short_form_is_blocked(host: str) -> None:
    result, resolver, transport = run(f"https://{host}/")
    assert result.outcome is OUTCOME.BLOCKED_HOST
    assert transport.sent == [] and resolver.asked == []  # never even resolved


def test_wg16_a_public_literal_is_allowed_and_pinned_without_resolving() -> None:
    result, resolver, transport = run("https://93.184.216.34/ok")
    assert result.outcome is OUTCOME.OK and resolver.asked == []
    assert transport.sent[0].ip == PUBLIC


# --- DNS answers ----------------------------------------------------------------------------------


def test_wg16_one_private_answer_among_public_ones_blocks_the_whole_name() -> None:
    for answers in ((PUBLIC, "10.0.0.5"), ("10.0.0.5", PUBLIC), (PUBLIC, "::1"),
                    (PUBLIC, "::ffff:169.254.169.254")):  # fmt: skip
        result, _, transport = run(resolver=FakeResolver(*answers))
        assert result.outcome is OUTCOME.BLOCKED_HOST and transport.sent == []


def test_wg16_a_name_that_resolves_only_to_private_space_is_blocked() -> None:
    for answer in ("127.0.0.1", "169.254.169.254", "192.168.0.10", "100.64.0.9", "fc00::1"):
        result, _, transport = run(resolver=FakeResolver(answer))
        assert result.outcome is OUTCOME.BLOCKED_HOST and transport.sent == []


def test_wg16_an_unparseable_answer_is_blocked_and_no_answer_is_unreachable() -> None:
    assert run(resolver=FakeResolver("not-an-ip"))[0].outcome is OUTCOME.BLOCKED_HOST

    def nothing(host: str, port: int) -> Sequence[str]:
        return []

    result = ssrf.probe(config(), KEY, resolve=nothing, transport=FakeTransport())
    assert result.outcome is OUTCOME.UNREACHABLE


def test_wg16_a_name_that_does_not_resolve_is_unreachable() -> None:
    def gone(host: str, port: int) -> Sequence[str]:
        raise Unreachable("the name does not resolve")

    result = ssrf.probe(config(), KEY, resolve=gone, transport=FakeTransport())
    assert result.outcome is OUTCOME.UNREACHABLE


def test_wg16_the_name_is_resolved_once_and_the_call_goes_to_that_address() -> None:
    """Rebinding: a resolver that would answer something else next time is never asked again."""
    resolver = FakeResolver(PUBLIC, "93.184.216.35")
    result, _, transport = run(resolver=resolver)
    assert result.outcome is OUTCOME.OK
    assert resolver.asked == [("api.example.com", 443)]
    [sent] = transport.sent
    assert sent.ip == PUBLIC and sent.host == "api.example.com" and sent.port == 443


def test_wg16_the_host_name_is_normalised_and_the_port_is_kept() -> None:
    _, resolver, transport = run("https://API.Example.COM.:8443/x")
    assert resolver.asked == [("api.example.com", 8443)]
    assert transport.sent[0].host == "api.example.com" and transport.sent[0].port == 8443


# --- the URL --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/", "ftp://api.example.com/", "file:///etc/passwd", "gopher://x/",
        "https://user:pass@api.example.com/", "https://user@api.example.com/",
        "https://api.example.com#frag", "https://api.example.com\\@10.0.0.1/",
        "https://api.example.com/a b", "https://api.example.com/\n", "https://api.example.com/\x00",
        "https://exämple.com/", "https:///path", "https://:443/",
    ],
)  # fmt: skip
def test_wg16_a_url_that_is_not_plain_https_is_refused(url: str) -> None:
    with pytest.raises(BlockedHost):
        ssrf.parse_target(url)


def test_wg16_the_config_model_refuses_what_the_guard_refuses_first() -> None:
    for url in ("http://x.example.com/", "https://u:p@x.example.com/", "https://x.example.com/#a"):
        with pytest.raises(ValueError):
            config(url)


# --- how the key travels --------------------------------------------------------------------------


def test_wg16_a_bearer_key_goes_in_one_header_and_not_in_the_url() -> None:
    _, _, transport = run(auth=AuthScheme.BEARER)
    [sent] = transport.sent
    assert ("Authorization", f"Bearer {KEY}") in sent.headers
    assert KEY not in sent.path_and_query and sent.path_and_query == "/v1/ping"
    assert sum(KEY in v for _, v in sent.headers) == 1


def test_wg16_basic_and_named_header_auth() -> None:
    _, _, basic = run(auth=AuthScheme.BASIC)
    assert ("Authorization", f"Basic {KEY}") in basic.sent[0].headers
    _, _, named = run(auth=AuthScheme.HEADER, auth_name="X-Api-Key")
    assert ("X-Api-Key", KEY) in named.sent[0].headers and KEY not in named.sent[0].path_and_query


def test_wg16_a_query_key_is_one_encoded_parameter_next_to_the_existing_query() -> None:
    _, _, plain = run(auth=AuthScheme.QUERY, auth_name="api_key", value="a b&c=d/é")
    assert plain.sent[0].path_and_query == "/v1/ping?api_key=a%20b%26c%3Dd%2F%C3%A9"
    assert all("a b" not in v for _, v in plain.sent[0].headers)
    _, _, more = run("https://api.example.com/v1?x=1", auth=AuthScheme.QUERY, auth_name="k")
    assert more.sent[0].path_and_query.startswith("/v1?x=1&k=")


@pytest.mark.parametrize("value", ["a\r\nX-Injected: 1", "a\nb", "a\rb", "a\x00b", "a\x7fb"])
def test_wg16_a_value_that_would_split_a_header_is_never_sent(value: str) -> None:
    transport = FakeTransport()
    with pytest.raises(UnsendableValue):
        ssrf.probe(config(), value, resolve=FakeResolver(), transport=transport)
    assert transport.sent == []


def test_wg16_the_method_is_the_one_the_fde_chose() -> None:
    _, _, transport = run(method=ProbeMethod.HEAD)
    assert transport.sent[0].method == "HEAD"


def test_wg16_a_request_never_prints_its_key() -> None:
    _, _, transport = run()
    assert KEY not in repr(transport.sent[0]) and KEY not in str(transport.sent[0])


# --- what came back -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "outcome", "klass"),
    [
        (200, OUTCOME.OK, 2), (204, OUTCOME.OK, 2), (301, OUTCOME.REDIRECT, 3),
        (302, OUTCOME.REDIRECT, 3), (307, OUTCOME.REDIRECT, 3), (401, OUTCOME.UNAUTHORIZED, 4),
        (403, OUTCOME.UNAUTHORIZED, 4), (404, OUTCOME.NOT_FOUND, 4), (410, OUTCOME.NOT_FOUND, 4),
        (429, OUTCOME.RATE_LIMITED, 4), (500, OUTCOME.SERVER_ERROR, 5),
        (503, OUTCOME.SERVER_ERROR, 5),
        (400, OUTCOME.UNREACHABLE, 4), (405, OUTCOME.UNREACHABLE, 4), (422, OUTCOME.UNREACHABLE, 4),
    ],
)  # fmt: skip
def test_wg16_the_status_class_becomes_a_clear_outcome(
    status: int, outcome: ConnectionTestOutcome, klass: int
) -> None:
    result, _, _ = run(transport=FakeTransport(status))
    assert (result.outcome, result.http_status_class) == (outcome, klass)


def test_wg16_only_a_2xx_is_a_pass() -> None:
    for status in range(100, 600):
        passed = ssrf.outcome_of(status) is OUTCOME.OK
        assert passed == (200 <= status < 300)


def test_wg16_a_redirect_is_a_result_and_is_never_followed() -> None:
    transport = FakeTransport(302)
    result, _, _ = run(transport=transport)
    assert result.outcome is OUTCOME.REDIRECT and len(transport.sent) == 1


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (CallTimeout(), OUTCOME.TIMEOUT),
        (TlsFailure(), OUTCOME.TLS_ERROR),
        (Unreachable("down"), OUTCOME.UNREACHABLE),
        (PeerMismatch("moved"), OUTCOME.BLOCKED_HOST),
    ],
)
def test_wg16_a_failed_call_is_an_outcome_not_an_error(
    error: Exception, outcome: ConnectionTestOutcome
) -> None:
    assert run(transport=FakeTransport(error=error))[0].outcome is outcome


def test_wg16_every_message_is_fixed_and_holds_neither_the_key_nor_the_target() -> None:
    for outcome in ConnectionTestOutcome:
        text = ssrf.MESSAGES[outcome]
        assert text and KEY not in text and "example.com" not in text


# --- the real connection class (no network: socket and TLS are replaced) --------------------------


class FakeSocket:
    def __init__(self, peer: str) -> None:
        self.peer, self.closed = peer, False

    def getpeername(self) -> tuple[str, int]:
        return (self.peer, 443)

    def close(self) -> None:
        self.closed = True


def test_wg16_the_real_connection_drops_a_peer_that_is_not_the_pinned_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSocket("10.0.0.9")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: fake)
    conn = ssrf.PinnedHTTPSConnection("api.example.com", PUBLIC, 443, 1.0)
    with pytest.raises(PeerMismatch):
        conn.connect()
    assert fake.closed


def test_wg16_the_real_connection_connects_to_the_address_and_verifies_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSocket(PUBLIC)
    seen: dict[str, Any] = {}

    def create(address: tuple[str, int], timeout: float) -> FakeSocket:
        seen["address"] = address
        return fake

    monkeypatch.setattr(socket, "create_connection", create)
    conn = ssrf.PinnedHTTPSConnection("api.example.com", PUBLIC, 443, 1.0)

    class Wrapped:
        pass

    def wrap(sock: object, server_hostname: str) -> Wrapped:
        seen["sni"] = server_hostname
        return Wrapped()

    monkeypatch.setattr(conn.tls, "wrap_socket", wrap)
    conn.connect()
    assert seen == {"address": (PUBLIC, 443), "sni": "api.example.com"}  # the IP, not the name
    assert conn.tls.check_hostname and conn.tls.verify_mode.name == "CERT_REQUIRED"


def test_wg16_the_real_transport_turns_socket_failures_into_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = PinnedRequest(PUBLIC, "api.example.com", 443, "GET", "/", ())
    for raised, expected in (
        (TimeoutError(), CallTimeout),
        (ConnectionRefusedError(), Unreachable),
    ):

        def refuse(*a: object, error: Exception = raised, **k: object) -> None:
            raise error

        monkeypatch.setattr(socket, "create_connection", refuse)
        with pytest.raises(expected):
            ssrf.HttpsTransport(1.0).send(request)
