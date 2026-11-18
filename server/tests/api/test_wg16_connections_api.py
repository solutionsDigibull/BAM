"""WG-16 (#764): the connections routes through the real app, auth, SQL repositories and audit.

T01 no read-back on any route; T03 Test connection says clearly when the key is rejected; T04 Commit
is blocked while a required secret is missing; T05 the export names only; T06 no value in a log or
an audit row. The outbound call is a fake (the resolver and the transport are mocked): no test here
makes a real request.
"""

import json
import logging
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sbam_api.audit.models import AuditLog
from sbam_api.connections import ssrf
from sbam_api.connections.models import ConnectionRow
from sbam_api.isatvon.models import SubSkill
from sbam_api.workflows.models import Workflow
from sbam_contracts.connections import ConnectionTestConfig, ConnectionTestOutcome
from sqlalchemy import select
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_project_workspace import Harness

KEYS = "*k1:QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU="
SECRET = "fixture_only_not_a_real_1716"  # noqa: S105 - a test value, 28 characters
LEDGER, MAIL = "LEDGER_API_KEY", "MAIL_TOKEN"
WORKFLOW = uuid.UUID("0190f3a2-7b1c-7d4e-8f00-0000000000a1")
TEST_URL = "https://ledger.example.com/v1/ping"
Calls = list[ssrf.PinnedRequest]


class Net:
    """The fake outside world: what the service answers and what was sent."""

    def __init__(self) -> None:
        self.status = 200
        self.sent: Calls = []
        self.resolved: list[str] = []
        self.answer = "93.184.216.34"

    def send(self, request: ssrf.PinnedRequest) -> int:
        self.sent.append(request)
        return self.status

    def resolve(self, host: str, port: int) -> list[str]:
        self.resolved.append(host)
        return [self.answer]

    def probe(self, config: ConnectionTestConfig, value: str) -> ssrf.ProbeResult:
        return ssrf.probe(config, value, resolve=self.resolve, transport=self)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    bind = harness.sessions.kw["bind"]
    for model in (ConnectionRow, Workflow, SubSkill):
        model.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
    state = harness.client.app.state  # type: ignore[attr-defined]
    state.settings.secrets_keys = SecretStr(KEYS)
    state.connection_net = Net()
    state.connection_probe = state.connection_net.probe
    yield harness


def net(h: Harness) -> Net:
    found: Net = h.client.app.state.connection_net  # type: ignore[attr-defined]
    return found


def seed_workflow(h: Harness, *secrets: str) -> None:
    with h.sessions() as s:
        s.add(
            Workflow(
                id=WORKFLOW,
                tenant_id=TENANT_A,
                project_id=uuid.UUID(h.project),
                sow_version_id=uuid.uuid4(),
                major=1,
                version="1.0.0",
                name="Ledger",
                state="FROZEN",
            )
        )
        s.add(
            SubSkill(
                tenant_id=TENANT_A,
                project_id=uuid.UUID(h.project),
                macro_skill_id=uuid.uuid4(),
                workflow_id=WORKFLOW,
                skill_id="MS01.1_Ledger",
                skill_name="Ledger",
                order=1,
                isatvon={
                    "skill_metadata": {"skill_id": "MS01.1_Ledger", "skill_name": "Ledger"},
                    "isatvon_definition": {"v_variables": {"secrets": list(secrets)}},
                },
                sow_refs=[],
                content_sha256="0" * 64,
                state="DRAFT",
            )
        )
        s.commit()


def base(h: Harness) -> str:
    return f"/projects/{h.project}/connections"


def put_value(h: Harness, name: str = LEDGER, value: str = SECRET, *, token: str | None = None,
              if_match: str | None = None) -> Any:  # fmt: skip
    headers = {"if-match": if_match} if if_match else {}
    return h.call(
        "PUT", f"{base(h)}/{name}/value", token or h.fde(), json={"value": value}, headers=headers
    )


def etag_of(h: Harness, name: str = LEDGER) -> str:
    res = h.call("GET", f"{base(h)}/{name}", h.fde())
    assert res.status_code == 200, res.text
    return str(res.headers["etag"])


def set_test_target(h: Harness, name: str = LEDGER, **extra: Any) -> Any:
    body = {"test_config": {"url": TEST_URL}, **extra}
    return h.call(
        "PUT", f"{base(h)}/{name}", h.fde(), json=body, headers={"if-match": etag_of(h, name)}
    )


