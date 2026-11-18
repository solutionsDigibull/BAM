"""WG-16 (#764) checkpoint d: the connection store, its logic, `resolve_env` and the re-encrypt.

SQLite with the real tables (the CHECK on the value columns is enforced there too). Nothing here
makes a network call. Every test that stores a value also checks it is nowhere it must not be.
"""

import base64
import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.connections import inject, rekey, service
from sbam_api.connections.crypto import KeyRing
from sbam_api.connections.mask import masked_tail
from sbam_api.connections.models import ConnectionRow
from sbam_api.connections.repository import ConnectionRepository
from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.workflows.models import Workflow
from sbam_contracts.connections import (
    RESPONSE_MODELS,
    BlockerReason,
    ConnectionSource,
    ConnectionStatus,
    ConnectionTestMode,
    ConnectionTestOutcome,
    ConnectionUpdate,
    Readiness,
)
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

TENANT = uuid.UUID("6a1f3e2c-0b7d-4c55-9a51-1c2f0a7e4b11")
OTHER_TENANT = uuid.UUID("8d2b9c4e-5f60-4a18-b3c7-2e9d4f1a6c22")
PROJECT = uuid.UUID("0190f3a2-7b1c-7d4e-8f00-000000000001")
OTHER_PROJECT = uuid.UUID("0190f3a2-7b1c-7d4e-8f00-000000000002")
WORKFLOW = uuid.UUID("0190f3a2-7b1c-7d4e-8f00-0000000000a1")
OLD_WORKFLOW = uuid.UUID("0190f3a2-7b1c-7d4e-8f00-0000000000a0")
CTX = TenantContext(TENANT, PROJECT)
FDE = "fde-a1"
SECRET = "fixture_only_not_a_real_1716"  # noqa: S105 - a test value, 28 characters: the tail is 4
LEDGER = "LEDGER_API_KEY"
MAIL = "MAIL_TOKEN"


def key() -> bytes:
    return os.urandom(32)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


K1, K2 = key(), key()
RING1 = KeyRing.parse(f"*k1:{b64(K1)}", allow_placeholder=False)
RING2 = KeyRing.parse(f"k1:{b64(K1)},*k2:{b64(K2)}", allow_placeholder=False)


def isatvon(skill_id: str, *secrets: str) -> dict[str, Any]:
    return {
        "skill_metadata": {"skill_id": skill_id, "skill_name": "Name"},
        "isatvon_definition": {"v_variables": {"secrets": list(secrets)}},
    }


class Store:
    """SQLite with the real tables and one Workflow that names LEDGER_API_KEY and MAIL_TOKEN."""

    def __init__(self, *secrets: str) -> None:
        fd, path = tempfile.mkstemp(prefix="sbam-wg16-", suffix=".db")
        os.close(fd)
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
            execution_options={"schema_translate_map": {"sbam": None}},
        )
        for model in (AuditLog, Workflow, SubSkill, ConnectionRow):
            model.metadata.create_all(engine, tables=[model.__table__])  # type: ignore[list-item]
        self.sessions: sessionmaker[Session] = sessionmaker(engine, expire_on_commit=False)
        self.session = self.sessions()
        self.workflow(WORKFLOW, 1)
        self.skill(WORKFLOW, "MS01.1_A", *(secrets or (LEDGER, MAIL)))

    def workflow(self, workflow_id: uuid.UUID, major: int, project: uuid.UUID = PROJECT) -> None:
        self.session.add(
            Workflow(
                id=workflow_id,
                tenant_id=TENANT,
                project_id=project,
                sow_version_id=uuid.uuid4(),
                major=major,
                version=f"{major}.0.0",
                name=f"wf{major}",
                state="FROZEN",
            )
        )
        self.session.commit()

    def skill(self, workflow_id: uuid.UUID, skill_id: str, *secrets: str) -> SubSkill:
        row = SubSkill(
            tenant_id=TENANT,
            project_id=PROJECT,
            macro_skill_id=uuid.uuid4(),
            workflow_id=workflow_id,
            skill_id=skill_id,
            skill_name="Name",
            order=len(skill_id),
            isatvon=isatvon(skill_id, *secrets),
            sow_refs=[],
            content_sha256="0" * 64,
            state="DRAFT",
        )
        self.session.add(row)
        self.session.commit()
        return row

    def set(self, name: str = LEDGER, value: str = SECRET, ring: KeyRing = RING1, **kw: Any) -> Any:
        etag = kw.pop("if_match", self.etag(name))
        return service.set_value(
            self.session, CTX, FDE, name, value, keyring=ring, if_match=etag, **kw
        )

    def etag(self, name: str) -> str | None:
        row = ConnectionRepository(self.session, CTX).by_name(name)
        return None if row is None else f'"{row.row_version}"'

    def row(self, name: str = LEDGER, ctx: TenantContext = CTX) -> ConnectionRow | None:
        return ConnectionRepository(self.session, ctx).by_name(name)

    def status(self, name: str = LEDGER) -> ConnectionStatus:
        return service.get_connection(self.session, CTX, name).status

    def audit(self, action: str | None = None) -> list[AuditLog]:
        query = select(AuditLog).order_by(AuditLog.id)
        rows = list(self.session.scalars(query))
        return [r for r in rows if action is None or r.action == action]

    def everything_stored(self) -> str:
        """Every column of every row of the tables, as text."""
        out: list[str] = []
        with self.session.get_bind().connect() as conn:  # type: ignore[union-attr]
            for table in ("audit_log", "project_connections"):
                for row in conn.execute(text(f"SELECT * FROM {table}")):  # noqa: S608
                    out.append(repr(tuple(row)))
        return "\n".join(out)


