"""Store and logic for project connections (ADR 0030, #764 checkpoint d): list, set, clear, update,
record a test, and the Commit readiness. No routes yet (checkpoint e), no outbound call.

Rules held here:

- **Write-only.** Nothing in this module returns a stored value; the contract models it builds have
  no field that could hold one. A value is sealed (AES-256-GCM, AAD `tenant:project:name`) as
  soon as it is cleaned and is only decrypted by `inject.resolve_env` and `rekey`.
- **Scope.** Every query is tenant and project scoped by the repository; another tenant's or
  project's entry is a 404. `ctx` must name the project.
- **Expected names are live**: the union of the Workflow's current Sub-Skills' V-block secrets, read
  on every call. The project list uses the project's newest MAJOR Workflow.
- **Audit** rows hold the name, action, key id and status, never the value and never the tail.
- A value change (set or clear) makes the entry untested; so does a change of `test_config`. An FDE
  who marks an entry `test_mode = NONE` (audited, explicit) has it count as tested while a value
  is set.
"""

import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import SecretStr
from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.connections import (
    MAX_BLOCKERS,
    Blocker,
    BlockerReason,
    CleanStep,
    Connection,
    ConnectionList,
    ConnectionSource,
    ConnectionStatus,
    ConnectionTestConfig,
    ConnectionTestMode,
    ConnectionTestOutcome,
    ConnectionTestResult,
    ConnectionUpdate,
    ConnectionValueResult,
    Readiness,
)
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.connections.clean import ValueRejected, clean_value
from sbam_api.connections.crypto import KeyRing
from sbam_api.connections.env_example import EnvEntry, env_example
from sbam_api.connections.expected import ExpectedScan, expected_connections
from sbam_api.connections.mask import masked_tail
from sbam_api.connections.models import ConnectionRow
from sbam_api.connections.names import NameCheck, check_name, is_valid_name
from sbam_api.connections.repository import ConnectionRepository
from sbam_api.connections.ssrf import ProbeResult, UnsendableValue
from sbam_api.core.conventions import ApiError, check_if_match, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.workflows.repository import WorkflowRepository

ENTITY = "project_connection"
VALUE_SET = "CONNECTION_VALUE_SET"
VALUE_CLEARED = "CONNECTION_VALUE_CLEARED"
UPDATED = "CONNECTION_UPDATED"
TESTED = "CONNECTION_TESTED"
TESTMODE_SET = "CONNECTION_TESTMODE_SET"  # the FDE's explicit, audited choice of NONE or HTTP


def _invalid(path: str, message: str) -> ApiError:
    """422 with a field path and a fixed message, never the input."""
    return ApiError(
        422,
        ErrorCode.VALIDATION_ERROR,
        "Invalid request",
        errors=[FieldError(path=path, message=message)],
    )


def project_of(ctx: TenantContext) -> uuid.UUID:
    if ctx.project_id is None:
        raise not_found()
    return ctx.project_id


# --- what is expected -----------------------------------------------------------------------------


def expected_for_workflow(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID
) -> ExpectedScan:
    """Read live from the Workflow's current (not deleted) Sub-Skills. 404 for a Workflow that is
    not in this tenant's project."""
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    skills = SubSkillRepository(session, ctx).of_workflow(workflow_id)
    return expected_connections(skill.isatvon for skill in skills)


def expected_for_project(session: Session, ctx: TenantContext) -> ExpectedScan:
    """The project's newest MAJOR Workflow; nothing expected while it has none."""
    workflows = WorkflowRepository(session, ctx).of_project()
    if not workflows:
        return ExpectedScan((), ())
    return expected_for_workflow(session, ctx, workflows[0].id)


# --- a row as the API shows it --------------------------------------------------------------------


def status_of(row: ConnectionRow | None) -> ConnectionStatus:
    if row is None or not row.has_value:
        return ConnectionStatus.MISSING
    if row.test_mode == ConnectionTestMode.NONE.value:
        return ConnectionStatus.TESTED  # the FDE vouched there is no service to test
    if row.tested_value_version == row.value_version and row.last_test_code is not None:
        ok = row.last_test_code == ConnectionTestOutcome.OK.value
        return ConnectionStatus.TESTED if ok else ConnectionStatus.TEST_FAILED
    return ConnectionStatus.SET