def every_route_once(h: Harness) -> list[Any]:
    """One call to each route (a write each, then the reads), the way an FDE uses them."""
    out = [put_value(h)]
    out.append(set_test_target(h))
    out.append(h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()))
    out.append(h.call("GET", base(h), h.fde()))
    out.append(h.call("GET", f"{base(h)}/{LEDGER}", h.fde()))
    wf = f"/projects/{h.project}/workflows/{WORKFLOW}/connections"
    out.append(h.call("GET", f"{wf}/readiness", h.fde()))
    out.append(h.call("GET", f"{wf}/env-example", h.fde()))
    out.append(h.call("DELETE", f"{base(h)}/{MAIL}/value", h.fde()))
    return out


# --- WG16-T01: nothing returns a value --------------------------------------------------------


def test_wg16_t01_no_route_returns_the_saved_value(h: Harness) -> None:
    seed_workflow(h, LEDGER, MAIL)
    responses = every_route_once(h)
    for res in responses:
        assert res.status_code in (200, 404), res.text
        assert SECRET not in res.text and SECRET not in json.dumps(dict(res.headers))
    saved = responses[0].json()["connection"]
    assert saved["masked_tail"] == SECRET[-4:] and saved["status"] == "SET"
    assert not {"value", "secret", "ciphertext"} & set(saved)


def test_wg16_t01_the_openapi_response_schemas_of_these_routes_hold_no_value_property(
    h: Harness,
) -> None:
    spec = h.client.get("/api/v1/openapi.json").json()
    schemas = spec["components"]["schemas"]
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in seen:
                    seen.add(name)
                    walk(schemas[name])
            for key, child in node.items():
                if key == "properties":
                    assert not {"value", "secret", "ciphertext", "password"} & set(child), name_of(
                        node
                    )
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    def name_of(node: dict[str, Any]) -> str:
        return str(node.get("title", "?"))

    checked = 0
    for path, item in spec["paths"].items():
        if "/connections" not in path:
            continue
        for operation in item.values():
            for response in operation.get("responses", {}).values():
                walk(response)
                checked += 1
    assert checked >= 8 and "ConnectionValueResult" in seen


def test_wg16_t01_the_value_is_not_in_the_database_in_plain_and_the_request_model_hides_it(
    h: Harness,
) -> None:
    put_value(h)
    with h.sessions() as s:
        [row] = list(s.scalars(select(ConnectionRow)))
    assert row.ciphertext is not None and SECRET.encode() not in row.ciphertext
    assert row.key_id == "k1" and row.masked_tail == SECRET[-4:]


# --- the routes -------------------------------------------------------------------------------


def test_wg16_the_list_merges_expected_names_and_stored_entries(h: Harness) -> None:
    seed_workflow(h, LEDGER, MAIL)
    put_value(h, "PAGER_KEY", "pager-value-123456")
    body = h.call("GET", base(h), h.fde()).json()
    assert body["project_id"] == h.project
    assert [(c["name"], c["source"], c["status"]) for c in body["connections"]] == [
        (LEDGER, "ISATVON", "MISSING"),
        (MAIL, "ISATVON", "MISSING"),
        ("PAGER_KEY", "FDE", "SET"),
    ]