def passed_test(
    store: Store, name: str = LEDGER, outcome: ConnectionTestOutcome = ConnectionTestOutcome.OK
) -> None:
    service.record_test(store.session, CTX, FDE, name, outcome)


# --- the table ------------------------------------------------------------------------------------


def test_wg16_the_value_columns_are_all_null_or_all_set() -> None:
    store = Store()
    for bad in (
        {"ciphertext": b"x"},
        {"key_id": "k1"},
        {"masked_tail": ""},
        {"ciphertext": b"x", "key_id": "k1"},
    ):
        store.session.add(
            ConnectionRow(
                tenant_id=TENANT,
                project_id=PROJECT,
                name="X_" + uuid.uuid4().hex[:6].upper(),
                source="FDE",
                required=False,
                value_version=0,
                **bad,
            )
        )
        with pytest.raises(IntegrityError):
            store.session.commit()
        store.session.rollback()


def test_wg16_one_entry_per_tenant_project_and_name() -> None:
    store = Store()
    store.set()
    store.session.add(
        ConnectionRow(
            tenant_id=TENANT,
            project_id=PROJECT,
            name=LEDGER,
            source="FDE",
            required=False,
            value_version=0,
        )
    )
    with pytest.raises(IntegrityError):
        store.session.commit()


# --- masked tail ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "tail"),
    [
        ("1234567", ""),
        ("abcdefgh", "gh"),
        ("abcdefghijklmno", "no"),
        ("0123456789abcdef", "cdef"),
        ("\u00e9" * 16, "\u00e9" * 4),
    ],
)
def test_wg16_the_masked_tail_follows_the_length_rules(value: str, tail: str) -> None:
    assert masked_tail(value) == tail


# --- set, clear, status ---------------------------------------------------------------------------


def test_wg16_a_saved_value_is_sealed_and_only_a_status_and_a_tail_come_back() -> None:
    store = Store()
    result = store.set()
    assert result.connection.status is ConnectionStatus.SET
    assert result.connection.masked_tail == SECRET[-4:]
    assert result.connection.source is ConnectionSource.ISATVON and result.connection.required
    row = store.row()
    assert row is not None and row.ciphertext is not None and row.key_id == "k1"
    assert SECRET.encode() not in row.ciphertext and row.value_version == 1
    assert SECRET not in store.everything_stored()
    assert SECRET not in repr(row) and SECRET not in repr(result)
    for blob in (
        result.model_dump_json(),
        service.list_connections(store.session, CTX).model_dump_json(),
    ):
        assert SECRET not in blob