def view(row: ConnectionRow | None, name: str, scan: ExpectedScan) -> Connection:
    """The contract model of one entry: no value, only the status and the masked tail."""
    expected = next((e for e in scan.names if e.name == name), None)
    if expected is not None:
        source, required = ConnectionSource.ISATVON, True
    elif row is not None:
        source = ConnectionSource(row.source)
        required = row.required and source is not ConnectionSource.ISATVON  # no longer named
    else:
        source, required = ConnectionSource.FDE, False
    purpose = (row.purpose if row is not None else "") or (expected.purpose if expected else "")
    config = None
    if row is not None and row.test_config is not None:
        config = ConnectionTestConfig.model_validate(row.test_config)
    return Connection(
        name=name,
        source=source,
        purpose=purpose,
        where_to_get=row.where_to_get if row is not None else "",
        required=required,
        status=status_of(row),
        masked_tail=(row.masked_tail or "") if row is not None else "",
        test_mode=ConnectionTestMode(row.test_mode) if row is not None else ConnectionTestMode.HTTP,
        test_config=config,
        value_set_at=row.value_set_at if row is not None else None,
        value_set_by=row.value_set_by if row is not None else None,
        last_test_at=row.last_test_at if row is not None else None,
        last_test_outcome=(
            ConnectionTestOutcome(row.last_test_code)
            if row is not None and row.last_test_code
            else None
        ),
        row_version=row.row_version if row is not None else 0,
    )


def list_connections(session: Session, ctx: TenantContext) -> ConnectionList:
    """Expected names and stored entries, merged, by name. Names only, no values."""
    project_id = project_of(ctx)
    scan = expected_for_project(session, ctx)
    rows = {row.name: row for row in ConnectionRepository(session, ctx).all_rows()}
    names = sorted({e.name for e in scan.names} | set(rows))
    entries = [view(rows.get(n), n, scan) for n in names][:MAX_BLOCKERS]
    return ConnectionList(project_id=project_id, connections=entries)


def get_connection(session: Session, ctx: TenantContext, name: str) -> Connection:
    """One entry; 404 for a name that is neither expected nor stored."""
    scan = expected_for_project(session, ctx)
    row = ConnectionRepository(session, ctx).by_name(name)
    if row is None and name not in {e.name for e in scan.names}:
        raise not_found()
    return view(row, name, scan)


# --- writes ---------------------------------------------------------------------------------------


def _audit(
    session: Session,
    ctx: TenantContext,
    actor_id: str,
    action: str,
    row: ConnectionRow,
    after: dict[str, object],
) -> None:
    """Name, key id and status; never the value and never the tail."""
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=actor_id,
        action=action,
        entity_type=ENTITY,
        entity_id=str(row.id),
        after={"name": row.name, **after},
        correlation_id=correlation_id_var.get() or "unknown",
    )


def _find(
    session: Session, ctx: TenantContext, name: str, scan: ExpectedScan
) -> tuple[ConnectionRow | None, bool]:
    """The row to write to (locked, or None), and whether the Workflow expects the name."""
    if not is_valid_name(name):
        raise _invalid("name", "not a valid environment variable name")
    row = ConnectionRepository(session, ctx).by_name(name, for_update=True)
    return row, name in {e.name for e in scan.names}


def _create(session: Session, ctx: TenantContext, name: str, *, expected: bool) -> ConnectionRow:
    """A new row: an ISATVON entry (required) or an FDE entry (optional until marked)."""
    source = ConnectionSource.ISATVON if expected else ConnectionSource.FDE
    row = ConnectionRow(name=name, source=source.value, required=expected, value_version=0)
    return ConnectionRepository(session, ctx).add(row)


def _untested(row: ConnectionRow) -> None:
    row.tested_value_version = None
    row.last_test_code = None
    row.last_test_at = None


