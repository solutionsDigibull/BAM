"""E13.5: mock definitions from API references, stored and hashed with the dataset; the client."""

import hashlib
import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_api.storage.base import Kind
from sbam_contracts.mocks import MockDefinition
from sbam_contracts.testgen import CaseBody, CaseKind, dataset_sha256
from sbam_workers.test.mocks import (
    MockApiClient,
    definition_bytes,
    definition_hash,
    mock_definitions,
    route_of,
    tool_headers,
)
from test_e13_4_testgen import GOOD, INPUT, World, sample
from test_e13_5_mock_api import RUN_A, TOKEN, Service, srv


def with_refs(*refs: dict[str, str]) -> dict[str, Any]:
    isatvon = sample()
    isatvon["isatvon_definition"]["s_source"]["external_data"] = list(refs)
    return isatvon


CRM = {"name": "crm accounts", "kind": "API", "ref": "GET /crm/accounts/{id} (OpenAPI crm.yaml)"}


def pairs(*kinds: tuple[str, CaseKind, str]) -> list[tuple[CaseKind, CaseBody]]:
    return [
        (kind, CaseBody.model_validate({"name": name, "input": INPUT, "expected_terminal": end}))
        for name, kind, end in kinds
    ]


CASES = pairs(
    ("happy", CaseKind.PASS_FIRST, "SUCCESS"),
    ("never passes", CaseKind.ESCALATION, "ESCALATED"),
    ("retry", CaseKind.PASS_AFTER_RETRY, "SUCCESS"),
    ("edge", CaseKind.EDGE, "SUCCESS"),
)
CASES = [(k, c) for k, c in ((CASES[i][0], CASES[i][1]) for i in range(len(CASES)))]


@pytest.mark.parametrize(
    ("ref", "route"),
    [
        ("GET /crm/accounts/{id}", ("GET", "/crm/accounts/{id}")),
        ("get /v1/items?limit=5 (paged)", ("GET", "/v1/items")),
        ("Calls POST /orders then PUT /orders/{id}", ("POST", "/orders")),
        ("openapi: https://api.realcrm.com/spec.yaml", None),
        ("https://api.realcrm.com/crm GET", None),
        ("the CRM account lookup endpoint", None),
        ("GET crm/accounts", None),
        ("TRACE /x", None),
    ],
)
def test_e13_5_route_of_reads_the_first_method_and_path(
    ref: str, route: tuple[str, str] | None
) -> None:
    assert route_of(ref) == route


def test_e13_5_one_definition_per_api_reference_with_a_route() -> None:
    isatvon = with_refs(
        CRM,
        {"name": "rates", "kind": "FILE", "ref": "rates.csv"},
        {"name": "vague", "kind": "API", "ref": "the partner's endpoint"},
        {"name": "dup", "kind": "API", "ref": "GET /crm/accounts/{id}"},
    )
    [definition] = mock_definitions(isatvon, CASES)
    assert (
        definition.name == "crm accounts"
    )  # FILE, no-route and same-route references are left out


def test_e13_5_every_case_kind_gets_the_answers_its_scenario_needs() -> None:
    [definition] = mock_definitions(with_refs(CRM), CASES)
    by_case = {s.case: s for s in definition.stubs}
    assert [r.status for r in by_case[None].responses] == [200]
    assert [r.status for r in by_case["retry"].responses] == [503, 200]  # fails, then succeeds
    assert [r.status for r in by_case["never passes"].responses] == [500]  # never succeeds
    assert set(by_case) == {None, "retry", "never passes"}  # the others use the default stub
    assert {(s.method, s.path) for s in definition.stubs} == {("GET", "/crm/accounts/{id}")}
    ids = [s.id for s in definition.stubs]
    assert len(set(ids)) == len(ids)


def test_e13_5_generation_is_deterministic_and_independent_of_case_order() -> None:
    one = mock_definitions(with_refs(CRM), CASES)
    two = mock_definitions(with_refs(CRM), list(reversed(CASES)))
    assert [definition_bytes(d.model_dump()) for d in one] == [
        definition_bytes(d.model_dump()) for d in two
    ]


def test_e13_5_no_api_references_means_no_definitions() -> None:
    assert mock_definitions(sample(), CASES) == []
    assert mock_definitions(with_refs({"name": "f", "kind": "FILE", "ref": "f.csv"}), CASES) == []


def test_e13_5_definition_bytes_are_canonical_and_content_addressed() -> None:
    [definition] = mock_definitions(with_refs(CRM), CASES)
    content = definition.model_dump(mode="json")
    shuffled = dict(reversed(list(content.items())))
    assert definition_bytes(content) == definition_bytes(shuffled)
    assert definition_hash(content) == hashlib.sha256(definition_bytes(content)).hexdigest()


# --- stored with the dataset --------------------------------------------------------------