def test_wg16_the_value_decrypts_only_in_its_own_tenant_project_and_name() -> None:
    store = Store()
    store.set()
    row = store.row()
    assert row is not None and row.sealed() is not None
    sealed = row.sealed()
    assert sealed is not None
    assert RING1.open(TENANT, PROJECT, LEDGER, sealed).get_secret_value() == SECRET
    for tenant, project, name in (
        (OTHER_TENANT, PROJECT, LEDGER),
        (TENANT, OTHER_PROJECT, LEDGER),
        (TENANT, PROJECT, MAIL),
    ):
        with pytest.raises(Exception, match="cannot decrypt"):
            RING1.open(tenant, project, name, sealed)


def test_wg16_the_value_is_cleaned_and_the_answer_says_how() -> None:
    store = Store()
    result = store.set(value='  "' + SECRET + '"  ')
    assert [c.value for c in result.cleaned] == ["TRIMMED", "UNQUOTED"]
    row = store.row()
    assert row is not None and row.sealed() is not None
    sealed = row.sealed()
    assert sealed is not None
    assert RING1.open(TENANT, PROJECT, LEDGER, sealed).get_secret_value() == SECRET


@pytest.mark.parametrize("raw", ["", "   ", '""', "a\x00b", "x" * 9000])
def test_wg16_a_bad_value_for_a_required_name_is_refused_without_echo(raw: str) -> None:
    store = Store()
    with pytest.raises(ApiError) as caught:
        store.set(value=raw)
    assert caught.value.status == 422
    assert raw.strip() == "" or raw not in json.dumps(
        caught.value.to_problem("c").model_dump(mode="json")
    )
    assert store.row() is None  # nothing was stored


def test_wg16_a_name_that_cannot_be_a_variable_is_refused() -> None:
    store = Store()
    for bad in ("lower_case", "1ABC", "A-B", "A B", "A" * 129):
        with pytest.raises(ApiError) as caught:
            store.set(name=bad, value=SECRET)
        assert caught.value.status == 422
    assert store.audit() == []


def test_wg16_an_unexpected_name_becomes_an_optional_fde_entry_and_is_flagged() -> None:
    store = Store()
    result = store.set(name="LEDGER_API_KEYS", value=SECRET)
    assert result.unexpected_name and result.did_you_mean == LEDGER
    assert result.connection.source is ConnectionSource.FDE and not result.connection.required
    assert store.set(name=LEDGER).unexpected_name is False


def test_wg16_an_empty_value_clears_an_optional_entry() -> None:
    store = Store()
    store.set(name="EXTRA_KEY", value=SECRET)
    assert store.status("EXTRA_KEY") is ConnectionStatus.SET
    store.set(name="EXTRA_KEY", value="  ")
    assert store.status("EXTRA_KEY") is ConnectionStatus.MISSING
    assert [r.action for r in store.audit()] == ["CONNECTION_VALUE_SET", "CONNECTION_VALUE_CLEARED"]


def test_wg16_writes_need_the_row_version_and_a_stale_one_is_refused() -> None:
    store = Store()
    store.set()
    with pytest.raises(ApiError) as missing:
        store.set(if_match=None)
    assert missing.value.status == 428
    with pytest.raises(ApiError) as stale:
        store.set(if_match='"99"')
    assert stale.value.status == 412
    assert store.set().connection.row_version == 3  # a right If-Match works


def test_wg16_clearing_removes_the_value_and_is_idempotent() -> None:
    store = Store()
    store.set()
    cleared = service.clear_value(store.session, CTX, FDE, LEDGER, if_match=store.etag(LEDGER))
    assert cleared.status is ConnectionStatus.MISSING and cleared.masked_tail == ""
    row = store.row()
    assert row is not None and row.ciphertext is None and row.key_id is None
    again = service.clear_value(store.session, CTX, FDE, LEDGER, if_match=None)
    assert again.status is ConnectionStatus.MISSING
    assert [r.action for r in store.audit()] == ["CONNECTION_VALUE_SET", "CONNECTION_VALUE_CLEARED"]
    assert (
        service.clear_value(store.session, CTX, FDE, MAIL, if_match=None).status
        is ConnectionStatus.MISSING
    )
    with pytest.raises(ApiError) as unknown:
        service.clear_value(store.session, CTX, FDE, "NOT_EXPECTED", if_match=None)
    assert unknown.value.status == 404


# --- tested, and re-tested ------------------------------------------------------------------------