def set_value(
    session: Session,
    ctx: TenantContext,
    actor_id: str,
    name: str,
    raw: SecretStr | str,
    *,
    keyring: KeyRing,
    if_match: str | None,
) -> ConnectionValueResult:
    """Clean, seal and store a value. An entry that exists needs the `If-Match` of its row version;
    a first value creates the row. An empty value is refused for a required name and clears an
    optional one. The answer says which clean-ups ran and flags an unexpected name."""
    scan = expected_for_project(session, ctx)
    row, expected = _find(session, ctx, name, scan)
    required = expected or (row is not None and row.required)
    try:
        cleaned = clean_value(raw, required=required)
    except ValueRejected as rejected:
        raise _invalid("value", f"rejected: {rejected.code.value}") from None
    if cleaned.empty:  # only an optional name gets here
        return _result(clear_value(session, ctx, actor_id, name, if_match=if_match), (), None)
    if row is None:
        row = _create(session, ctx, name, expected=expected)
    else:
        check_if_match(if_match, row.row_version)
    text = cleaned.value.get_secret_value()
    sealed = keyring.seal(ctx.tenant_id, project_of(ctx), name, cleaned.value)
    row.ciphertext = sealed.ciphertext
    row.key_id = sealed.key_id
    row.masked_tail = masked_tail(text)
    row.value_version += 1
    row.value_set_at = datetime.now(UTC)
    row.value_set_by = actor_id
    _untested(row)
    ConnectionRepository(session, ctx).update(row, row.row_version)
    _audit(
        session,
        ctx,
        actor_id,
        VALUE_SET,
        row,
        {
            "key_id": sealed.key_id,
            "value_version": row.value_version,
            "cleaned": [step.value for step in cleaned.steps],
        },
    )
    session.commit()
    check = check_name(name, [e.name for e in scan.names])
    return _result(view(row, name, scan), cleaned.steps, check)


def _result(
    connection: Connection, steps: Collection[CleanStep], check: NameCheck | None
) -> ConnectionValueResult:
    return ConnectionValueResult(
        connection=connection,
        cleaned=list(steps),
        unexpected_name=check.unexpected if check else False,
        did_you_mean=check.did_you_mean if check else None,
    )


def clear_value(
    session: Session, ctx: TenantContext, actor_id: str, name: str, *, if_match: str | None
) -> Connection:
    """Remove the stored value. Idempotent when there is none. 404 for an unknown name."""
    scan = expected_for_project(session, ctx)
    row, expected = _find(session, ctx, name, scan)
    if row is None:
        if not expected:
            raise not_found()
        return view(None, name, scan)
    if not row.has_value:
        return view(row, name, scan)
    check_if_match(if_match, row.row_version)
    previous = row.key_id
    row.ciphertext = row.key_id = row.masked_tail = None
    row.value_version += 1
    row.value_set_at = row.value_set_by = None
    _untested(row)
    ConnectionRepository(session, ctx).update(row, row.row_version)
    _audit(session, ctx, actor_id, VALUE_CLEARED, row, {"key_id": previous})
    session.commit()
    return view(row, name, scan)


def update_entry(
    session: Session,
    ctx: TenantContext,
    actor_id: str,
    name: str,
    body: ConnectionUpdate,
    *,
    if_match: str | None,
) -> Connection:
    """An entry's purpose, where to get it, required flag, test mode and test config. A field left
    out is kept. An ISATVON name stays required. A change of `test_config` makes the value untested;
    setting `test_mode = NONE` is audited as the FDE's explicit statement that there is nothing to
    test."""
    scan = expected_for_project(session, ctx)
    row, expected = _find(session, ctx, name, scan)
    if body.required is False and expected:
        raise _invalid("required", "a name from the ISATVON is always required")
    if row is None:
        row = _create(session, ctx, name, expected=expected)
    else:
        check_if_match(if_match, row.row_version)
    changed: dict[str, object] = {}
    for field in ("purpose", "where_to_get"):
        new = getattr(body, field)
        if new is not None and new != getattr(row, field):
            setattr(row, field, new)
            changed[field] = True
    if body.required is not None and body.required != row.required and not expected:
        row.required = body.required
        changed["required"] = body.required
    if body.test_config is not None:
        config = body.test_config.model_dump(mode="json")
        if config != row.test_config:
            row.test_config = config
            _untested(row)
            changed["test_config"] = True
    mode_change: tuple[str, str] | None = None
    if body.test_mode is not None and body.test_mode.value != row.test_mode:
        mode_change = (row.test_mode, body.test_mode.value)
        row.test_mode = body.test_mode.value
        _untested(row)
    if changed or mode_change:
        ConnectionRepository(session, ctx).update(row, row.row_version)
        if changed:
            _audit(session, ctx, actor_id, UPDATED, row, {"changed": changed})
        if mode_change:  # a separate row: skipping the test is a statement made on the record
            _audit(
                session,
                ctx,
                actor_id,
                TESTMODE_SET,
                row,
                {"from": mode_change[0], "to": mode_change[1]},
            )
    session.commit()
    return view(row, name, scan)


