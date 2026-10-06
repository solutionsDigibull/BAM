"""E0.7 unit tests for the framework-free API convention helpers."""

import pytest
from sbam_api.core.conventions import (
    IDEMPOTENCY_TTL_SECONDS,
    ApiError,
    IdempotencyScope,
    InMemoryIdempotencyStore,
    body_hash,
    check_if_match,
    decode_cursor,
    encode_cursor,
    etag,
    not_found,
    parse_limit,
    parse_sort,
    replay_run_id,
    validate_idempotency_key,
)
from sbam_contracts.api import ErrorCode

SECRET = b"test-cursor-secret"


def problem_of(fn: object, *args: object, **kwargs: object) -> ApiError:
    with pytest.raises(ApiError) as info:
        fn(*args, **kwargs)  # type: ignore[operator]
    return info.value


# --- If-Match -------------------------------------------------------------------------------


def test_e0_7_t02_missing_if_match_is_428() -> None:
    p = problem_of(check_if_match, None, 4)
    assert (p.status, p.code) == (428, ErrorCode.PRECONDITION_REQUIRED)


def test_e0_7_t03_stale_if_match_is_412() -> None:
    p = problem_of(check_if_match, '"3"', 4)
    assert (p.status, p.code) == (412, ErrorCode.PRECONDITION_FAILED)


def test_e0_7_t03_current_if_match_passes() -> None:
    check_if_match(etag(4), 4)
    check_if_match('W/"4"', 4)


def test_e0_7_frozen_is_423_even_with_correct_if_match() -> None:
    p = problem_of(check_if_match, etag(4), 4, frozen=True)
    assert (p.status, p.code) == (423, ErrorCode.RESOURCE_LOCKED)
    assert p.detail and "frozen / approved — create a new version" in p.detail


# --- Idempotency ----------------------------------------------------------------------------

SCOPE = IdempotencyScope(tenant_id="t1", user_id="u1", endpoint="POST W/generate")


def test_e0_7_t04_same_key_same_body_replays_the_same_run() -> None:
    store = InMemoryIdempotencyStore()
    body = {"sub_skill_ids": ["MS01.1"]}
    assert replay_run_id(store, SCOPE, "key-0001", body) is None  # first call: start the run
    store.put(SCOPE, "key-0001", body_hash(body), "run-42")
    assert replay_run_id(store, SCOPE, "key-0001", {"sub_skill_ids": ["MS01.1"]}) == "run-42"


def test_e0_7_t05_same_key_different_body_is_422() -> None:
    store = InMemoryIdempotencyStore()
    store.put(SCOPE, "key-0001", body_hash({"a": 1}), "run-42")
    p = problem_of(replay_run_id, store, SCOPE, "key-0001", {"a": 2})
    assert (p.status, p.code) == (422, ErrorCode.IDEMPOTENCY_KEY_REUSED)


def test_e0_7_idempotency_key_is_scoped_and_expires() -> None:
    clock = [0.0]
    store = InMemoryIdempotencyStore(now=lambda: clock[0])
    store.put(SCOPE, "key-0001", body_hash({}), "run-1")
    other_user = IdempotencyScope("t1", "u2", SCOPE.endpoint)
    assert replay_run_id(store, other_user, "key-0001", {}) is None
    clock[0] = IDEMPOTENCY_TTL_SECONDS + 1
    assert replay_run_id(store, SCOPE, "key-0001", {}) is None


@pytest.mark.parametrize(
    "key,ok",
    [
        ("0190f3a2-7b1c-7d4e-8f00-123456789abc", True),
        ("abc_DEF-1", True),
        ("x" * 64, True),
        ("short", False),
        ("x" * 65, False),
        ("has space!", False),
        (None, False),
    ],
)
def test_e0_7_idempotency_key_format(key: str | None, ok: bool) -> None:
    if ok:
        assert validate_idempotency_key(key) == key
    else:
        assert problem_of(validate_idempotency_key, key).status == 400


# --- Pagination -----------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [("1", 1), ("100", 100), (None, 25), ("", 25), (50, 50)])
def test_e0_7_t06_limit_in_bounds(raw: str | int | None, expected: int) -> None:
    assert parse_limit(raw) == expected


@pytest.mark.parametrize("raw", ["0", "101", "-1", "x", "1.5", 0, 101])
def test_e0_7_t06_limit_out_of_bounds_is_400_on_limit(raw: str | int) -> None:
    p = problem_of(parse_limit, raw)
    assert (p.status, p.code) == (400, ErrorCode.VALIDATION_ERROR)
    assert p.errors[0].path == "limit"


def test_e0_7_sort_allowlist_and_direction() -> None:
    allowed = frozenset({"created_at", "name"})
    assert parse_sort("-created_at", allowed) == ("created_at", True)
    assert parse_sort("name", allowed) == ("name", False)
    assert parse_sort(None, allowed) is None
    assert problem_of(parse_sort, "password", allowed).errors[0].path == "sort"


# --- Cursors ----------------------------------------------------------------------------------


def test_e0_7_cursor_round_trip() -> None:
    token = encode_cursor({"after": "2026-10-06T00:00:00Z", "id": "p1"}, "tenant-a", SECRET)
    assert decode_cursor(token, "tenant-a", SECRET) == {"after": "2026-10-06T00:00:00Z", "id": "p1"}


@pytest.mark.parametrize("tamper", ["edit-payload", "edit-mac", "garbage", "other-tenant"])
def test_e0_7_t08_forged_cursor_is_400(tamper: str) -> None:
    token = encode_cursor({"id": "p1"}, "tenant-a", SECRET)
    payload, mac = token.split(".")
    forged, tenant = {
        "edit-payload": (
            encode_cursor({"id": "p9"}, "tenant-a", b"x").split(".")[0] + "." + mac,
            "tenant-a",
        ),
        "edit-mac": (payload + "." + mac[:-2] + "AA", "tenant-a"),
        "garbage": ("not-a-cursor", "tenant-a"),
        "other-tenant": (token, "tenant-b"),
    }[tamper]
    p = problem_of(decode_cursor, forged, tenant, SECRET)
    assert (p.status, p.code) == (400, ErrorCode.INVALID_CURSOR)


# --- 404 without existence leak --------------------------------------------------------------


def test_e0_7_not_found_is_identical_for_every_cause() -> None:
    missing = not_found().to_problem("c1").model_dump(exclude={"correlation_id"})
    other_tenant = not_found().to_problem("c2").model_dump(exclude={"correlation_id"})
    assert missing == other_tenant
    assert missing["status"] == 404 and missing["code"] == ErrorCode.NOT_FOUND