def test_wg16_a_first_value_needs_no_if_match_and_the_next_needs_the_right_one(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    first = put_value(h)
    assert first.status_code == 200 and first.headers["etag"]
    problem(put_value(h), 428, "PRECONDITION_REQUIRED")
    problem(put_value(h, if_match='"99"'), 412, "PRECONDITION_FAILED")
    second = put_value(h, value=SECRET + "-2", if_match=first.headers["etag"])
    assert second.status_code == 200
    assert int(second.headers["etag"].strip('"')) == int(first.headers["etag"].strip('"')) + 1
    assert second.json()["connection"]["status"] == "SET"


def test_wg16_the_save_answer_lists_the_clean_ups_and_flags_an_unexpected_name(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    body = put_value(h, LEDGER, f'  "{SECRET}"  ').json()
    assert body["cleaned"] == ["TRIMMED", "UNQUOTED"] and body["unexpected_name"] is False
    typo = put_value(h, "LEDGER_API_KEYS").json()
    assert typo["unexpected_name"] is True and typo["did_you_mean"] == LEDGER


def test_wg16_a_bad_value_or_name_is_422_without_echo(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    for value in ("", "   ", "a\u0000b", "x" * 9000):
        res = put_value(h, value=value)
        problem(res, 422, "VALIDATION_ERROR")
        assert value.strip() == "" or value not in res.text
    for name in ("lower_case", "1ABC", "A-B", "A" * 129):
        assert h.call("GET", f"{base(h)}/{name}", h.fde()).status_code == 422
    assert h.call("PUT", f"{base(h)}/{LEDGER}/value", h.fde(), json={"value": 5}).status_code == 422
    assert (
        h.call("PUT", f"{base(h)}/{LEDGER}/value", h.fde(), json={"value": "x", "y": 1}).status_code
        == 422
    )


def test_wg16_clear_removes_the_value_and_an_unknown_name_is_404(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    saved = put_value(h)
    res = h.call(
        "DELETE", f"{base(h)}/{LEDGER}/value", h.fde(), headers={"if-match": saved.headers["etag"]}
    )
    assert res.status_code == 200 and res.json()["status"] == "MISSING"
    assert h.call("DELETE", f"{base(h)}/{LEDGER}/value", h.fde()).status_code == 200  # idempotent
    problem(h.call("DELETE", f"{base(h)}/NOT_A_THING/value", h.fde()), 404, "NOT_FOUND")
    problem(h.call("GET", f"{base(h)}/NOT_A_THING", h.fde()), 404, "NOT_FOUND")


def test_wg16_the_fde_edits_an_entry_and_an_isatvon_name_stays_required(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    problem(
        h.call("PUT", f"{base(h)}/{LEDGER}", h.fde(), json={"required": False}),
        422,
        "VALIDATION_ERROR",
    )
    ok = h.call(
        "PUT", f"{base(h)}/PAGER_KEY", h.fde(), json={"purpose": "Pages on-call", "required": True}
    )
    assert ok.status_code == 200
    assert ok.json()["required"] is True and ok.json()["purpose"] == "Pages on-call"
    problem(
        h.call("PUT", f"{base(h)}/PAGER_KEY", h.fde(), json={"purpose": "x"}),
        428,
        "PRECONDITION_REQUIRED",
    )
    problem(
        h.call(
            "PUT",
            f"{base(h)}/PAGER_KEY",
            h.fde(),
            json={"test_config": {"url": "http://x.example.com/"}},
        ),
        422,
        "VALIDATION_ERROR",
    )  # http is not allowed as a test target


def test_wg16_no_keyring_means_503_and_nothing_is_stored(h: Harness) -> None:
    h.client.app.state.settings.secrets_keys = None  # type: ignore[attr-defined]
    problem(put_value(h), 503, "SERVICE_UNAVAILABLE")
    with h.sessions() as s:
        assert list(s.scalars(select(ConnectionRow))) == []


# --- WG16-T03: Test connection ----------------------------------------------------------------


def test_wg16_t03_a_rejected_key_is_reported_clearly_and_does_not_count_as_tested(
    h: Harness,
) -> None:
    seed_workflow(h, LEDGER)
    put_value(h)
    set_test_target(h)
    net(h).status = 401
    res = h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outcome"] == "UNAUTHORIZED" and body["http_status_class"] == 4
    assert body["message"] == "The service rejected this key."
    assert body["connection"]["status"] == "TEST_FAILED"
    assert SECRET not in res.text
    [sent] = net(h).sent
    assert ("Authorization", f"Bearer {SECRET}") in sent.headers and sent.ip == "93.184.216.34"
    assert sent.host == "ledger.example.com"


def test_wg16_t03_a_passing_test_makes_it_tested_and_a_new_value_makes_it_untested(
    h: Harness,
) -> None:
    seed_workflow(h, LEDGER)
    saved = put_value(h)
    put_etag = set_test_target(h).headers["etag"]
    assert saved.status_code == 200
    ok = h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()).json()
    assert ok["outcome"] == "OK" and ok["connection"]["status"] == "TESTED"
    assert ok["connection"]["last_test_outcome"] == "OK"
    again = put_value(
        h,
        value=SECRET + "-new",
        if_match=h.call("GET", f"{base(h)}/{LEDGER}", h.fde()).headers["etag"],
    )
    assert again.json()["connection"]["status"] == "SET"
    assert put_etag  # the target edit gave an ETag


def test_wg16_t03_a_test_with_no_value_no_target_or_nothing_to_test_is_refused(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    problem(h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()), 404, "NOT_FOUND")  # no value
    put_value(h)
    problem(
        h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()), 422, "VALIDATION_ERROR"
    )  # no target
    h.call(
        "PUT",
        f"{base(h)}/{LEDGER}",
        h.fde(),
        json={"test_mode": "NONE"},
        headers={"if-match": etag_of(h)},
    )
    problem(h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()), 409, "CONFLICT")
    assert net(h).sent == []


def test_wg16_t03_a_private_target_is_blocked_before_anything_is_sent(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    put_value(h)
    set_test_target(h)
    net(h).answer = "169.254.169.254"  # the name resolves to the metadata service
    body = h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()).json()
    assert body["outcome"] == "BLOCKED_HOST" and body["connection"]["status"] == "TEST_FAILED"
    assert net(h).sent == []


def test_wg16_a_value_that_cannot_travel_in_a_header_is_422_and_nothing_is_sent(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    put_value(h, value="line-one-0123456789\nline-two-0123456789")  # a PEM-like value is storable
    set_test_target(h)
    problem(h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()), 422, "VALIDATION_ERROR")
    assert net(h).sent == []


def test_wg16_tests_are_limited_to_ten_a_minute_per_user(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    put_value(h)
    set_test_target(h)
    codes = [h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]
    limited = h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde())
    assert limited.headers["retry-after"].isdigit()


def test_wg16_every_test_and_write_is_audited_without_the_value(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    put_value(h)
    set_test_target(h)
    h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde())
    with h.sessions() as s:
        rows = list(s.scalars(select(AuditLog).where(AuditLog.entity_type == "project_connection")))
    assert [r.action for r in rows] == [
        "CONNECTION_VALUE_SET",
        "CONNECTION_UPDATED",
        "CONNECTION_TESTED",
    ]
    assert all(r.actor_id for r in rows) and rows[-1].after == {"name": LEDGER, "outcome": "OK"}
    assert SECRET not in json.dumps([r.after for r in rows]) and SECRET[-4:] not in json.dumps(
        [r.after for r in rows]
    )


# --- WG16-T04: Commit is blocked until every required value is set and tested -----------------


def test_wg16_t04_readiness_blocks_commit_until_every_required_secret_is_set_and_tested(
    h: Harness,
) -> None:
    seed_workflow(h, LEDGER, MAIL)
    url = f"/projects/{h.project}/workflows/{WORKFLOW}/connections/readiness"

    def ready() -> dict[str, Any]:
        res = h.call("GET", url, h.fde())
        assert res.status_code == 200, res.text
        return dict(res.json())

    first = ready()
    assert first["ready"] is False and first["workflow_id"] == str(WORKFLOW)
    assert {(b["name"], b["reason"]) for b in first["blockers"]} == {
        (LEDGER, "MISSING"),
        (MAIL, "MISSING"),
    }
    put_value(h, LEDGER)
    put_value(h, MAIL, "mail-token-value-1234")
    assert {b["reason"] for b in ready()["blockers"]} == {"NOT_TESTED"}
    for name in (LEDGER, MAIL):
        set_test_target(h, name)
        assert h.call("POST", f"{base(h)}/{name}/test", h.fde()).json()["outcome"] == "OK"
    done = ready()
    assert done["ready"] is True and done["blockers"] == []
    assert SECRET not in json.dumps(done)


def test_wg16_t04_a_workflow_of_another_project_has_no_readiness(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    other = f"/projects/{uuid.uuid4()}/workflows/{WORKFLOW}/connections/readiness"
    problem(h.call("GET", other, h.fde()), 404, "NOT_FOUND")
    unknown = f"/projects/{h.project}/workflows/{uuid.uuid4()}/connections/readiness"
    problem(h.call("GET", unknown, h.fde()), 404, "NOT_FOUND")


# --- WG16-T05: the export names only ----------------------------------------------------------


def test_wg16_t05_the_env_example_has_names_purposes_and_never_a_value(h: Harness) -> None:
    seed_workflow(h, LEDGER, MAIL)
    put_value(h)
    h.call("PUT", f"{base(h)}/{MAIL}", h.fde(), json={"where_to_get": "Mail admin console"})
    res = h.call(
        "GET", f"/projects/{h.project}/workflows/{WORKFLOW}/connections/env-example", h.fde()
    )
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    text = res.text
    assert f"\n{LEDGER}=\n" in text and f"\n{MAIL}=\n" in text
    assert "# Get it: Mail admin console" in text and "(required)" in text
    assert SECRET not in text and SECRET[-4:] not in text
    assert res.headers["x-content-type-options"] == "nosniff"


# --- WG16-T06: logs and audit rows ------------------------------------------------------------


def test_wg16_t06_no_value_reaches_a_log_line_an_audit_row_or_an_error_body(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    seed_workflow(h, LEDGER, MAIL)
    bodies = [r.text for r in every_route_once(h)]
    net(h).status = 401
    bodies.append(h.call("POST", f"{base(h)}/{LEDGER}/test", h.fde()).text)
    bodies.append(put_value(h, value="\u0000" + SECRET).text)  # refused
    bodies.append(put_value(h, if_match='"99"').text)  # stale
    with h.sessions() as s:
        audit = json.dumps(
            [[r.action, r.after, r.before] for r in s.scalars(select(AuditLog))], default=str
        )
        stored = json.dumps([r.masked_tail for r in s.scalars(select(ConnectionRow))])
    for blob in (caplog.text, audit, *bodies):
        assert SECRET not in blob
    assert SECRET not in stored.replace(SECRET[-4:], "")


# --- access: FDE only, tenant and project isolation -------------------------------------------


def routes(h: Harness) -> list[tuple[str, str, dict[str, Any]]]:
    wf = f"/projects/{h.project}/workflows/{WORKFLOW}/connections"
    return [
        ("GET", base(h), {}),
        ("GET", f"{base(h)}/{LEDGER}", {}),
        ("PUT", f"{base(h)}/{LEDGER}/value", {"json": {"value": SECRET}}),
        ("DELETE", f"{base(h)}/{LEDGER}/value", {}),
        ("PUT", f"{base(h)}/{LEDGER}", {"json": {"purpose": "x"}}),
        ("POST", f"{base(h)}/{LEDGER}/test", {}),
        ("GET", f"{wf}/readiness", {}),
        ("GET", f"{wf}/env-example", {}),
    ]


def test_wg16_admins_have_no_access_to_any_route(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    put_value(h)
    for method, path, kw in routes(h):
        problem(h.call(method, path, h.admin_a(), **kw), 403, "WRONG_PORTAL")
    problem(h.call("GET", base(h), h.admin_b()), 403, "WRONG_PORTAL")


def test_wg16_no_token_is_401(h: Harness) -> None:
    for method, path, kw in routes(h):
        res = h.client.request(method, f"/api/v1{path}", **kw)
        assert res.status_code == 401, (method, path)


def test_wg16_idor_another_tenant_an_unassigned_fde_and_another_project_all_get_404(
    h: Harness,
) -> None:
    seed_workflow(h, LEDGER)
    put_value(h)
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    unassigned = h.realm.token(FDE_A2)
    for token in (other_tenant, unassigned):
        for method, path, kw in routes(h):
            res = h.call(method, path, token, **kw)
            problem(res, 404, "NOT_FOUND")
            assert SECRET not in res.text
    random = h.call("GET", f"/projects/{uuid.uuid4()}/connections", h.fde())
    foreign = h.call("GET", base(h), other_tenant)
    assert (random.status_code, random.json()["code"]) == (
        foreign.status_code,
        foreign.json()["code"],
    )
    assert h.call("GET", base(h), h.fde()).json()["connections"][0]["status"] == "SET"  # untouched


def test_wg16_a_denied_write_stores_and_audits_nothing(h: Harness) -> None:
    seed_workflow(h, LEDGER)
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other_tenant, h.admin_a()):
        put_value(h, token=token)
        h.call("PUT", f"{base(h)}/{LEDGER}", token, json={"purpose": "x"})
    with h.sessions() as s:
        assert list(s.scalars(select(ConnectionRow))) == []
        assert (
            list(s.scalars(select(AuditLog).where(AuditLog.entity_type == "project_connection")))
            == []
        )


def test_wg16_the_expected_outcome_helper_matches_the_contract_enum() -> None:
    check: Callable[[int], ConnectionTestOutcome] = ssrf.outcome_of
    assert check(401) is ConnectionTestOutcome.UNAUTHORIZED