def record_test(
    session: Session,
    ctx: TenantContext,
    actor_id: str,
    name: str,
    outcome: ConnectionTestOutcome,
    *,
    at: datetime | None = None,
    value_version: int | None = None,
) -> Connection:
    """Store the result of a Test connection against the value version that was tested. Only `OK`
    makes the entry TESTED. `value_version`: the version the call used; if the value changed while
    the call was out, the result is dropped (it says nothing about the new value)."""
    scan = expected_for_project(session, ctx)
    row = ConnectionRepository(session, ctx).by_name(name, for_update=True)
    if row is None or not row.has_value:
        raise not_found()
    if value_version is not None and row.value_version != value_version:
        return view(row, name, scan)
    row.last_test_code = outcome.value
    row.last_test_at = at or datetime.now(UTC)
    row.tested_value_version = row.value_version
    ConnectionRepository(session, ctx).update(row, row.row_version)
    _audit(session, ctx, actor_id, TESTED, row, {"outcome": outcome.value})
    session.commit()
    return view(row, name, scan)


# --- Commit's gate and the .env.example ---------------------------------------------------------

_REASON = {
    ConnectionStatus.MISSING: BlockerReason.MISSING,
    ConnectionStatus.SET: BlockerReason.NOT_TESTED,
    ConnectionStatus.TEST_FAILED: BlockerReason.TEST_FAILED,
}


@dataclass(frozen=True)
class _Entry:
    name: str
    required: bool
    purpose: str
    where_to_get: str


def commit_blockers(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> Readiness:
    """Every required name that has no value, whose value is not tested, or whose last test failed,
    and every name the ISATVON gives that cannot be a variable. Required = named in the Workflow's
    Sub-Skills, or an FDE entry marked required. The Commit gate (#766) consumes this; a secret
    change needs no recertification."""
    scan = expected_for_workflow(session, ctx, workflow_id)
    rows = {row.name: row for row in ConnectionRepository(session, ctx).all_rows()}
    entries = [view(rows.get(n), n, scan) for n in sorted({e.name for e in scan.names} | set(rows))]
    blockers = [Blocker(name=n, reason=BlockerReason.INVALID_NAME) for n in scan.invalid]
    for entry in entries:
        reason = _REASON.get(entry.status)
        if entry.required and reason is not None:
            blockers.append(Blocker(name=entry.name, reason=reason))
    return Readiness(workflow_id=workflow_id, ready=not blockers, blockers=blockers[:MAX_BLOCKERS])


def env_example_for(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> str:
    """`.env.example` of the Workflow: names, purposes and where to get them, never a value."""
    scan = expected_for_workflow(session, ctx, workflow_id)
    rows = {row.name: row for row in ConnectionRepository(session, ctx).all_rows()}
    entries = [
        EnvEntry(
            c.name,
            c.purpose,
            c.where_to_get,
            c.required,
        )
        for c in (
            view(rows.get(n), n, scan) for n in sorted({e.name for e in scan.names} | set(rows))
        )
    ]
    return env_example(entries)


# --- Test connection -----------------------------------------------------------------------------


def test_connection(
    session: Session,
    ctx: TenantContext,
    actor_id: str,
    name: str,
    *,
    keyring: KeyRing,
    probe: Callable[[ConnectionTestConfig, str], ProbeResult],
) -> ConnectionTestResult:
    """Decrypt the value, test it against the FDE's `test_config` through `probe` (the SSRF-guarded
    call), and store the outcome against the value version tested. 404: no value. 409: the FDE said
    there is nothing to test. 422: no test target yet, or a value that cannot travel in a header.
    The answer holds the outcome and a fixed message: never the value, the URL or the response."""
    row = ConnectionRepository(session, ctx).by_name(name)
    sealed = None if row is None else row.sealed()
    if row is None or sealed is None:
        raise not_found()
    if row.test_mode == ConnectionTestMode.NONE.value:
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "Nothing to test",
            "This connection is marked as having no service to test.",
        )
    if row.test_config is None:
        raise _invalid("test_config", "set the test target first")
    config = ConnectionTestConfig.model_validate(row.test_config)
    version = row.value_version
    value = keyring.open(ctx.tenant_id, project_of(ctx), name, sealed)
    session.rollback()  # no transaction is held open while the call is out
    try:
        result = probe(config, value.get_secret_value())
    except UnsendableValue:
        raise _invalid("value", "cannot be sent in a header") from None
    at = datetime.now(UTC)
    entry = record_test(session, ctx, actor_id, name, result.outcome, at=at, value_version=version)
    return ConnectionTestResult(
        name=name,
        outcome=result.outcome,
        message=result.message,
        http_status_class=result.http_status_class,
        tested_at=at,
        connection=entry,
    )