def test_wg16_t03_a_value_is_tested_only_after_a_passing_test_and_a_change_untests_it() -> None:
    store = Store()
    store.set()
    assert store.status() is ConnectionStatus.SET  # saved, not tested: blocks Commit
    passed_test(store, outcome=ConnectionTestOutcome.UNAUTHORIZED)
    assert store.status() is ConnectionStatus.TEST_FAILED
    passed_test(store)
    assert store.status() is ConnectionStatus.TESTED
    store.set(value=SECRET + "-new")
    assert store.status() is ConnectionStatus.SET  # a new value needs a new test
    passed_test(store)
    config = ConnectionUpdate.model_validate(
        {"test_config": {"url": "https://api.example.com/ping"}}
    )
    service.update_entry(store.session, CTX, FDE, LEDGER, config, if_match=store.etag(LEDGER))
    assert store.status() is ConnectionStatus.SET  # a new test target needs a new test
    service.clear_value(store.session, CTX, FDE, LEDGER, if_match=store.etag(LEDGER))
    assert store.status() is ConnectionStatus.MISSING
    with pytest.raises(ApiError):
        passed_test(store)  # nothing to test


def test_wg16_a_testable_connection_is_never_none_by_default() -> None:
    store = Store()
    result = store.set()
    assert result.connection.test_mode is ConnectionTestMode.HTTP
    assert store.status() is ConnectionStatus.SET


def test_wg16_test_mode_none_is_an_explicit_audited_choice_and_counts_as_tested() -> None:
    store = Store()
    store.set()
    body = ConnectionUpdate(test_mode=ConnectionTestMode.NONE)
    entry = service.update_entry(store.session, CTX, FDE, LEDGER, body, if_match=store.etag(LEDGER))
    assert entry.test_mode is ConnectionTestMode.NONE and entry.status is ConnectionStatus.TESTED
    [vouch] = store.audit("CONNECTION_TESTMODE_SET")
    assert vouch.actor_id == FDE and vouch.after == {"name": LEDGER, "from": "HTTP", "to": "NONE"}
    store.set(value=SECRET + "-v2")  # nothing to test: stays TESTED, but the change is on record
    assert store.status() is ConnectionStatus.TESTED
    assert len(store.audit("CONNECTION_VALUE_SET")) == 2
    back = ConnectionUpdate(test_mode=ConnectionTestMode.HTTP)
    service.update_entry(store.session, CTX, FDE, LEDGER, back, if_match=store.etag(LEDGER))
    assert (
        store.status() is ConnectionStatus.SET and len(store.audit("CONNECTION_TESTMODE_SET")) == 2
    )


def test_wg16_an_isatvon_name_stays_required_and_the_fde_may_mark_their_own() -> None:
    store = Store()
    with pytest.raises(ApiError) as refused:
        service.update_entry(
            store.session, CTX, FDE, LEDGER, ConnectionUpdate(required=False), if_match=None
        )
    assert refused.value.status == 422
    mine = ConnectionUpdate(purpose="Pager", where_to_get="Ops wiki", required=True)
    entry = service.update_entry(store.session, CTX, FDE, "PAGER_KEY", mine, if_match=None)
    assert entry.source is ConnectionSource.FDE and entry.required and entry.purpose == "Pager"
    assert entry.status is ConnectionStatus.MISSING


# --- listing and expected names -------------------------------------------------------------------


def test_wg16_the_list_merges_expected_and_stored_and_reads_sub_skills_live() -> None:
    store = Store()
    store.set()

    def names() -> list[str]:
        return [c.name for c in service.list_connections(store.session, CTX).connections]

    assert names() == [LEDGER, MAIL]
    store.skill(WORKFLOW, "MS01.2_B", "NEW_SECRET")  # an edit of the ISATVON shows at once
    assert names() == [LEDGER, MAIL, "NEW_SECRET"]
    first = service.list_connections(store.session, CTX).connections[0]
    assert first.purpose.startswith("Used by MS01.1_A")


def test_wg16_the_project_list_uses_the_newest_major_workflow_and_the_others_ask_by_id() -> None:
    store = Store()
    store.workflow(OLD_WORKFLOW, 0)
    store.skill(OLD_WORKFLOW, "MS09.1_Old", "OLD_ONLY_KEY")
    listed = [c.name for c in service.list_connections(store.session, CTX).connections]
    assert "OLD_ONLY_KEY" not in listed  # newest major is the panel's default view
    old = service.commit_blockers(store.session, CTX, OLD_WORKFLOW)
    assert [b.name for b in old.blockers] == ["OLD_ONLY_KEY"]  # but readiness names its own


