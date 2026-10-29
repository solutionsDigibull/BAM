"""E13.5: the mock definition contract: explicit answers only, no forwarding, no credentials."""

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.mocks import (
    FORBIDDEN_MODES,
    MAX_BODY_BYTES,
    MAX_RUN_STUBS,
    MAX_SEQUENCE,
    MockDefinition,
    MockRunSetup,
    definition_sha256,
)


def stub(**over: Any) -> dict[str, Any]:
    return {
        "id": "get-account",
        "method": "GET",
        "path": "/crm/accounts/{id}",
        "responses": [{"status": 200, "body": {"id": 42, "name": "Acme"}}],
        **over,
    }


def definition(*stubs: dict[str, Any], name: str = "crm") -> dict[str, Any]:
    return {"name": name, "stubs": list(stubs) or [stub()]}


def bad(data: dict[str, Any], *, match: str | None = None) -> None:
    with pytest.raises(ValidationError, match=match):
        MockDefinition.model_validate(data)


def test_e13_5_t00_a_plain_definition_is_valid_and_round_trips() -> None:
    parsed = MockDefinition.model_validate(definition())
    assert (
        parsed.model_dump(mode="json", exclude_none=True)["stubs"][0]["path"]
        == "/crm/accounts/{id}"
    )


@pytest.mark.parametrize("key", sorted(FORBIDDEN_MODES))
def test_e13_5_t06_a_forwarding_or_recording_key_is_refused_by_name(key: str) -> None:
    bad(definition(stub(**{key: "https://api.realcrm.com"})), match="never forward or record")
    bad({**definition(), key: "https://api.realcrm.com"}, match="never forward or record")
    bad(definition(stub(responses=[{"status": 200, key: True}])), match="never forward or record")


def test_e13_5_an_unknown_key_is_refused_too() -> None:
    bad(definition(stub(headers={"x": "y"})))


def test_e13_5_every_stub_needs_an_explicit_response() -> None:
    bad(definition(stub(responses=[])))
    bad(definition({k: v for k, v in stub().items() if k != "responses"}))


@pytest.mark.parametrize("status", [99, 600, 0, -1, "200", 200.5, None])
def test_e13_5_status_is_100_to_599(status: Any) -> None:
    bad(definition(stub(responses=[{"status": status}])))


@pytest.mark.parametrize("status", [100, 200, 204, 404, 503, 599])
def test_e13_5_status_boundaries_are_accepted(status: int) -> None:
    MockDefinition.model_validate(definition(stub(responses=[{"status": status}])))


def test_e13_5_a_body_over_one_mib_is_refused_and_one_at_the_limit_is_not() -> None:
    overhead = len(b'"x"')
    ok = {"status": 200, "body": "a" * (MAX_BODY_BYTES - overhead + 1 - 1)}
    MockDefinition.model_validate(definition(stub(responses=[ok])))
    bad(definition(stub(responses=[{"status": 200, "body": "a" * MAX_BODY_BYTES}])), match="larger")


def test_e13_5_a_sequence_is_at_most_ten_long() -> None:
    ok = [{"status": 503}] * (MAX_SEQUENCE - 1) + [{"status": 200}]
    MockDefinition.model_validate(definition(stub(responses=ok)))
    bad(definition(stub(responses=[*ok, {"status": 200}])))


@pytest.mark.parametrize(
    "path",
    [
        "crm/accounts",
        "/crm/../etc",
        "/crm//x",
        "/a?b=1",
        "/a#frag",
        "http://x/y",
        "/health",
        "/_control/runs/x",
        "/" + "a" * 513,
        "/a b",
        "",
    ],
)
def test_e13_5_bad_paths_are_refused(path: str) -> None:
    bad(definition(stub(path=path)))


@pytest.mark.parametrize(
    "path", ["/", "/crm/accounts/{id}", "/v1/items/{a}/sub/{b}", "/a.b/c-d_e~f"]
)
def test_e13_5_good_paths_are_accepted(path: str) -> None:
    MockDefinition.model_validate(definition(stub(path=path)))


@pytest.mark.parametrize("method", ["get", "HEAD", "OPTIONS", "TRACE", "CONNECT", ""])
def test_e13_5_only_the_five_methods_are_stubbed(method: str) -> None:
    bad(definition(stub(method=method)))


# Built from pieces at run time: the source holds no literal that a secret scanner would flag.
SECRETS = [
    "-----BEGIN RSA " + "PRIVATE KEY-----\nabc",
    "AKIA" + "IOSFODNN7EXAMPLE",
    ".".join(
        ["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "dozjgNryP4J3jVmNHl0w5N_XgL0"]
    ),
    "Bearer " + "abcdefghijklmnopqrstuvwxyz0123",
    "sk_" + "live_" + "a" * 24,
    "gh" + "p_" + "a" * 36,
    "xox" + "b-" + "123456789012-abcdefghij",
]


@pytest.mark.parametrize("secret", SECRETS, ids=[f"kind{n}" for n in range(len(SECRETS))])
def test_e13_5_a_real_looking_credential_in_a_body_is_refused_anywhere_in_it(secret: str) -> None:
    for body in (secret, {"a": [{"b": secret}]}, {secret: 1}):
        bad(definition(stub(responses=[{"status": 200, "body": body}])), match="credential")


def test_e13_5_made_up_values_that_merely_look_like_data_pass() -> None:
    body = {"token": "abc", "password": "x", "email": "a@b.example", "bearer": "yes"}
    MockDefinition.model_validate(definition(stub(responses=[{"status": 200, "body": body}])))


def test_e13_5_ids_and_routes_are_unique() -> None:
    bad(definition(stub(), stub()), match="unique")
    bad(definition(stub(), stub(id="other")), match="same method and path")
    MockDefinition.model_validate(definition(stub(), stub(id="other", case="retry")))  # per case
    MockDefinition.model_validate(definition(stub(), stub(id="other", method="POST")))


def test_e13_5_a_run_has_unique_definition_names_and_ids_and_a_total_cap() -> None:
    a, b = definition(name="a"), definition(stub(path="/other"), name="b")
    with pytest.raises(ValidationError, match="names must be unique"):
        MockRunSetup.model_validate({"definitions": [a, a]})
    with pytest.raises(ValidationError, match="across the run"):
        MockRunSetup.model_validate({"definitions": [a, b]})  # both use id get-account
    many = [
        definition(*[stub(id=f"s{d}-{i}", path=f"/p{i}") for i in range(200)], name=f"d{d}")
        for d in range(3)
    ]
    assert MAX_RUN_STUBS == 400
    with pytest.raises(ValidationError, match="at most 400"):
        MockRunSetup.model_validate({"definitions": many})


def test_e13_5_the_hash_follows_the_content_not_the_key_order() -> None:
    one = MockDefinition.model_validate(definition())
    flipped = MockDefinition.model_validate(
        {"stubs": [dict(reversed(list(stub().items())))], "name": "crm"}
    )
    assert definition_sha256(one) == definition_sha256(flipped)
    changed = copy.deepcopy(definition())
    changed["stubs"][0]["responses"][0]["body"]["name"] = "Other"
    assert definition_sha256(MockDefinition.model_validate(changed)) != definition_sha256(one)
