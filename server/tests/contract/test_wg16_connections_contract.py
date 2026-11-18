"""#764 (ADR 0030): no response of the connections API can carry a stored value.

The generated JSON Schema of every response model is walked: a property whose name holds `value`,
`secret`, `ciphertext`, `password`, `passwd`, `credential` or `plaintext` fails the test, as does a
free-form object that could hold anything. The one request that carries a value is write-only.
"""

import json
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, Field
from sbam_contracts.connections import (
    RESPONSE_MODELS,
    AuthScheme,
    Blocker,
    BlockerReason,
    Connection,
    ConnectionSource,
    ConnectionStatus,
    ConnectionTestConfig,
    ConnectionUpdate,
    ConnectionValueIn,
    Readiness,
)
from sbam_contracts.registry import ROOTS
from sbam_contracts.review import ReviewAnswer  # noqa: F401 - registry import order

FORBIDDEN = ("value", "secret", "ciphertext", "password", "passwd", "credential", "plaintext")
ALLOWED = {"masked_tail", "value_set_at", "value_set_by", "value_version"}  # metadata, not a value
SCHEMA_ROOTS = dict(ROOTS)


def properties(schema: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every property of the schema and its `$defs`, with where it is."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(schema, dict):
        for name, sub in (schema.get("properties") or {}).items():
            found.append((f"{path}.{name}", sub))
            found += properties(sub, f"{path}.{name}")
        for key in ("$defs", "items", "anyOf", "allOf", "oneOf"):
            inner = schema.get(key)
            if isinstance(inner, dict):
                for def_name, sub in inner.items() if key == "$defs" else [(key, inner)]:
                    found += properties(sub, f"{path}.{def_name}")
            elif isinstance(inner, list):
                for sub in inner:
                    found += properties(sub, path)
    return found


def violations(model: type[BaseModel]) -> list[str]:
    """What in the model's schema could hold a stored value (empty for a safe response)."""
    schema = model.model_json_schema()
    found: list[str] = []
    for path, sub in properties(schema):
        leaf = path.rsplit(".", 1)[-1]
        if leaf in ALLOWED:
            continue
        if any(word in leaf.lower() for word in FORBIDDEN):
            found.append(f"{path}: a name that holds a value")
        if sub.get("additionalProperties") not in (None, False):
            found.append(f"{path}: an open object")
    if '"writeonly"' in json.dumps(schema).lower():
        found.append("a write-only field in a response")
    return found


@pytest.mark.parametrize("model", RESPONSE_MODELS, ids=lambda m: m.__name__)
def test_wg16_no_response_property_can_hold_a_value(model: type[BaseModel]) -> None:
    assert properties(model.model_json_schema()), model
    assert violations(model) == []


def test_wg16_the_walker_has_teeth() -> None:
    """A response that gained a value, a ciphertext, an open object or a write-only field is caught,
    nested or not."""

    class Plain(BaseModel):
        value: str

    class Cipher(BaseModel):
        ciphertext: bytes

    class Open(BaseModel):
        extra: dict[str, str]

    class Write(BaseModel):
        note: str = Field(json_schema_extra={"writeOnly": True})

    class Inner(BaseModel):
        client_secret: str

    class Nested(BaseModel):
        items: list[Inner]

    for bad in (Plain, Cipher, Open, Write, Nested):
        assert violations(bad), bad.__name__
    for good in (Connection, Readiness):
        assert violations(good) == []


def test_wg16_every_response_model_is_walked_and_registered_for_the_ts_types() -> None:
    registered = {path for path in SCHEMA_ROOTS if path.startswith("connections/")}
    assert registered == {
        "connections/connection",
        "connections/connection_list",
        "connections/value_in",
        "connections/value_result",
        "connections/update",
        "connections/test_result",
        "connections/readiness",
    }
    assert {m.__name__ for m in RESPONSE_MODELS} == {
        "Connection",
        "ConnectionList",
        "ConnectionValueResult",
        "ConnectionTestResult",
        "Readiness",
    }
    assert {SCHEMA_ROOTS[p] for p in registered} >= set(RESPONSE_MODELS)


def test_wg16_the_only_request_with_a_value_marks_it_write_only_and_secret() -> None:
    schema = ConnectionValueIn.model_json_schema()
    value = schema["properties"]["value"]
    assert value["writeOnly"] is True and value["format"] == "password"
    assert set(schema["properties"]) == {"value"}
    request = ConnectionValueIn.model_validate({"value": "sk_live_abc"})
    assert "sk_live_abc" not in repr(request) and "sk_live_abc" not in str(request)
    assert "sk_live_abc" not in request.model_dump_json()  # a dump is masked too


def test_wg16_the_update_request_has_no_value_field_and_refuses_a_pasted_key_in_a_note() -> None:
    assert not any(w in n for n in ConnectionUpdate.model_fields for w in ("value", "secret"))
    with pytest.raises(ValueError, match="secret value"):
        ConnectionUpdate(purpose="use sk-live-0123456789abcdef")
    with pytest.raises(ValueError, match="secret value"):
        ConnectionUpdate(where_to_get="Bearer abcdef")
    assert ConnectionUpdate(purpose="Ledger API key").purpose == "Ledger API key"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/health",
        "ftp://api.example.com/",
        "https://user:pw@api.example.com/",
        "https://user@api.example.com/",
        "https://api.example.com/#frag",
        "https://api.example.com/a b",
        "https://api.example.com:notaport/",
        "https:///nohost",
        "//api.example.com/",
    ],
)
def test_wg16_a_test_url_is_https_without_credentials(url: str) -> None:
    with pytest.raises(ValueError):
        ConnectionTestConfig(url=url)


def test_wg16_a_header_or_query_auth_needs_a_name_and_the_others_take_none() -> None:
    assert ConnectionTestConfig(
        url="https://api.example.com/", auth=AuthScheme.HEADER, auth_name="X-Key"
    )
    assert ConnectionTestConfig(url="https://api.example.com/", auth=AuthScheme.BEARER)
    with pytest.raises(ValueError):
        ConnectionTestConfig(url="https://api.example.com/", auth=AuthScheme.HEADER)
    with pytest.raises(ValueError):
        ConnectionTestConfig(url="https://api.example.com/", auth=AuthScheme.BEARER, auth_name="X")


def test_wg16_a_connection_answers_status_and_a_short_tail_only() -> None:
    entry = Connection(
        name="LEDGER_API_KEY",
        source=ConnectionSource.ISATVON,
        required=True,
        status=ConnectionStatus.SET,
    )
    assert entry.masked_tail == "" and entry.row_version == 0
    with pytest.raises(ValueError):
        Connection(
            name="LEDGER_API_KEY",
            source=ConnectionSource.ISATVON,
            required=True,
            status=ConnectionStatus.SET,
            masked_tail="whole-key-here",
        )
    with pytest.raises(ValueError):  # extra fields, such as a `value`, are refused
        Connection.model_validate(
            {"name": "A_KEY", "source": "FDE", "required": True, "status": "SET", "value": "x"}
        )


def test_wg16_ready_means_no_blockers() -> None:
    wf = UUID("00000000-0000-0000-0000-000000000001")
    assert Readiness(workflow_id=wf, ready=True).blockers == []
    blocker = Blocker(name="A_KEY", reason=BlockerReason.MISSING)
    assert Readiness(workflow_id=wf, ready=False, blockers=[blocker]).ready is False
    with pytest.raises(ValueError):
        Readiness(workflow_id=wf, ready=True, blockers=[blocker])
    with pytest.raises(ValueError):
        Readiness(workflow_id=wf, ready=False)