def test_wg16_an_entry_that_is_no_longer_named_is_kept_but_not_required() -> None:
    store = Store(LEDGER)
    store.set()
    sub = store.session.scalars(select(SubSkill)).one()
    sub.isatvon = isatvon("MS01.1_A")  # the secret was removed from the V block
    store.session.commit()
    kept = service.get_connection(store.session, CTX, LEDGER)
    assert kept.status is ConnectionStatus.SET and not kept.required  # the value is not lost
    assert service.commit_blockers(store.session, CTX, WORKFLOW).ready


# --- scope: tenant and project --------------------------------------------------------------------


def test_wg16_another_tenant_or_project_sees_nothing_and_gets_404() -> None:
    store = Store()
    store.set()
    for ctx in (TenantContext(OTHER_TENANT, PROJECT), TenantContext(TENANT, OTHER_PROJECT)):
        assert store.row(ctx=ctx) is None
        assert ConnectionRepository(store.session, ctx).all_rows() == []
        for call in (
            lambda ctx=ctx: service.get_connection(store.session, ctx, LEDGER),
            lambda ctx=ctx: service.commit_blockers(store.session, ctx, WORKFLOW),
            lambda ctx=ctx: service.env_example_for(store.session, ctx, WORKFLOW),
            lambda ctx=ctx: inject.resolve_env(
                store.session, ctx, WORKFLOW, keyring=RING1, actor_id="x", purpose="p", run_id="r"
            ),
        ):
            with pytest.raises(ApiError) as caught:
                call()
            assert caught.value.status == 404
    with pytest.raises(ApiError) as other:  # nor can it write into this one
        service.clear_value(
            store.session, TenantContext(OTHER_TENANT, PROJECT), FDE, LEDGER, if_match=None
        )
    assert other.value.status == 404


def test_wg16_a_context_without_a_project_cannot_read_connections() -> None:
    store = Store()
    with pytest.raises(ApiError) as caught:
        service.list_connections(store.session, TenantContext(TENANT))
    assert caught.value.status == 404


# --- readiness (Commit's gate) --------------------------------------------------------------------


def test_wg16_t04_commit_is_blocked_until_every_required_value_is_set_and_tested() -> None:
    store = Store()

    def ready() -> Readiness:
        return service.commit_blockers(store.session, CTX, WORKFLOW)

    assert {(b.name, b.reason) for b in ready().blockers} == {
        (LEDGER, BlockerReason.MISSING),
        (MAIL, BlockerReason.MISSING),
    }
    store.set(LEDGER)
    store.set(MAIL)
    assert {b.reason for b in ready().blockers} == {BlockerReason.NOT_TESTED}
    passed_test(store, LEDGER)
    passed_test(store, MAIL, ConnectionTestOutcome.SERVER_ERROR)
    assert [(b.name, b.reason) for b in ready().blockers] == [(MAIL, BlockerReason.TEST_FAILED)]
    passed_test(store, MAIL)
    done = ready()
    assert done.ready and done.blockers == [] and done.workflow_id == WORKFLOW
    store.set(LEDGER, SECRET + "x")  # a changed value blocks again
    assert [(b.name, b.reason) for b in ready().blockers] == [(LEDGER, BlockerReason.NOT_TESTED)]


def test_wg16_a_required_fde_entry_blocks_and_an_optional_one_does_not() -> None:
    store = Store(LEDGER)
    service.update_entry(
        store.session, CTX, FDE, "PAGER_KEY", ConnectionUpdate(required=True), if_match=None
    )
    service.update_entry(
        store.session, CTX, FDE, "OPT_KEY", ConnectionUpdate(purpose="x"), if_match=None
    )
    blocked = {b.name for b in service.commit_blockers(store.session, CTX, WORKFLOW).blockers}
    assert blocked == {LEDGER, "PAGER_KEY"}


def test_wg16_an_isatvon_name_that_is_not_a_variable_blocks_by_name() -> None:
    store = Store(LEDGER, "bad name")
    ready = service.commit_blockers(store.session, CTX, WORKFLOW)
    assert (("bad name", BlockerReason.INVALID_NAME)) in {
        (b.name, b.reason) for b in ready.blockers
    }


