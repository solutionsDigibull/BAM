"""Bulk master rows (#782): `POST /admin/masters/{master_type}/bulk`.

The client has already mapped its file onto one master's 0.3 payload keys (Add files, Import
agents, Skills import). Each row is checked on its own and reported by its 1-based number:

* a row the type's model refuses, a malformed id, or an id used twice in the request: an error;
* a blank id: the next server-assigned id (`service.next_id`), several per request without clashes;
* an id that exists with the same values: `skipped_exact`;
* an id that exists with other values: a conflict, never written. Unlike the workbook import, which
  adds a version, the bulk path only adds rows; a change goes through a new version.

`validate-only` writes nothing. `upsert` writes the clean new rows in one transaction, each audited
like a single create (MASTER_ENTRY_CREATED); errors and conflicts don't block the clean rows.
"""

import uuid
from typing import Any

from pydantic import ValidationError
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_v03 import (
    ID_KEY,
    PAYLOAD_MODELS,
    BulkConflict,
    BulkRowError,
    MasterBulkReport,
    MasterBulkRequest,
    stable_id,
)
from sbam_contracts.masters_workbook import (
    ID_HEADER,
    ID_LABEL,
    MasterRow,
    row_identity,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser
from sbam_api.db.repository import StaleRowVersion
from sbam_api.masters.importer import _create, _keep_non_columns
from sbam_api.masters.service import (
    _active_project,
    _editable,
    _repo,
    next_id,
    version_conflict,
    with_id,
)


def bulk_rows(
    session: Session,
    user: CurrentUser,
    master_type: MasterType,
    body: MasterBulkRequest,
    project_id: uuid.UUID | None,
) -> MasterBulkReport:
    _editable(master_type)
    if project_id is not None:
        _active_project(session, user, project_id)
    repo = _repo(session, user)
    existing = {
        e.stable_id: e
        for e in repo.of_type(master_type.value, project_id)
        if e.project_id == project_id
    }
    current = repo.current_versions(list(existing.values()))
    write = body.mode == "upsert"
    key = ID_KEY[master_type]
    errors: list[BulkRowError] = []
    conflicts: list[BulkConflict] = []
    skipped = 0
    clean: list[tuple[int, Any]] = []  # (row number, validated model) of the new rows
    seen: set[str] = set()

    for number, raw in enumerate(body.rows, start=1):
        try:
            model = PAYLOAD_MODELS[master_type].model_validate(raw)
        except ValidationError as exc:
            errors.extend(
                BulkRowError(
                    row=number,
                    field=".".join(str(p) for p in e["loc"]) or None,
                    code="VALIDATION_ERROR",
                    message=e["msg"],
                )
                for e in exc.errors()
            )
            continue
        sid = stable_id(master_type, model)
        if sid is None:
            clean.append((number, model))  # the id is assigned below
            continue
        label = str(getattr(model, key)).strip()
        if not ID_LABEL.fullmatch(label):
            message = f"{ID_HEADER[master_type]} must be one line of at most 64 characters"
            errors.append(BulkRowError(row=number, field=key, code="INVALID_ID", message=message))
            continue
        if sid in seen:
            message = f"{sid} appears more than once in this request"
            errors.append(BulkRowError(row=number, field=key, code="DUPLICATE_ID", message=message))
            continue
        seen.add(sid)
        entry = existing.get(sid)
        if entry is None:
            clean.append((number, model))
            continue
        incoming = model.model_dump(mode="json", by_alias=True)
        stored = current[entry.id].payload if entry.id in current else {}
        incoming = _keep_non_columns(master_type, stored, incoming)
        keys = (incoming.keys() | stored.keys()) - {key}  # the id matched already, case aside
        differ = sorted(k for k in keys if incoming.get(k) != stored.get(k))
        if differ:
            conflicts.append(BulkConflict(row=number, stable_id=sid, fields=differ))
        else:
            skipped += 1

    counters: dict[str, int] = {}
    created: list[str] = []
    try:
        for number, model in clean:
            payload = model.model_dump(mode="json", by_alias=True)
            if stable_id(master_type, model) is None:
                new_id = next_id(repo, master_type, model, counters, seen)
                payload = with_id(master_type, payload, new_id)
                model = PAYLOAD_MODELS[master_type].model_validate(payload)
            label = str(getattr(model, key)).strip()
            identity = row_identity(master_type, label, model)
            created.append(identity.stable_id)
            if write:
                row = MasterRow(
                    master_type=master_type,
                    row_label=label,
                    sheet_row=number,
                    payload=model,
                    identity=identity,
                )
                _create(session, user, repo, row, payload, project_id)
        if write:
            session.commit()
    except (IntegrityError, StaleRowVersion):  # another writer took an id first
        session.rollback()
        raise version_conflict() from None
    return MasterBulkReport(
        master_type=master_type,
        mode=body.mode,
        applied=write,
        created=created,
        skipped_exact=skipped,
        conflicts=conflicts,
        errors=errors,
    )