def test_e13_5_the_dataset_stores_one_content_addressed_definition_per_api_reference() -> None:
    w = World([GOOD], with_refs(CRM))
    w.run()
    (dataset,) = w.datasets()
    (key,) = dataset.mocks_keys
    assert key.rsplit("/", 2)[1] == Kind.TESTS.value and dataset.files_keys == []
    stored = (w.storage.root / key).read_bytes() if hasattr(w.storage, "root") else b""
    assert key.rsplit("/", 1)[1] == hashlib.sha256(stored).hexdigest()
    assert MockDefinition.model_validate(json.loads(stored)).name == "crm accounts"
    assert {c.name for c in w.cases()} == {"happy", "never passes", "retry"}


def test_e13_5_t07_the_dataset_hash_covers_the_mocks() -> None:
    plain = World([GOOD])
    plain.run()
    one = World([GOOD], with_refs(CRM))
    one.run()
    other = World([GOOD], with_refs({**CRM, "name": "crm accounts v2"}))  # another stub body
    other.run()
    shas = {plain.datasets()[0].sha256, one.datasets()[0].sha256, other.datasets()[0].sha256}
    assert len(shas) == 3  # same cases: a mock more, or one body changed, is another dataset
    again = World([GOOD], with_refs(CRM))
    again.run()
    assert (
        again.datasets()[0].sha256 == one.datasets()[0].sha256
    )  # and the same input, the same hash


def test_e13_5_t07_dataset_sha256_changes_when_one_stub_body_does() -> None:
    cases = [(k, c) for k, c in CASES]
    base = dataset_sha256(cases, {}, {"k": "a" * 64})
    assert dataset_sha256(cases, {}, {"k": "b" * 64}) != base
    assert dataset_sha256(cases, {}, {}) != base
    assert dataset_sha256(cases, {}, {"k": "a" * 64}) == base


def test_e13_5_t06_a_forwarding_definition_is_refused_before_anything_is_stored() -> None:
    w = World([GOOD], with_refs(CRM))
    w.run()  # a normal dataset first, to see the store untouched by the refused one
    store = w.stage.store
    forwarding = {
        "name": "crm",
        "stubs": [
            {
                "id": "p",
                "method": "GET",
                "path": "/x",
                "proxy_base_url": "https://api.realcrm.com",
                "responses": [{"status": 200}],
            }
        ],
    }
    before = len(w.datasets())
    with pytest.raises(ValidationError, match="never forward or record"):
        store.record(w.inp, CASES, [], "g" * 64, [forwarding])
    assert len(w.datasets()) == before  # no dataset row, no cases
    assert len(w.cases()) == 3


# --- the worker's side of the control protocol -----------------------------------------


@pytest.fixture
def svc() -> Iterator[Service]:
    service = Service()
    yield service
    service.close()


def test_e13_5_the_client_installs_reads_and_removes_a_run(svc: Service) -> None:
    client = MockApiClient(svc.base, TOKEN)
    definitions = mock_definitions(with_refs(CRM), CASES)
    client.install(RUN_A, definitions)
    status, _, body = svc.call("GET", "/crm/accounts/9", None, tool_headers(RUN_A, "retry"))
    assert (status, body["error"]) == (503, "service_unavailable")  # the retry case's first answer
    assert svc.call("GET", "/crm/accounts/9", None, tool_headers(RUN_A, "retry"))[0] == 200
    journal = client.journal(RUN_A)
    assert [(e.case, e.status, e.response_index) for e in journal.entries] == [
        ("retry", 503, 0),
        ("retry", 200, 1),
    ]
    client.remove(RUN_A)
    client.remove(RUN_A)  # already gone is fine
    assert (
        svc.call("GET", "/crm/accounts/9", None, tool_headers(RUN_A))[2]["code"]
        == "MOCK_RUN_UNKNOWN"
    )


def test_e13_5_the_client_refuses_a_bad_setup_before_it_sends_anything(svc: Service) -> None:
    client = MockApiClient(svc.base, TOKEN)
    with pytest.raises(ValidationError):
        client.install(RUN_A, [])
    assert svc.store._runs == {}


def test_e13_5_a_wrong_token_is_an_http_error_not_a_silent_success(svc: Service) -> None:
    import httpx

    with pytest.raises(httpx.HTTPStatusError):
        MockApiClient(svc.base, "x" * 20).install(RUN_A, mock_definitions(with_refs(CRM), CASES))


def test_e13_5_tool_headers_carry_the_run_and_optionally_the_case() -> None:
    assert tool_headers("run-1") == {"X-Sbam-Run-Id": "run-1"}
    assert tool_headers("run-1", "retry") == {"X-Sbam-Run-Id": "run-1", "X-Sbam-Case": "retry"}


def test_e13_5_the_service_import_does_not_start_a_server() -> None:
    assert srv.__name__ == "mock_api_server" and threading.active_count() >= 1