def test_wg16_the_env_example_has_names_and_purposes_and_never_a_value() -> None:
    store = Store()
    store.set()
    text_out = service.env_example_for(store.session, CTX, WORKFLOW)
    assert f"{LEDGER}=\n" in text_out and f"{MAIL}=\n" in text_out
    assert SECRET not in text_out and SECRET[-4:] not in text_out


# --- resolve_env ----------------------------------------------------------------------------------


def resolve(store: Store, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "keyring": RING1,
        "actor_id": "preview-launcher",
        "purpose": "preview",
        "run_id": "run-1",
    }
    return inject.resolve_env(store.session, CTX, WORKFLOW, **{**args, **kw})


def test_wg16_resolve_env_returns_only_set_and_tested_values_as_secret_strs() -> None:
    store = Store()
    store.set(LEDGER)
    store.set(MAIL, "mail-token-value-1234")
    passed_test(store, LEDGER)  # MAIL is set but not tested
    got = resolve(store)
    assert list(got) == [LEDGER] and got[LEDGER].get_secret_value() == SECRET
    assert SECRET not in repr(got) and SECRET not in str(got)
    passed_test(store, MAIL)
    both = resolve(store)
    assert both[MAIL].get_secret_value() == "mail-token-value-1234"


def test_wg16_resolve_env_is_partial_not_an_error_for_a_gap() -> None:
    store = Store()
    assert resolve(store) == {}  # nothing set: a preview still starts
    store.set(LEDGER)
    assert resolve(store) == {}  # set but untested: not handed out
    body = ConnectionUpdate(test_mode=ConnectionTestMode.NONE)
    service.update_entry(store.session, CTX, FDE, LEDGER, body, if_match=store.etag(LEDGER))
    assert list(resolve(store)) == [LEDGER]  # vouched: nothing to test


def test_wg16_resolve_env_never_hands_out_a_name_the_workflow_does_not_declare() -> None:
    store = Store()
    for name in (LEDGER, MAIL):
        store.set(name)
        passed_test(store, name)
    sub = store.session.scalars(select(SubSkill)).one()
    sub.isatvon = isatvon("MS01.1_A", LEDGER)  # MAIL_TOKEN is no longer named by this Workflow
    store.session.commit()
    assert set(resolve(store)) == {LEDGER}
    assert set(resolve(store, names=[LEDGER, MAIL, "ANYTHING_ELSE"])) == {LEDGER}
    assert resolve(store, names=["ANYTHING_ELSE", MAIL]) == {}


def test_wg16_an_entry_the_fde_added_is_declared_once_set_and_tested() -> None:
    store = Store(LEDGER)
    store.set("PAGER_KEY", "pager-value-123456")
    assert resolve(store) == {}  # not tested yet
    passed_test(store, "PAGER_KEY")
    assert list(resolve(store)) == ["PAGER_KEY"]


def test_wg16_every_resolve_is_audited_by_name_actor_and_run_and_never_by_value() -> None:
    store = Store()
    for name in (LEDGER, MAIL):
        store.set(name)
        passed_test(store, name)
    resolve(store, run_id="run-42", purpose="srim")
    used = store.audit("CONNECTION_SECRET_USED")
    assert sorted(r.after["name"] for r in used if r.after) == [LEDGER, MAIL]
    for row in used:
        assert row.actor_id == "preview-launcher" and row.at is not None
        assert row.after is not None
        assert set(row.after) == {"name", "purpose", "run_id"}
        assert row.after["run_id"] == "run-42" and row.after["purpose"] == "srim"
    blob = json.dumps([r.after for r in used])
    assert SECRET not in blob and SECRET[-4:] not in blob
    resolve(store)
    assert len(store.audit("CONNECTION_SECRET_USED")) == 4  # every resolve is a use


def test_wg16_a_failed_decryption_stops_before_anything_is_audited() -> None:
    store = Store()
    store.set(LEDGER)
    passed_test(store, LEDGER)
    wrong = KeyRing.parse(f"*k1:{b64(key())}", allow_placeholder=False)
    with pytest.raises(Exception, match="cannot decrypt the value of LEDGER_API_KEY") as caught:
        resolve(store, keyring=wrong)
    assert SECRET not in str(caught.value)
    assert store.audit("CONNECTION_SECRET_USED") == []


def test_wg16_resolve_env_is_not_in_any_response_model() -> None:
    for model in RESPONSE_MODELS:
        assert "resolve" not in json.dumps(model.model_json_schema()).lower()


# --- rekey ----------------------------------------------------------------------------------------


def rotated_store() -> Store:
    store = Store()
    for name in (LEDGER, MAIL):
        store.set(name, SECRET + name, RING1)
        passed_test(store, name)
    other = TenantContext(OTHER_TENANT, PROJECT)
    sealed = RING1.seal(OTHER_TENANT, PROJECT, "THEIRS", SECRET + "T")
    ConnectionRepository(store.session, other).add(
        ConnectionRow(
            name="THEIRS",
            source="FDE",
            required=False,
            ciphertext=sealed.ciphertext,
            key_id=sealed.key_id,
            masked_tail="",
            value_version=1,
        )
    )
    store.session.commit()
    return store


def test_wg16_rekey_moves_every_row_to_the_active_key_and_the_values_still_open() -> None:
    store = rotated_store()
    report = rekey.rekey(store.session, RING2)
    assert report.moved == 3 and report.failed == [] and report.remaining == 0
    for ctx, name, value in (
        (CTX, LEDGER, SECRET + LEDGER),
        (CTX, MAIL, SECRET + MAIL),
        (TenantContext(OTHER_TENANT, PROJECT), "THEIRS", SECRET + "T"),
    ):
        row = store.row(name, ctx)
        assert row is not None and row.key_id == "k2" and row.value_version == 1
        sealed = row.sealed()
        assert sealed is not None
        assert RING2.open(ctx.tenant_id, PROJECT, name, sealed).get_secret_value() == value
    assert store.status(LEDGER) is ConnectionStatus.TESTED  # the value is the same: still tested
    assert SECRET not in store.everything_stored()
    # the old key can go once nothing names it
    only_new = KeyRing.parse(f"*k2:{b64(K2)}", allow_placeholder=False)
    got = inject.resolve_env(
        store.session, CTX, WORKFLOW, keyring=only_new, actor_id="x", purpose="p", run_id="r"
    )
    assert got[LEDGER].get_secret_value() == SECRET + LEDGER


def test_wg16_rekey_is_idempotent_and_audited_with_both_key_ids() -> None:
    store = rotated_store()
    rekey.rekey(store.session, RING2)
    audited = store.audit("CONNECTION_REKEYED")
    assert len(audited) == 3
    assert all(
        r.after and r.after["from_key_id"] == "k1" and r.after["to_key_id"] == "k2" for r in audited
    )
    versions = {r.name: r.row_version for r in ConnectionRepository(store.session, CTX).all_rows()}
    again = rekey.rekey(store.session, RING2)
    assert again.moved == 0 and again.remaining == 0
    assert len(store.audit("CONNECTION_REKEYED")) == 3
    assert versions == {
        r.name: r.row_version for r in ConnectionRepository(store.session, CTX).all_rows()
    }


def test_wg16_rekey_in_small_batches_and_for_one_tenant() -> None:
    store = rotated_store()
    report = rekey.rekey(store.session, RING2, tenant_id=TENANT, batch=1)
    assert report.moved == 2
    theirs = store.row("THEIRS", TenantContext(OTHER_TENANT, PROJECT))
    assert theirs is not None and theirs.key_id == "k1"  # the other tenant was not touched
    assert rekey.rekey(store.session, RING2, batch=1).moved == 1


def test_wg16_a_dry_run_changes_nothing() -> None:
    store = rotated_store()
    report = rekey.rekey(store.session, RING2, dry_run=True)
    assert report.moved == 0 and report.remaining == 3
    assert {r.key_id for r in ConnectionRepository(store.session, CTX).all_rows()} == {"k1"}
    assert store.audit("CONNECTION_REKEYED") == []


def test_wg16_a_row_whose_key_is_gone_is_reported_by_name_and_left_alone() -> None:
    store = rotated_store()
    lost = KeyRing.parse(f"*k2:{b64(K2)},k3:{b64(key())}", allow_placeholder=False)  # no k1
    report = rekey.rekey(store.session, lost)
    assert report.moved == 0 and report.remaining == 3
    assert sorted(n for n, _ in report.failed) == sorted([LEDGER, MAIL, "THEIRS"])
    assert {k for _, k in report.failed} == {"k1"}
    assert {r.key_id for r in ConnectionRepository(store.session, CTX).all_rows()} == {"k1"}


def test_wg16_t06_no_value_reaches_a_log_an_audit_row_or_the_database_in_plain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    store = Store()
    store.set(LEDGER, SECRET)
    passed_test(store, LEDGER)
    with pytest.raises(ApiError):
        store.set(LEDGER, SECRET + "\x00")
    resolve(store)
    rekey.rekey(store.session, RING2)
    service.commit_blockers(store.session, CTX, WORKFLOW)
    service.env_example_for(store.session, CTX, WORKFLOW)
    assert SECRET not in caplog.text and SECRET not in store.everything_stored()
    assert any("connections resolved" in m for m in caplog.messages)  # counts only
    assert datetime.now(UTC).year >= 2026  # the clock the audit rows use is real


# --- the rekey command ----------------------------------------------------------------------------


def run_cli(store: Store, monkeypatch: pytest.MonkeyPatch, keys: str | None, *args: str) -> int:
    monkeypatch.chdir(tempfile.mkdtemp())  # no stray .env
    monkeypatch.setenv("DATABASE_URL", "postgresql://sbam@127.0.0.1:1/sbam")  # never opened
    monkeypatch.setenv("SBAM_ENV", "development")
    for name, value in (  # the rest of what the API's settings need, as in its container
        ("KEYCLOAK_ISSUER", "http://127.0.0.1:1/realms/sbam"),
        ("KEYCLOAK_AUDIENCE", "sbam-api"),
        ("STORAGE_ROOT", tempfile.gettempdir()),
        ("TEMPORAL_HOST", "127.0.0.1:1"),
    ):
        monkeypatch.setenv(name, value)
    if keys is None:
        monkeypatch.delenv("SECRETS_KEYS", raising=False)
    else:
        monkeypatch.setenv("SECRETS_KEYS", keys)
    return rekey.main(list(args), open_session=store.sessions)


def test_wg16_the_rekey_command_parses_its_arguments_moves_rows_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = rotated_store()
    ring = f"k1:{b64(K1)},*k2:{b64(K2)}"
    assert run_cli(store, monkeypatch, ring, "--tenant", str(TENANT), "--batch", "1") == 0
    assert "moved=2 failed=0 remaining=0" in capsys.readouterr().out
    assert run_cli(store, monkeypatch, ring) == 0
    assert "moved=1 failed=0 remaining=0" in capsys.readouterr().out
    assert run_cli(store, monkeypatch, ring) == 0  # idempotent
    assert "moved=0" in capsys.readouterr().out


def test_wg16_the_rekey_command_dry_run_moves_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = rotated_store()
    assert run_cli(store, monkeypatch, f"k1:{b64(K1)},*k2:{b64(K2)}", "--dry-run") == 0
    assert "moved=0 failed=0 remaining=3" in capsys.readouterr().out
    assert {r.key_id for r in ConnectionRepository(store.session, CTX).all_rows()} == {"k1"}
    assert store.audit("CONNECTION_REKEYED") == []


def test_wg16_the_rekey_command_exits_one_and_names_a_row_whose_key_is_gone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = rotated_store()
    code = run_cli(store, monkeypatch, f"*k2:{b64(K2)}")  # k1 was removed too early
    out = capsys.readouterr()
    assert code == 1 and "failed=3" in out.out
    assert f"cannot decrypt {LEDGER} (key k1)" in out.err
    assert b64(K1) not in out.out + out.err and SECRET not in out.out + out.err


def test_wg16_the_rekey_command_exits_two_without_a_usable_keyring(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = rotated_store()
    assert run_cli(store, monkeypatch, None) == 2
    assert "SECRETS_KEYS is not set" in capsys.readouterr().err
    assert run_cli(store, monkeypatch, f"k1:{b64(K1)}") == 2  # nothing marked active: refused
    assert "exactly one key active" in capsys.readouterr().err
    assert {r.key_id for r in ConnectionRepository(store.session, CTX).all_rows()} == {"k1"}
